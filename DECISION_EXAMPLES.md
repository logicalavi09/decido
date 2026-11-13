# Decision Explanation Examples

These examples show the same evidence exposed by `GET /runs/{id}/explanation` and
`GET /runs/{id}/audit`. Values are trimmed for readability; the API returns full rule traces, dependency attempts and
the complete audit stream.

The shared decision model is:

```
input snapshot + configured rules + dependency outputs
        -> ordered rule traces (field values captured)
        -> first deciding rule / stage transition
        -> outcome + reason
        -> append-only audit events
```

---

## Example 1 — Approved application (happy path)

### Input

```json
{
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
}
```

### Rules triggered

| Stage | Rule | Evaluation | Effect | Decision |
| --- | --- | --- | --- | --- |
| intake | `supported_country` | `US in [US,CA,GB,IN]` -> true | reject | no |
| intake | `employment_declared` | field exists -> true | manual_review | no |
| credit_check | `bureau_verified` | `dependencies.credit_bureau.verified == true` -> true | retry | no |
| credit_check | `minimum_credit_score` | `720 >= 650` -> true | reject | no |
| credit_check | `premium_fast_track` | guard `720 >= 780` -> false | branch | no |
| affordability | `exposure_limit` | `15000 <= 50000` -> true | reject | no |
| affordability | `income_threshold` | `4000 >= 1000` -> true | manual_review | no |
| affordability | `high_value_review` | guard `15000 > 25000` -> false | manual_review | no |

No rule triggered. The final stage has no rules, so the run passes to `approved`.

### Output

```json
{
  "status": "approved",
  "outcome": "approved",
  "current_stage": "auto_approval",
  "result": {
    "decision_stage": "auto_approval",
    "decided_by": null,
    "reason": "all rules passed",
    "workflow_version": "1.0.0"
  }
}
```

### Audit reasoning

```
request_received   input hash, workflow 1.0.0, pinned snapshot hash
run_started        start_stage=intake
stage_started      intake
rule_evaluated     supported_country (true), employment_declared (true)
stage_completed    intake -> credit_check
stage_started      credit_check
dependency_attempt credit_bureau attempt 1, ok, latency 0ms, bureau_score=720
rule_evaluated     bureau_verified, minimum_credit_score, premium_fast_track
stage_completed    credit_check -> affordability
stage_started      affordability
rule_evaluated     exposure_limit, income_threshold, high_value_review
stage_completed    affordability -> auto_approval
stage_started      auto_approval
stage_completed    auto_approval, passed
outcome_decided    approved, all rules passed
```

Why this is explainable: every rule that was checked is in the trace with the actual field value it saw, even when it
did not trigger. The dependency result used by `bureau_verified` is recorded in the same stream.

---

## Example 2 — Rejected application (threshold rule)

### Input

Same as Example 1 with:

```json
"applicant": { "credit_score": 600 }
```

Dependency returns:

```json
{ "verified": true, "bureau_score": 600, "provider": "sim-credit-bureau" }
```

### Rules triggered

| Rule | Evaluation | Result |
| --- | --- | --- |
| `bureau_verified` | verified == true | pass |
| `minimum_credit_score` | 600 >= 650 -> false | **triggered** (`reject`) |
| `premium_fast_track` | guard 600 >= 780 -> false | not triggered |

### Output

```json
{
  "status": "rejected",
  "outcome": "rejected",
  "current_stage": "credit_check",
  "result": {
    "decision_stage": "credit_check",
    "decided_by": "minimum_credit_score",
    "reason": "Credit score is below the 650 minimum",
    "rule_traces": [
      {
        "rule_id": "minimum_credit_score",
        "mode": "assert",
        "condition_result": false,
        "triggered": true,
        "effect": "reject",
        "severity": "high",
        "field_values": { "applicant.credit_score": 600 },
        "decided": true
      }
    ]
  }
}
```

### Audit reasoning

`run_started -> stage_started(intake) -> 2x rule_evaluated -> stage_completed -> stage_started(credit_check)
-> dependency_attempt(ok) -> 3x rule_evaluated -> stage_completed(rejected) -> outcome_decided(rejected)`

The explanation endpoint returns `decision.rule_id = "minimum_credit_score"`, the reason and the captured value `600`,
so an operator can answer both "what happened?" and "on what data?" without replaying the request.

### Counterfactual

```json
{
  "mode": "assert",
  "field": "applicant.credit_score",
  "operator": "gte",
  "actual": 600,
  "expected": 650,
  "required": ">= 650",
  "gap": 50,
  "action": "increase to at least 650 to satisfy the rule"
}
```

This answers the follow-up question "how close was it?" without editing and resubmitting the request.

---

## Example 3 — Manual review and human resolution

### Input

