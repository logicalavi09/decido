# Resilient Workflow Decision Platform

A configurable, auditable decision engine for business workflows. Workflows, stages, rules, thresholds, branching,
dependencies and failure policies are defined in YAML; the Python engine stays generic. The same runtime executes
application approvals, insurance claims, employee onboarding, vendor onboarding and document verification.

Built for the ScoreMe hackathon assignment. The design prioritises **problem structuring**, **architecture quality**
and **engineering robustness** (the three highest-weight criteria). Beyond the execution core it includes practical
governance tooling: a tamper-evident audit chain, actionable counterfactuals, configuration linting, ETag-guarded
dry-run reloads and shadow impact analysis of candidate rule changes.

---

## Why this shape?

The problem is not "write an approval endpoint". It is "build a platform that survives requirement changes, failures
and duplicate traffic while explaining every decision". The implementation therefore separates:

```
HTTP API  ->  orchestration  ->  pure domain logic  ->  persistence / connectors
```

- **Domain logic is pure.** Rule evaluation, schema validation and the state machine have no I/O and no framework
  imports. They are cheap to test and impossible to "accidentally retry" or write twice.
- **Configuration is data, not code.** Adding a workflow or changing a threshold is a YAML edit plus a validated
  reload. Existing runs are pinned to the definition snapshot they started with.
- **Every write has one transactional boundary.** A stage's state change and its audit events commit together,
  or not at all. Partial saves cannot corrupt history.
- **External dependencies are behind an adapter seam.** The demo adapter is deterministic and fault-injectable;
  a real integration only replaces the adapter, not retries, audit or decision logic.
- **Idempotency and optimistic locking are first-class.** Duplicate submissions replay the original result;
  concurrent writers fail loudly instead of silently overwriting.

---

## Quick start

Requirements: Python 3.11+ (tested on 3.13).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# run the API (creates data/workflow.db on first start)
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Web console: <http://localhost:8000>
OpenAPI/Swagger UI: <http://localhost:8000/docs>

Or with Make:

```bash
make install
make run
```

Run the tests:

```bash
make test
# or with coverage
make test-cov
```

Run the scripted demo (server must be running):

```bash
./examples/demo.sh
```

---

## Web console

`web/` is a build-free, same-origin operations console served by FastAPI. It uses
vanilla ES modules, self-hosted fonts and the live API — no Node build step, CDN or
separate frontend server.

- **Overview** — decision wire, outcome mix, approval rate and workflow library.
- **Decisions** — searchable ledger; open any run for decision evidence, rule traces,
  counterfactuals, manual-review actions and the verified audit stream.
- **Workflows** — inspect stages, dependencies, conditions, transitions, snapshot hashes
  and lint findings without reading raw YAML.
- **Change lab** — run lint, preview an atomic reload with an ETag diff, then shadow-test
  candidate rule changes against real inputs.
- **Demo ledger** — one click seeds approved, manual-review, rejected and failed outcomes
  so the audit and impact views are immediately explorable.
- **Keyboard** — `⌘K` / `Ctrl+K` opens the command palette; the console also supports
  reduced motion, visible focus, responsive rail navigation and dismissible toasts.

The API mounts `web/` at `/` only when the directory exists, so the JSON API and
`/docs` remain unchanged.

---

## Core concepts

| Concept | Meaning |
| --- | --- |
| `WorkflowConfig` | Named, versioned workflow with an input schema, ordered stages and retry policy |
| `StageConfig` | One decision step: optional dependency call, rules, transitions |
| `Condition` | Declarative condition tree (`all`, `any`, `not`, or a leaf `field/op/value`) |
| `Rule` | A check with `assert`/`guard` mode and an effect (`reject`, `manual_review`, `retry`, `continue`, `branch`) |
| `Run` | One submitted request with status, current stage, context, retry state and pinned workflow snapshot |
| `RunEvent` | Append-only audit record (`request_received`, `rule_evaluated`, `dependent_attempt`, ...) |

### Outcomes

| Outcome | Meaning |
| --- | --- |
| `approved` | Success; all required stages passed |
| `rejected` | Business rejection by a rule or dependency policy |
| `manual_review` | A human decision is required; resolve via API |
| `retry_scheduled` | Transient failure exhausted inline attempts; run resumes automatically or via API |
| `failed` | Terminal technical/retry-budget failure |

