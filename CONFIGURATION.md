# Configuration Model

Everything business-specific is data. The engine interprets five workflow files in `workflows/`; changing rules,
thresholds, ordering or routing requires no Python change.

## 1. File format

One YAML file per workflow. Files are loaded and validated at startup and on `POST /workflows/reload`. Candidate
changes can be linted, previewed and impact-tested before activation.

```yaml
name: application_approval        # unique, [a-z][a-z0-9_]*
version: "1.0.0"                  # arbitrary string, recorded on every run
description: "..."                # optional
input_schema: {...}               # JSON-Schema subset (see below)
entry_stage: intake               # optional; defaults to first stage
retry:
  workflow_backoff_seconds: 2
  max_workflow_attempts: 4
metadata: {}                      # optional free-form object
stages: [...]
```

A workflow fails validation if stage names are duplicated, `entry_stage` is unknown, or any transition/branch points at
an unknown stage. Reload is atomic: on any validation error the previous registry remains active.

## 2. Input schema

Supported subset:

| Keyword | Notes |
| --- | --- |
| `type` | `object`, `array`, `string`, `number`, `integer`, `boolean`, `null`, or a list of types |
| `required` | object property names |
| `properties` | recursive child schemas |
| `additionalProperties` | `false` to reject unknown keys |
| `items` | recursive schema for array elements |
| `enum`, `const` | exact-value constraints |
| `minimum`, `maximum` | numeric bounds |
| `minLength`, `maxLength`, `pattern` | string constraints (regex via `re.search`) |
| `minItems`, `maxItems` | array bounds |

Validation errors are returned as `422` with `path`, `code` and `message`, and no run is persisted.

## 3. Stage

```yaml
stages:
  - name: credit_check
    description: "Call bureau, apply score rules"
    dependency: {...}             # optional
    rules: [...]                  # ordered; first deciding rule wins
    on_pass: {goto: affordability}       # optional
    on_fail: {outcome: manual_review}    # optional fallback for any failure
    on:                            # optional effect-specific overrides
      reject: {outcome: rejected}
      retry: {outcome: retry_scheduled}
      manual_review: {goto: review_stage}
```

Transition rules:

- Pass: `on_pass` if present; otherwise the next stage in file order; otherwise `approved`.
- Failure: `on[effect]` if present; otherwise `on_fail`; otherwise the default mapping:
  `reject -> rejected`, `manual_review -> manual_review`, `retry -> retry_scheduled`.
- A transition must define exactly one of `goto` (another stage) or `outcome` (terminal/retry).
- A dependency failure uses the same failure transition resolution, with the effect taken from `failure_policy`.
- Branch rules jump directly via their own `goto` and bypass `on_*`.

## 4. Rule

```yaml
rules:
  - id: minimum_credit_score     # unique within the workflow (duplicates are a lint error)
    description: "Risk appetite"
    severity: high               # info | low | medium | high | critical
    mode: assert                 # assert (condition must hold) | guard (condition triggers)
    condition: {field: applicant.credit_score, op: gte, value: 650}
    effect: reject               # reject | manual_review | retry | continue | branch
    reason: "Credit score is below the 650 minimum"
    # goto: auto_approval        # required only for effect: branch
```

### Modes

- **`assert`** (default): if the condition is false, the rule triggers its effect.
- **`guard`**: if the condition is true, the rule triggers its effect. Use for escalations and fast-track branches.

### Effects

| Effect | Behaviour |
| --- | --- |
| `reject` | Fail the stage with `rejected` (subject to stage overrides) |
| `manual_review` | Fail the stage with `manual_review` |
| `retry` | Schedule a workflow-level retry |
| `continue` | Non-deciding informational trace; evaluation continues |
| `branch` | Jump to `goto` immediately; mark stage `branched` |