Same applicant (720, income 4000) requesting **30,000**.

### Rules triggered

| Stage | Rule | Evaluation | Result |
| --- | --- | --- | --- |
| credit_check | `minimum_credit_score` | 720 >= 650 | pass |
| affordability | `exposure_limit` | 30000 <= 50000 | pass |
| affordability | `income_threshold` | 4000 >= 1000 | pass |
| affordability | `high_value_review` | guard 30000 > 25000 | **triggered** (`manual_review`) |

### Output (initial)

```json
{
  "status": "manual_review",
  "outcome": "manual_review",
  "result": {
    "decision_stage": "affordability",
    "decided_by": "high_value_review",
    "reason": "Applications above 25,000 require a human credit decision"
  }
}
```

### Resolution

```http
POST /runs/{id}/resolve
{
  "decision": "approve",
  "reviewer": "credit.officer@example.com",
  "comment": "Verified income documents"
}
```

```json
{
  "status": "approved",
  "outcome": "approved",
  "result": {
    "reason": "manual review approved by credit.officer@example.com: Verified income documents",
    "manual_review": {
      "decision": "approve",
      "reviewer": "credit.officer@example.com",
      "comment": "Verified income documents",
      "resolved_at": "..."
    }
  }
}
```

### Audit reasoning

The original stage events remain untouched. Appended events:

```
manual_review_resolved  decision=approve, reviewer, comment, resolved_at
outcome_decided         approved, reason="manual review approved by ..."
```

A second resolution attempt returns `409 invalid_state_transition`; the human decision cannot be silently overwritten.

### Counterfactual

For `high_value_review` the explanation reports a **guard** counterfactual:

```json
{
  "mode": "guard",
  "field": "requested_amount",
  "operator": "gt",
  "actual": 30000,
  "expected": 25000,
  "required": "> 25000",
  "gap": 5000,
  "action": "decrease to at most 25000 to avoid triggering this rule"
}
```

---

## Example 4 — Dependency failure, scheduled retry and recovery

### Input

A valid application plus injected failure:

```json
"simulation": { "faults": { "credit_bureau": "error" } }
```

The dependency config is `max_attempts: 3`, `failure_policy: retry`.

### Rules triggered

No rules are evaluated: the stage cannot obtain bureau output. The failure policy decides.

### Output (initial)

```json
{
  "status": "retry_scheduled",
  "outcome": "retry_scheduled",
  "retry_count": 1,
  "next_retry_at": "2026-11-13T08:06:48.198+00:00",
  "last_error": "dependency 'credit_bureau' failed after 3 attempt(s): credit_bureau is unavailable",
  "result": {
    "decision_stage": "credit_check",
    "dependency_attempts": [
      {"attempt": 1, "ok": false, "error_code": "dependency_unavailable"},
      {"attempt": 2, "ok": false, "error_code": "dependency_unavailable"},
      {"attempt": 3, "ok": false, "error_code": "dependency_unavailable"}
    ]
  }
}
```

### Audit reasoning

```
stage_started(credit_check)
dependency_attempt x3   each failure recorded with attempt number, error code, latency
stage_completed         retry_scheduled
retry_scheduled         retry_count=1, next_retry_at, max_workflow_attempts=4
```

After the backoff, the scheduler (or `POST /runs/{id}/resume` with the fault cleared) appends:

```
run_resumed
stage_started(credit_check)
dependency_attempt      attempt 1, ok, bureau_score=720
rule_evaluated          bureau_verified, minimum_credit_score, premium_fast_track
stage_completed ... -> outcome_decided(approved)
```

Every attempt across every resume is retained. If failures reach `max_workflow_attempts`, the run becomes `failed`
with a budget-exhaustion reason. A partial database commit is handled the same way: the stage's state and events never
become visible, and the run can be resumed from its last committed stage.

---

## Example 5 — Conditional branching (fast track)

### Input

Applicant score **790**, requested amount **10,000**.

### Rules triggered

| Rule | Evaluation | Result |
| --- | --- | --- |
| `minimum_credit_score` | 790 >= 650 | pass |
| `premium_fast_track` | guard (790 >= 780 AND 10000 <= 20000) | **branch to `auto_approval`** |

`affordability` is skipped entirely.

### Output

```json
{
  "status": "approved",
  "result": {
    "decision_stage": "auto_approval",
    "reason": "all rules passed",
    "stage_results": [
      {"stage": "intake", "status": "passed"},
      {"stage": "credit_check", "status": "branched", "next_stage": "auto_approval",
       "decided_by": "premium_fast_track",
       "reason": "Low-risk premium applicant qualified for fast-track approval"},
      {"stage": "auto_approval", "status": "decided"}
    ]
  }
}
```

### Audit reasoning