### Rule semantics

- `mode: assert` (default): the condition **must be true**; if false, the effect fires.
- `mode: guard`: if the condition **is true**, the effect fires (used for escalations and fast-track branches).
- The first deciding rule wins. All evaluated rules are still written to the trace.
- `effect: branch` jumps to the rule's `goto` stage and stops evaluating that stage.
- `effect: continue` is a non-deciding informational check.

Rule values may reference request or dependency data, e.g. `value: "$input.vendor.country"`.

---

## API

Base URL: `http://localhost:8000`

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness plus config/DB check |
| `GET` | `/workflows` | List loaded workflow definitions |
| `GET` | `/workflows/{name}` | Full definition + snapshot hash + lint findings |
| `GET` | `/workflows/{name}/lint` | Static config analysis (duplicates, unknown fields, graph issues) |
| `POST` | `/workflows/{name}/impact` | Shadow-evaluate a candidate config against historical or supplied inputs |
| `POST` | `/workflows/reload` | Atomically reload YAML; supports `dry_run` and `If-Match` ETag guard |
| `POST` | `/runs` | Submit a request (idempotent) |
| `GET` | `/runs` | List runs (`status`, `workflow`, `limit`) |
| `GET` | `/runs/{id}` | Current run state |
| `GET` | `/runs/{id}/audit` | Ordered immutable audit events + input snapshot + chain integrity |
| `GET` | `/runs/{id}/audit/verify` | Recompute the SHA-256 audit chain and report the first broken event |
| `GET` | `/runs/{id}/explanation` | Decision, triggered rules, traces, counterfactuals and audit reasoning |
| `POST` | `/runs/{id}/resume` | Resume a retry-scheduled/running run |
| `POST` | `/runs/{id}/resolve` | Approve/reject a manual review |

### Submit a request

```bash
curl -s -X POST http://localhost:8000/runs \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: app-2026-0001' \
  -d '{
    "workflow": "application_approval",
    "input": {
      "applicant": {
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "credit_score": 720,
        "monthly_income": 4000,
        "employment_status": "employed"
      },
      "requested_amount": 15000,
      "country": "US"
    }
  }'
```

`201 Created` for a new run, `200 OK` plus `Idempotency-Replayed: true` for a replay of the same key and payload.
Reusing a key with a different payload returns `409 idempotency_conflict`. Without a header, an `auto:<payload-hash>`
key is used, so identical submissions are naturally de-duplicated.

### Inspect the decision

```bash
curl -s http://localhost:8000/runs/<run-id>/explanation
```

The response includes the input, decision stage/rule/reason, every evaluated rule with captured field values,
dependency attempts, counterfactuals for deciding rules, per-stage summaries and the full audit event stream.

### Verify the audit trail

Every event is linked to its predecessor with a SHA-256 hash (`prev_hash`, `event_hash`). The audit response includes
an `integrity` block; the verify endpoint returns the exit condition and the first broken sequence number:

```bash
curl -s http://localhost:8000/runs/<run-id>/audit/verify
# {"valid": true, "algorithm": "sha256-chain", "event_count": 17,
#  "head_hash": "...", "broken_at_seq": null, "reason": null, ...}
```

Editing a stored payload, deleting a non-tail event, or reordering events makes verification fail at the affected
sequence. Truncating the newest events is detected by comparing the stream with the persisted anchor (event count +
head hash) in the run row. An attacker with full database write access could rewrite both, so production should also
anchor the head outside the primary store.

### Turn a rejection into a counterfactual

`/explanation` contains a top-level `counterfactuals` list plus one per triggered rule trace. Numeric conditions report
the field, actual value, expected threshold, absolute gap and a plain-language action:

```json
{
  "field": "applicant.credit_score",
  "operator": "gte",
  "actual": 640,
  "expected": 650,
  "required": ">= 650",
  "gap": 10,
  "action": "increase to at least 650 to satisfy the rule",
  "mode": "assert"
}
```

Guards describe how to avoid triggering an escalation, e.g. `decrease to at most 25000 to avoid triggering this rule`.

### Lint, preview and diff configuration

```bash
curl -s http://localhost:8000/workflows/application_approval/lint
curl -s -X POST 'http://localhost:8000/workflows/reload?dry_run=true' \
  -H 'If-Match: <config_etag>'
```

