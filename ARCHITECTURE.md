# Architecture Document

## 1. Problem framing

The assignment asks for a *platform*, not a single approval endpoint. The distinguishing requirements are:

1. Business logic must be configurable (workflows, rules, thresholds, branching).
2. Decisions must be explainable and fully auditable.
3. Execution must survive external failures, partial writes, duplicate traffic and retries.
4. In-flight decisions must not change when configuration changes.
5. The platform must be generic across several unrelated use cases.

The architecture is therefore organised around a **generic execution core** plus **data-driven workflow definitions**.
Use-case specifics live in YAML; reliability and auditability live in the platform.

### Scope boundaries

**In scope:** intake validation, declarative rule evaluation, multi-stage transitions, dependency simulation with
retries/timeouts, persistence, hash-chained audit, counterfactuals, idempotency, manual review, retry scheduling,
configuration reload/versioning, configuration lint/diff/dry-run, shadow impact analysis, REST API, tests.

**Out of scope / deliberate simplifications:** production authentication/authorisation, a full BPMN expression
language, distributed workers, a front-end UI, real vendor integrations, PII encryption/redaction, multi-tenancy.
Each is called out under assumptions or scaling.

---

## 2. System overview

```
                        +--------------------------------------------------+
                        |                  FastAPI API                     |
                        |  /runs  /audit  /audit/verify  /explanation      |
                        |  /workflows  /lint  /impact  /reload  /resolve   |
                        +------------------------+-------------------------+
                                                 |
                                                 v
                        +--------------------------------------------------+
                        |              WorkflowService                     |
                        |  intake validation -> idempotency -> execution   |
                        |  transitions -> outcome -> retry scheduling      |
                        +-----------+------------------+-------------------+
                                    |                  |
              +---------------------v----+    +--------v-------------------+
              |    Domain (pure)         |    |   Infrastructure         |
              |                          |    |                          |
              |  rule_engine             |    |  RunRepository (SQLite)  |
              |  validation              |    |  WorkflowRegistry (YAML) |
              |  state_machine           |    |  DependencyRunner        |
              |  models                  |    |    -> SimulatedAdapter   |
              +--------------------------+    |    -> real adapters       |
                                              +--------------------------+
                                    ^
                                    |
                        +-----------+-----------+
                        |   RetryScheduler      |
                        |   polls due retries   |
                        +-----------------------+
```

**Dependency direction:** API -> services -> domain. Infrastructure implements ports used by services; domain imports
neither FastAPI nor SQLite. This keeps decision logic deterministic and testable in isolation.

### Components

| Component | Responsibility | Key files |
| --- | --- | --- |
| API layer | HTTP contracts, status codes, error envelope, OpenAPI | `app/api/routes.py`, `app/api/schemas.py` |
| Orchestrator | Use-case flow, stage iteration, transitions, outcome commit, retry budget | `app/services/workflow_service.py` |
| Rule engine | Evaluate condition trees against a context, produce per-rule traces | `app/domain/rule_engine.py` |
| Input validator | JSON-Schema subset with path/field error reporting | `app/domain/validation.py` |
| State machine | Legal run-status transitions | `app/domain/state_machine.py` |
| Config registry | Parse/validate YAML, atomic reload, snapshot/hash, preview and ETag | `app/infra/config_loader.py` |
| Config lint | Static analysis of fields, rules, thresholds and stage graph | `app/domain/config_lint.py` |
| Config diff | Structural comparison of workflow/stage/rule changes | `app/domain/config_diff.py` |
| Audit chain | SHA-256 event linking and verification | `app/domain/audit_chain.py` |
| Repository | Transactional persistence, optimistic locking, queries | `app/infra/repository.py` |
| Dependency runner | Retry/backoff, attempt recording, adapter dispatch | `app/infra/connectors.py` |
| Retry scheduler | Poll due runs and resume them | `app/services/retry_scheduler.py` |
| Shadow evaluator | Replays active vs candidate config in memory; builds impact matrix | `app/services/shadow_evaluator.py` |
| Simulation | Deterministic fault/response/latency/database injection | `app/domain/models.py`, adapters |

---

## 3. Domain model