Every rule is evaluated for a complete trace until a `branch` is taken; only the first non-`continue` triggered rule
decides. Field values used by each condition are recorded in the trace. Triggered leaf rules also include a
`counterfactual` in the trace (and in the explanation response) describing the field, actual/expected values, numeric
gap and the action needed to satisfy an `assert` or avoid a `guard`. Composite conditions are reported at the leaf
level only when they can be expressed as a single actionable change.

## 5. Conditions

Leaf:

```yaml
condition:
  field: applicant.credit_score   # dots traverse nested objects; numbers index arrays
  op: gte
  value: 650
```

Composite (exactly one):

```yaml
condition:
  all:
    - {field: applicant.credit_score, op: gte, value: 780}
    - {field: requested_amount, op: lte, value: 20000}
```

```yaml
condition:
  any:
    - {field: country, op: eq, value: US}
    - {field: country, op: eq, value: CA}
```

```yaml
condition:
  not: {field: applicant.employment_status, op: exists}
```

Operators:

| Operator | Meaning |
| --- | --- |
| `eq`, `neq` | equality / inequality |
| `gt`, `gte`, `lt`, `lte` | numeric comparison |
| `in`, `not_in` | membership in list/string/object |
| `contains`, `not_contains` | container contains value |
| `exists`, `not_exists` | key present and non-null |
| `matches` | regular expression search |
| `between` | inclusive numeric `[low, high]` |
| `len_gte`, `len_lte`, `len_eq` | length of string/list/object |

`value` may reference request or dependency data:

```yaml
- id: registry_country_match
  condition:
    field: dependencies.tax_registry.registry_country
    op: eq
    value: "$input.vendor.country"
```

## 6. Dependency

```yaml
dependency:
  connector: credit_bureau       # adapter lookup key + simulation key
  operation: score
  timeout_ms: 250
  retry:
    max_attempts: 3
    base_delay_ms: 5
    max_delay_ms: 40
  failure_policy: retry          # retry | manual_review | reject
  result:                        # simulated adapter output; templates supported
    verified: true
    bureau_score: "$input.applicant.credit_score"
    provider: sim-credit-bureau
```

- Retry delay is exponential: `base * 2^(attempt-1)`, capped at `max_delay_ms`.
- Successful output is merged into the run context under `dependencies.<connector>` and can be referenced by rules.
- Templates resolve `$input.*` and `$dependencies.*` paths recursively.
- To use a real integration, register a `ConnectorAdapter` for the connector name. Retry, timeout policy, audit and
  failure mapping remain in the platform.

## 7. Retry policy

```yaml
retry:
  workflow_backoff_seconds: 2     # delay before a scheduled retry becomes due
  max_workflow_attempts: 4        # total attempts including the initial one
```

When a retryable failure occurs:

1. `retry_count` increments.
2. If `retry_count >= max_workflow_attempts`, the run becomes `failed` with the budget-exhaustion reason.
3. Otherwise status becomes `retry_scheduled`, `current_stage` stays at the failed stage, and `next_retry_at` is set.
4. The scheduler or `POST /runs/{id}/resume` re-enters that stage from the beginning.

## 8. Simulation controls (demo/test only)

Sent in the request envelope, never inside `input`:

```json
{
  "workflow": "application_approval",
  "input": { "...": "..." },
  "simulation": {
    "faults": {"credit_bureau": "fail_until_attempt:2"},
    "responses": {"document_verifier": {"verified": false}},
    "latency_ms": {"credit_bureau": 500},
    "database_fault": "commit_failure"
  }
}
```

| Field | Values | Purpose |
| --- | --- | --- |
| `faults.<connector>` | `ok`, `error`, `unavailable`, `timeout`, `fail_until_attempt:<n>`, `flaky:<0..1>` | Deterministic dependency failures |
| `responses.<connector>` | any JSON | Override successful dependency output |
| `latency_ms.<connector>` | integer | Simulated latency; `> timeout_ms` produces a timeout |
| `database_fault` | `commit_failure` | Roll back a stage transaction to test partial-save safety |