The linter flags duplicate rule ids, references to unknown schema fields or dependency connectors, unreachable stages,
transition cycles, always-pass/always-fail thresholds and high-severity no-op rules. A dry-run validates and diffs the
candidate configuration without activating it. The response includes `active_etag`, `candidate_etag`, per-workflow
stage/rule changes and lint totals. Pass the active `config_etag` in `If-Match` to prevent overwriting a configuration
that changed since you inspected it (`409 conflict` on mismatch).

### Shadow impact analysis

Before promoting a rule change, replay it against real historical inputs without writing runs or events:

```bash
curl -s -X POST http://localhost:8000/workflows/application_approval/impact \
  -H 'Content-Type: application/json' \
  -d '{
    "candidate": { "...full candidate workflow snapshot...": "..." },
    "sample_size": 100,
    "include_inputs": false
  }'
```

The response reports the active-vs-candidate outcome transition matrix, changed percentage, outcome counts, schema
failures, up to ten changed examples with stage/rule reasons, and lint findings for the candidate. Supply an explicit
`inputs` array to evaluate specific cases, or omit it to use the most recent submitted inputs for that workflow.
Candidate validation is in-memory only; nothing is activated or persisted.

### Simulate failures and retries

Simulation is a demo/test-only envelope; it is not business input and is excluded from the idempotency fingerprint.

```bash
curl -s -X POST http://localhost:8000/runs \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: dep-failure-demo' \
  -d '{
    "workflow": "application_approval",
    "input": { "...": "..." },
    "simulation": {"faults": {"credit_bureau": "error"}}
  }'
```

Fault modes: `error`, `unavailable`, `timeout`, `fail_until_attempt:<n>`, `flaky:<rate>`.
Other controls: `responses` (override dependency output), `latency_ms`, and `database_fault: commit_failure`
(proves transactional rollback and recoverability).

```bash
# resume after clearing the simulated fault
curl -s -X POST http://localhost:8000/runs/<run-id>/resume \
  -H 'Content-Type: application/json' \
  -d '{"simulation": {"faults": {}}}'
```

### Resolve a manual review

```bash
curl -s -X POST http://localhost:8000/runs/<run-id>/resolve \
  -H 'Content-Type: application/json' \
  -d '{"decision":"approve","reviewer":"credit.officer@example.com","comment":"Income verified"}'
```

---

## Configuration model

Workflows live in `workflows/*.yaml`. A representative definition:

```yaml
name: application_approval
version: "1.0.0"
input_schema:
  type: object
  required: [applicant, requested_amount, country]
  properties:
    applicant:
      type: object
      required: [name, email, credit_score]
      properties:
        credit_score: {type: integer, minimum: 300, maximum: 850}
    requested_amount: {type: number, minimum: 100}
retry:
  workflow_backoff_seconds: 2
  max_workflow_attempts: 4
stages:
  - name: credit_check
    dependency:
      connector: credit_bureau
      operation: score
      timeout_ms: 250
      retry: {max_attempts: 3, base_delay_ms: 5, max_delay_ms: 40}
      failure_policy: retry
      result:
        verified: true
        bureau_score: "$input.applicant.credit_score"
    rules:
      - id: minimum_credit_score
        severity: high
        mode: assert
        condition: {field: applicant.credit_score, op: gte, value: 650}
        effect: reject
        reason: Credit score is below the 650 minimum
  - name: affordability
    rules:
      - id: high_value_review
        mode: guard
        condition: {field: requested_amount, op: gt, value: 25000}
        effect: manual_review
        reason: Applications above 25,000 require a human credit decision
```