```
WorkflowConfig 1---* StageConfig 1---* RuleConfig
      |                   |                  |
      |                   +---* DependencyConfig
      |                   +--- on_pass / on_fail / on{effect} transitions
      |
      +--- input_schema (JSON-Schema subset)
      +--- retry policy (backoff, max attempts)

RunState 1---* RunEvent (append-only, hash-chained)
   |             event_type, stage, outcome, payload, seq, created_at,
   |             prev_hash, event_hash
   +--- pinned workflow snapshot + hash + version
   +--- status, outcome, current_stage, context, result
   +--- idempotency_key, request_hash
   +--- retry_count, next_retry_at, last_error, optimistic version
```

### Run lifecycle

```
RECEIVED --start--> RUNNING --pass all stages--> APPROVED
                       |                          ^
                       |--rule/policy------------>| REJECTED
                       |                          |
                       |--escalation-------------> MANUAL_REVIEW --resolve--> APPROVED/REJECTED
                       |
                       |--transient failure------> RETRY_SCHEDULED --due/resume--> RUNNING
                       |
                       +--budget/technical-------> FAILED
```

Terminal states are `APPROVED`, `REJECTED`, `FAILED`. `MANUAL_REVIEW` is deliberately not terminal: it awaits an
explicit human decision. Illegal transitions raise a typed `409` rather than silently overwriting state.

### Decision algorithm

For each stage in order:

1. Write `stage_started`.
2. If a dependency is configured, execute it (inline retries). Record every attempt. On exhaustion, map the
   configured `failure_policy` (`retry` / `manual_review` / `reject`) through stage overrides to a terminal or retry
   outcome.
3. Merge successful dependency output into `context.dependencies`.
4. Evaluate all rules in order. Each rule produces a trace with the condition result, captured field values, mode,
   effect, severity and reason.
5. The **first deciding rule** determines the effect (`reject`, `manual_review`, `retry`) or a `branch` jump. For
   triggered leaf rules the trace also records a counterfactual: the field, actual value, expected threshold and the
   action that would satisfy an assertion or avoid a guard.
6. Apply stage transition precedence:
   `stage.on[effect]` -> `stage.on_fail` -> default outcome mapping. Pass uses `stage.on_pass` -> next stage -> approved.
7. Commit the stage's state update and all of its audit events in one transaction.
8. Continue, terminate, or schedule a workflow-level retry.

Rule semantics are intentionally small and explicit rather than a general expression language. This makes decisions
explainable and prevents a configuration change from accidentally introducing arbitrary code execution.

---

## 4. Data flow: submit request

```
Client
  | POST /runs {workflow, input, simulation?}
  v
API  -- Idempotency-Key header (optional) --> Service
                                                |
                                  1. load latest workflow config
                                  2. validate input against schema ----fail--> 422 + field paths (no run created)
                                  3. fingerprint = SHA256(workflow + input)
                                  4. lookup idempotency key
                                       existing + same hash  --> replay stored decision (200)
                                       existing + different hash --> 409
                                  5. INSERT run (RECEIVED) + request_received event  [txn]
                                  6. transition to RUNNING                            [txn]
                                  7. stage loop:
                                       - dependency attempts (retries/backoff)
                                       - rule evaluation + traces
                                       - stage commit (state + events)                [txn]
                                  8. terminal outcome commit / retry schedule         [txn]
                                                |
  <----------- RunView (status, outcome, result, current stage, retry info) ---------
```

The run is created **before** execution so that audit history exists even if a later stage fails. Input validation
happens before creation, so malformed requests do not produce noise in the run store. Each stage commit is atomic.

### Idempotency

- Key: caller `Idempotency-Key`, otherwise `auto:<sha256(workflow+input)>`.
- Fingerprint: canonical JSON hash of workflow name + business input. Simulation controls are excluded.
- Unique constraint on `idempotency_key` is the source of truth; a raced insert is caught and converted into a replay
  or conflict.
- Replay returns the current stored run state and sets `Idempotency-Replayed: true`. A failed mid-run request can be
  inspected/resumed by id rather than being duplicated.
- Reusing a key with a different payload is never allowed.

### Concurrency

Every run mutation updates `WHERE id = ? AND version = ?` and increments `version`. If a scheduler and an API request
race, one wins and the other receives `409 concurrent_modification`. The client can re-read and retry; retries are
idempotent by design. SQLite runs in WAL mode with `BEGIN IMMEDIATE` for writers.