Simulation is excluded from the idempotency fingerprint. A replay returns the original decision even if a different
simulation block is supplied; use a new idempotency key when intentionally re-running with different faults.

## 9. Recipes: changing behaviour without touching code

### Raise the minimum credit score

```yaml
- id: minimum_credit_score
  condition: {field: applicant.credit_score, op: gte, value: 700}
```

Bump `version`, reload. New runs use 700; existing runs keep 650 in their snapshot.

### Add a mandatory field

Add it to `input_schema.required` and a rule:

```yaml
- id: reference_required
  mode: assert
  condition: {field: reference, op: exists}
  effect: reject
  reason: Reference is mandatory
```

### Escalate high-value claims

```yaml
- id: high_value_claim
  mode: guard
  condition: {field: claim_amount, op: gt, value: 50000}
  effect: manual_review
  reason: Claims above 50,000 require senior review
```

### Fast-track low risk

```yaml
- id: fast_track
  mode: guard
  condition:
    all:
      - {field: applicant.credit_score, op: gte, value: 780}
      - {field: requested_amount, op: lte, value: 20000}
  effect: branch
  goto: auto_approval
  reason: Premium low-risk fast track
```

### Change degraded-service behaviour

```yaml
dependency:
  connector: document_verifier
  failure_policy: manual_review   # was retry
```

## 10. Safe change workflow

### Lint

```bash
curl -s http://localhost:8000/workflows/application_approval/lint
```

Findings have `severity`, `code`, `message`, `stage`, `rule_id` and `field`. The linter checks:

- duplicate rule ids across the workflow;
- rule fields and templates that are not declared in `input_schema.properties` (unless the schema is unconstrained);
- dependency references to connectors not configured in the current or an earlier stage;
- unreachable stages from `entry_stage`;
- stage transition cycles;
- numeric thresholds that can never pass or never fail given the schema `minimum`/`maximum`;
- high/critical rules using `effect: continue`, which can never decide.

The workflow detail endpoint embeds the same lint summary and findings.

### Dry-run and ETag guard

```bash
ETAG=$(curl -s http://localhost:8000/workflows | jq -r .config_etag)
curl -s -X POST 'http://localhost:8000/workflows/reload?dry_run=true' \
  -H "If-Match: $ETAG"
```

A dry-run parses and validates every file, returns the candidate summary and `candidate_etag`, and lists
added/removed/changed workflows, stages and rules. It does not swap the registry. Passing `If-Match` with the current
`config_etag` makes the operation conditional; a stale ETag returns `409 conflict` with the current value. An invalid
candidate returns `500 invalid_configuration` with per-file details and leaves the active registry untouched.

### Impact analysis

```bash
curl -s -X POST http://localhost:8000/workflows/application_approval/impact \
  -H 'Content-Type: application/json' \
  -d '{
    "candidate": { "...full WorkflowConfig JSON...": "..." },
    "sample_size": 100,
    "include_inputs": false
  }'
```

The candidate must be a complete workflow definition with the same `name`. The response includes:

- `sample_source`: `history` (recent stored inputs) or `request` (explicit `inputs` array);
- `transition_matrix`: baseline outcome -> candidate outcome counts;
- `outcomes` and `changed` / `changed_percent`;
- `schema_failures` for both configurations;
- up to ten `changed_examples` with baseline/candidate stage, reason, triggered rules and schema errors;
- `candidate_lint` findings.

Explicit `inputs` are evaluated without needing prior runs. Historical mode reads only the stored input snapshots; raw
inputs are included in changed examples only when `include_inputs` is true. Nothing is written or activated.

### Activate atomically

```bash
curl -s -X POST http://localhost:8000/workflows/reload -H "If-Match: $ETAG"
# 200 + new config_etag and diff, or 500 per-file validation details; old registry stays active on failure
```