The `stage_completed` event for `credit_check` records `status: branched`, `next_stage: auto_approval` and the branch
rule that caused the jump. The absence of an `affordability` `stage_started` event is itself evidence of the routing
decision. Branching is pure configuration: deleting or changing the rule changes routing with no code deployment.


---

## Example 6 — Audit-chain verification

`GET /runs/{id}/audit` returns the ordered stream plus an integrity block. Each event stores `prev_hash` and
`event_hash`; the first event links to the genesis hash (`"0" * 64`).

```json
{
  "integrity": {
    "valid": true,
    "algorithm": "sha256-chain",
    "event_count": 17,
    "head_hash": "9f2c...",
    "broken_at_seq": null,
    "reason": null,
    "anchor": {"event_count": 17, "head_hash": "9f2c..."}
  }
}
```

`GET /runs/{id}/audit/verify` recomputes the chain. If a payload was edited directly in storage, the first affected
sequence is reported:

```json
{
  "valid": false,
  "event_count": 17,
  "head_hash": "4ab1...",
  "broken_at_seq": 3,
  "reason": "event hash does not match the event contents",
  "genesis_hash": "0000000000000000000000000000000000000000000000000000000000000000"
}
```

Deleting a non-tail event or reordering rows is detected as a broken `prev_hash` or a missing/duplicate sequence.
The run row persists an anchor (event count + head hash), so deleting the newest events is reported as a truncated
stream. Rewriting both the events and the anchor would require full database write access; production should also
anchor the head in an external append-only store.

---

## Example 7 — Linting and previewing a rule change

Suppose a candidate changes `minimum_credit_score` from `650` to `900` while the schema maximum is `850`. Lint is
available before the config is activated:

```http
GET /workflows/application_approval/lint
```

```json
{
  "summary": {"error": 0, "warning": 1, "info": 0},
  "findings": [
    {
      "severity": "warning",
      "code": "rule_always_fails",
      "stage": "credit_check",
      "rule_id": "minimum_credit_score",
      "field": "applicant.credit_score",
      "message": "Rule 'minimum_credit_score' can never be satisfied ..."
    }
  ]
}
```

A dry-run returns the structural diff and candidate ETag without swapping the registry:

```http
POST /workflows/reload?dry_run=true
If-Match: <active config_etag>
```

```json
{
  "dry_run": true,
  "active_etag": "a1b2...",
  "candidate_etag": "c3d4...",
  "has_changes": true,
  "diff": {
    "added_workflows": [],
    "removed_workflows": [],
    "changed_workflows": [
      {
        "name": "application_approval",
        "version": {"from": "1.0.0", "to": "1.1.0"},
        "changed_sections": [],
        "added_stages": [],
        "removed_stages": [],
        "stages": {
          "credit_check": {
            "changed": true,
            "changed_fields": ["rules"],
            "added_rules": [],
            "removed_rules": [],
            "changed_rules": [
              {"rule_id": "minimum_credit_score", "fields": ["condition"]}
            ]
          }
        },
        "rule_changes": 1
      }
    ],
    "has_changes": true
  },
  "lint": {"totals": {"error": 0, "warning": 1, "info": 0}, "workflows": {"...": []}}
}
```

An `If-Match` value that no longer matches the active ETag returns `409 conflict` with `current_etag` and
`provided_etag`, so two operators cannot silently overwrite each other's configuration.

---

## Example 8 — Shadow impact of raising the manual-review threshold

Two historical applications request `30,000`; three request `15,000`. The candidate changes `high_value_review` from
`requested_amount > 25000` to `> 50000`.

```http
POST /workflows/application_approval/impact
{
  "candidate": { "...complete candidate workflow snapshot..." },
  "sample_size": 5
}
```

```json
{
  "sample_source": "history",
  "sample_size": 5,
  "changed": 2,
  "changed_percent": 40.0,
  "transition_matrix": {
    "approved": {"approved": 3},
    "manual_review": {"approved": 2}
  },
  "outcomes": {
    "active": {"approved": 3, "manual_review": 2},
    "candidate": {"approved": 5}
  },
  "schema_failures": {"active": 0, "candidate": 0},
  "changed_examples": [
    {
      "run_id": "f14c...",
      "baseline": {
        "outcome": "manual_review",
        "decision_stage": "affordability",
        "triggered_rules": ["high_value_review"]
      },
      "candidate": {
        "outcome": "approved",
        "decision_stage": "auto_approval",
        "triggered_rules": []
      }
    }
  ],
  "candidate_lint": {"summary": {"error": 0, "warning": 0, "info": 0}, "findings": []}
}
```

This makes the operational consequence of a rule edit explicit before activation: two manual reviews disappear, and
the reviewer can decide whether that is intended.