---

## 5. Persistence and transactional boundaries

Two tables:

### `workflow_runs`

Stores one row per submitted request: identifiers, pinned workflow snapshot/hash/version, idempotency key and request
hash, status/outcome, current stage, input snapshot, execution context, result, retry state, timestamps, optimistic
lock version, and the audit anchor (event count + head hash).

### `run_events`

Append-only audit stream: `(run_id, seq)` unique, event type, stage, outcome, JSON payload, timestamp, plus
`prev_hash`/`event_hash`. Events are never updated or deleted. `seq` is contiguous per run and assigned inside the same
transaction as the state change. Each event hash covers the previous hash, run identity, sequence, event type, stage,
outcome, payload and timestamp, so edits, reordering and deletion of non-tail events are detectable. The run row also
stores an anchor (`audit_event_count`, `audit_head_hash`) updated in the same transaction; verification compares the
stream against it, which detects tail truncation.

**Why events plus denormalised run row?** Reading current state is O(1); reconstructing history is a simple ordered
query. The snapshot/row can be rebuilt from events if needed. Events also provide the evidence used by the explanation
endpoint.

**Partial save guarantee:** a stage transition and all of its events are one transaction. If the process dies or a
simulated commit fault occurs, either both are visible or neither is. The run stays at its previous committed stage
and can be resumed.

---

## 6. Failure model

### External dependency failures

`DependencyRunner` owns retry/backoff and produces a typed `DependencyOutcome` with one record per attempt. Adapters
are selected by connector name; any connector without a registered adapter uses the deterministic simulator.

Failure policy is per dependency:

| Policy | Exhausted outcome |
| --- | --- |
| `retry` | `retry_scheduled` (workflow-level backoff) |
| `manual_review` | `manual_review` |
| `reject` | `rejected` |

Inline retry uses exponential backoff capped by `max_delay_ms`. A timeout (`latency_ms > timeout_ms`) is treated as a
dependency failure. Real adapters implement their own network timeout; the runner still owns attempts and audit.

### Workflow-level retries

When an outcome is retryable:

- `retry_count` increments and `next_retry_at = now + workflow_backoff_seconds`.
- Status becomes `retry_scheduled`; `current_stage` remains at the failed stage.
- The scheduler polls due runs and resumes them; the API also supports manual resume.
- `max_workflow_attempts` includes the initial attempt. With `max_workflow_attempts: 4`, the run may execute four
  times total before becoming `failed`.
- Resume re-enters the failed stage from its beginning. Dependency attempts restart, and new audit events are appended.
- Stored simulation is reused on automatic resumes; an explicit resume may override it. This lets a test or operator
  clear an injected fault without editing database rows.

### Partial save failures

`database_fault: commit_failure` raises immediately before commit in the stage transaction. The exception rolls the
transaction back, returns `503` with `run_id` and `resume_url`, and leaves the run recoverable. Tests assert that no
stage events were persisted and that a later resume completes normally.

### Duplicate requests

Covered by idempotency (above) and the DB unique constraint. Application-level pre-check plus DB-level enforcement
handles both ordinary duplicates and races.

### Crash recovery

The run row always reflects the last committed stage. Because stage commits are atomic and events are append-only,
a crash between stages leaves a resumable `running` run. There is no partially written stage to clean up. Stale
`running` runs can be resumed manually; a production deployment would add a heartbeat/lease.

---

## 7. Auditability and explainability

Every meaningful action emits an event:

`request_received`, `run_started`, `run_resumed`, `stage_started`, `dependency_attempt`, `rule_evaluated`,
`stage_completed`, `outcome_decided`, `retry_scheduled`, `manual_review_resolved`, `run_failed`.

`GET /runs/{id}/audit` returns the ordered stream plus the input snapshot and workflow identity/hash.
`GET /runs/{id}/explanation` assembles a human-readable decision view:

- final outcome, deciding stage, deciding rule, reason;
- every evaluated rule with captured field values (the data actually used);
- dependency attempts and returned data;
- per-stage summaries;
- the full audit event stream.