- Supported operators: `eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `in`, `not_in`, `contains`, `not_contains`,
  `exists`, `not_exists`, `matches`, `between`, `len_gte`, `len_lte`, `len_eq`.
- Supported schema keywords: `type` (including unions), `required`, `properties`, `items`, `additionalProperties`,
  `enum`, `const`, `minimum`, `maximum`, `minLength`, `maxLength`, `pattern`, `minItems`, `maxItems`.
- `POST /workflows/reload` validates every file before swapping the registry. An invalid file leaves the previous
  configuration untouched.
- Each run stores its workflow version, snapshot hash and full definition snapshot. Resumes use the pinned snapshot,
  so an in-flight decision never changes because someone edited YAML mid-flight.

Five ready-made workflows demonstrate genericity:

1. `application_approval` – thresholds, dependency, fast-track branch, manual escalation
2. `claim_processing` – document verification with `manual_review` degradation policy
3. `employee_onboarding` – consent, background check, role-based review
4. `vendor_approval` – tax registry dependency and contract thresholds
5. `document_verification` – OCR quality thresholds and retry-on-unusable-response

See [CONFIGURATION.md](CONFIGURATION.md) for the full configuration reference.

---

## Failure handling and consistency

| Concern | Implementation |
| --- | --- |
| External dependency failure | Adapter returns typed failure; inline exponential-backoff retries; configurable `failure_policy` |
| Timeout | Per-dependency `timeout_ms`; simulated and enforced in the adapter seam |
| Partial save failure | Stage state + audit events are committed in one SQLite transaction; failure rolls back everything |
| Duplicate requests | Unique idempotency key + request fingerprint; replay or `409` |
| Concurrent writers | Optimistic version check on every run mutation; stale writes raise `409 concurrent_modification` |
| Retry / recovery | Workflow-level `retry_scheduled` with backoff, retry budget, in-process scheduler + manual resume |
| Crash mid-stage | Run remains at its last committed stage; resume re-enters safely (events are append-only) |
| Manual review | Explicit state; approve/reject resolution is audited and can only happen once |

---

## Tests

`pytest` covers 88 unit and scenario tests:

- happy path across all five configured workflows
- fast-track branch routing and manual-review resolution
- invalid payloads, malformed envelopes, unknown workflows/statuses
- duplicate requests, automatic idempotency and key/payload conflicts
- dependency error, timeout, degraded-to-manual-review policy and transient retry success
- rule-triggered retry, retry-budget exhaustion, scheduler tick, illegal resume
- rule changes through config reload, atomic rollback of invalid config, version pinning
- audit/explanation completeness, field-level rule evidence, state-machine invariants
- partial-save rollback and optimistic-concurrency rejection
- SHA-256 audit-chain verification and tamper detection
- counterfactual generation for threshold and guard rules
- config lint, dry-run diff, ETag conflict handling and candidate impact matrices
- shadow evaluation of branches, retries, schema-invalid candidates and terminal outcomes

```bash
make test-cov
```

> Line coverage: 89% across the application (run `make test-cov` for the current report).

---

## Project layout

```
app/
  api/            FastAPI routes + request schemas
  core/           settings, clock, typed errors
  domain/         models, rule engine, schema validation, state machine,
                  audit chain, config lint and config diff (pure)
  services/       orchestration, retry scheduler, shadow impact evaluator
  infra/          SQLite repository, config loader, dependency adapters
workflows/        five YAML workflow definitions
web/              build-free operations console (HTML/CSS/ES modules)
tests/            88 unit and scenario tests
examples/         request payloads + demo script
ARCHITECTURE.md   design, data flow, trade-offs, scaling
CONFIGURATION.md  configuration reference and how to change rules/workflows
DECISION_EXAMPLES.md  worked input -> rules -> output -> audit examples
```

---

## Design choices at a glance

| Decision | Rationale |
| --- | --- |
| Pure domain layer | Deterministic tests, no I/O in decisions, easy to reason about retries |
| YAML + pinned snapshots | Requirement changes without code; reproducible decisions |
| SQLite + transactions | Simple, runnable, ACID; repository abstraction keeps Postgres migration mechanical |
| Synchronous execution | Clear audit ordering and immediate decisions; retries are scheduled for later attempts |
| In-process scheduler | Zero extra infrastructure for the assignment; contract is queue-ready |
| First-deciding-rule policy | Deterministic, explainable outcomes; full trace still records every evaluation |
| Simulation in request envelope | Fault injection without polluting business inputs or production defaults |
| Hash-chained audit events | Detects edits, deletions and reordering without changing the append-only event model |
| Dry-run + ETag reload guard | Configuration changes can be reviewed and concurrency-protected before activation |
| Shadow impact analysis | Quantifies outcome changes on real inputs before a rule change reaches traffic |

Detailed rationale, assumptions and scaling plan: [ARCHITECTURE.md](ARCHITECTURE.md).
Worked decision traces: [DECISION_EXAMPLES.md](DECISION_EXAMPLES.md).