The run's pinned workflow snapshot plus snapshot hash make the decision reproducible even after configuration changes.
Rule traces capture field **values**, not just booleans, which answers "why did this rule fire?" without re-running it.

### Tamper-evident audit chain

Events form a SHA-256 hash chain seeded by `GENESIS_HASH` (`"0" * 64`). `GET /runs/{id}/audit/verify` recomputes every
link and returns `valid`, `head_hash`, `event_count`, `broken_at_seq` and a reason. The same integrity summary is
embedded in `/audit` responses. The chain uses ordinary hashing, so it detects accidental or targeted row edits but is
not a substitute for database access control: an attacker able to rewrite every event and the run-row anchor could
recompute a consistent history. Production hardening is to HMAC the chain with a KMS-held key and anchor the head
(event count + hash) externally, e.g. in an append-only store or signed checkpoint.

---

## 8. Configurability and extensibility

### Changing a workflow or rule

Edit YAML, bump `version`, call `POST /workflows/reload`. No code change. Validation failures are returned with file and
field details, and the previous registry stays active (atomic swap). Existing runs continue using their pinned snapshot.

### Safe change workflow

1. **Lint:** `GET /workflows/{name}/lint` flags duplicate ids, unknown schema/dependency references, unreachable stages,
   transition cycles, thresholds that can never pass/fail under the declared schema bounds, and high-severity rules that
   can never decide.
2. **Preview:** `POST /workflows/reload?dry_run=true` validates the candidate directory, returns a structural diff of
   added/removed/changed workflows, stages and rules, and lints every candidate workflow. Nothing is activated.
3. **Guard concurrency:** send the current `config_etag` in `If-Match`; a mismatch returns `409 conflict` rather than
   activating a configuration that changed since inspection.
4. **Quantify impact:** `POST /workflows/{name}/impact` shadow-evaluates a full candidate snapshot against recent or
   explicitly supplied inputs and returns an outcome transition matrix, changed examples, schema failures and lint.
5. **Activate:** the ETag-guarded real reload performs the atomic swap.

### Shadow impact analysis

The shadow evaluator reuses the same rule engine, transition precedence and dependency runner as live execution, but
keeps all state in memory and writes no runs or events. It resolves workflow-level retries synchronously against the
retry budget so the report contains terminal outcomes instead of schedules. Inputs are schema-checked against both
configurations, which makes candidate schema changes visible as `schema_invalid` transitions. Historical inputs are
read from stored run snapshots; the default response omits raw inputs and only includes them when `include_inputs` is
true.

### Adding a workflow

Add a YAML file with a name, version, input schema, retry policy and stages/transitions. The generic engine picks it up
on reload. No Python changes are required for rules, thresholds, ordering, routing or dependency policies.

### Adding a rule operator

Extend the `Operator` enum, the comparison function in the rule engine, and tests. This is a domain-level change, not a
per-workflow change.

### Integrating a real dependency

Implement the `ConnectorAdapter` protocol and register it by connector name:

```python
runner.register("credit_bureau", RealCreditBureauAdapter(...))
```

The runner continues to own retries, backoff, attempt audit and failure-policy mapping. Neither the workflow service nor
the YAML changes.

### Adding a new outcome

Outcome/status enums and transition table are explicit. Adding an outcome is a deliberate domain change with tests,
rather than a hidden configuration side effect.

---

## 9. Scaling considerations

The current implementation is intentionally single-process (SQLite + in-process scheduler) so it runs anywhere. The
contracts are designed to scale without rewriting the domain:

1. **Stateless API replicas.** API pods can scale horizontally once the store moves to Postgres. Runs are addressed by
   ID and do not depend on local memory.
2. **Database.** Replace SQLite with Postgres using the same repository interface. Add indexes on
   `(workflow_name, created_at)`, `(status, next_retry_at)`; use `SELECT ... FOR UPDATE SKIP LOCKED` or a lease column.
3. **Idempotency at scale.** Keep the unique-key table in the primary database; optionally add a Redis cache for hot
   keys. The DB constraint remains the correctness boundary.
4. **Retry execution.** Replace the in-process poller with SQS/Kafka or a delayed-job system. The worker calls the same
   `resume_run(automatic=True)` contract. Leases prevent double-processing; optimistic versioning remains the final
   guard.
5. **Throughput.** Stage commits are short. For very high volume, partition `run_events` by time/run, archive closed
   runs, and use a write-optimised event store. Audit reads can go to replicas.
6. **Long-running dependencies.** Move dependency calls to async workers or add per-dependency circuit breakers and
   bulkheads. The adapter seam isolates this change.
7. **Multi-tenancy.** Add `tenant_id` to runs and include it in idempotency uniqueness and query predicates; enforce at
   the repository boundary.
8. **Observability.** Add metrics for decisions by workflow/outcome, dependency error rate, retry queue depth, stage
   latency and config reload failures. Structured audit events already provide the decision dimension.
9. **Data lifecycle.** Define retention for `run_events` (compliance-dependent), archival for terminal runs, and PII
   redaction/encryption before storing input snapshots in production.

---

## 10. Trade-offs and alternatives

| Choice | Benefit | Cost / mitigation |
| --- | --- | --- |
| Declarative rule DSL instead of a general expression language | Explainable, safe, validated, easy to audit | Less expressive; arithmetic/computed fields need operators or precomputed fields. Extend deliberately. |
| Synchronous execution | Immediate decisions, simple ordering, easy tests | Request latency tied to dependency latency; long workflows should move to async/queue workers. |
| SQLite | Zero infrastructure, ACID, easy runnable demo | Single-writer limits; repository abstraction and Postgres plan address production. |
| Stage-level transactions (not whole-run) | Crash recovery and retry without redoing committed stages | A run can remain `running` after a hard crash; resume endpoint / lease handles it. |
| First-deciding-rule wins | Deterministic and understandable | Later rules cannot "rescue" a decision; traces still show all evaluations. Severity does not override order—documented explicitly. |
| Store full input snapshot | Perfect auditability and reproducibility | PII exposure; production needs redaction/encryption/retention. |
| Pin workflow snapshot per run | Config changes never alter in-flight decisions | Storage overhead; acceptable for audit-critical workflows. |
| In-process scheduler | No extra infrastructure | Not HA; production requires queue/lease. |
| Auto idempotency key when header absent | Safe default against accidental duplicates | Two genuinely different intents with identical payloads collapse; callers who need distinct runs must send distinct keys or vary business data. |
| Simulation controls in request | Reproducible failure demos/tests | Must be disabled or strictly gated in production (assumption below). |
| Plain SHA-256 hash chain | Detects row-level tampering without external services | Whole-table rewrites can recompute the chain; HMAC + external anchoring is the production hardening path. |
| In-memory shadow evaluation | Fast, safe, no persistence side effects | Uses recorded inputs and simulated dependency behavior, not a live traffic mirror; see scaling note. |

### Assumptions

- Inputs are JSON with stable, comparable field types; no date arithmetic is required by the demo rules.
- Rule ordering is meaningful and authored deliberately; thresholds and routing are business-owned configuration.
- Exactly one active config version per workflow is loaded at a time; rollbacks are performed by editing YAML and
  reloading. Version history lives in the run snapshots and VCS.
- Idempotency applies to submission; resume/resolve are state-guarded but not separately keyed.
- The demo is single-tenant and unauthenticated. Admin reload and simulation overrides must be protected in production.
- Dependency adapters are synchronous; retries are bounded to avoid request pile-ups.
- Time is UTC; persisted timestamps are ISO-8601 with timezone.

---

## 11. Testing strategy

- **Unit-level:** condition operators, schema validation, transitions, traces.
- **API/integration:** FastAPI TestClient against a temporary SQLite database and copied workflow directory.
- **Failure injection:** deterministic dependency faults, timeout, commit failure, retry-budget exhaustion.
- **Config changes:** patch YAML, reload, assert new runs use the new version while old runs keep their pinned version;
  invalid reload is atomic; dry-run previews, ETag conflicts and diffs are covered.
- **Governance:** audit-chain verification/tamper detection, counterfactual values, lint findings and shadow impact
  matrices are asserted through the API and pure helpers.
- **Audit:** assert event ordering/sequence, input snapshot, field-level evidence, explanation contents, and that replay
  does not duplicate events or runs.

The suite is intentionally scenario-driven around the assignment's required cases rather than only code coverage.
