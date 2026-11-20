# Runbooks

Three incident classes are in scope for v1. Each runbook is written for the person
who owns the systems, not for someone who will read the source: what Nightjar does
on its own, what it will ask you to approve, what it cannot do, how to check the
result yourself, and how to undo the fix if the fix was wrong.

| Class | Runbook |
| --- | --- |
| Site or checkout is down | [site-outage.md](site-outage.md) |
| A credential was exposed | [exposed-credential.md](exposed-credential.md) |
| A phishing message was reported | [phishing.md](phishing.md) |

## The shape of every case

Nightjar moves a case through the same states regardless of class:

```
intake → investigating → awaiting_approval → executing → verifying → resolved
                │                 │
                │                 └── awaiting_manual  (a step only a human can do)
                └── failed / reopened
```

- **investigating** — the agent probes and reads. Read-only actions (`probe`,
  `http.get`) need no approval.
- **awaiting_approval** — at least one planned action is risk class R2. Nothing has
  changed yet. The approval expires after 30 minutes (`approval_ttl_seconds`).
- **awaiting_manual** — the provider has no API for the action (Stripe key
  revocation is the case in v1). Nightjar shows the instructions and waits.
- **verifying** — actions ran, and the required checks are being measured. A plan
  without the proof its class requires never reaches this state.
- **resolved** — the required checks passed *and* stayed passed for the stability
  window (15 minutes by default, `NIGHTJAR_STABILITY_SECONDS`). A site that comes
  back for 40 seconds and falls over again is not resolved.

Risk classes: R0 read-only, R1 reversible with no customer impact, R2 reversible
with possible customer impact, R3 destructive or financial. **R3 is refused in
v1**; nothing you can approve can delete data or move money.

## Before you start

```bash
export NIGHTJAR="https://nightjar.internal"
export TOKEN="…"                       # the shared token, or an OIDC access token
export ACTOR="you@example.com"         # audit label; ignored when OIDC is on

# Every request in these runbooks uses the same two headers.
auth=(-H "Authorization: Bearer $TOKEN")
curl -fsS "${auth[@]}" -H "x-nightjar-actor: $ACTOR" "$NIGHTJAR/v1/meta" | jq .
```

`GET /v1/meta/capabilities` (and `nightjarctl doctor`) answers the question that
matters before an incident: *for this action, which provider is configured and
healthy right now?* If an action shows no provider, Nightjar cannot perform it,
and the runbook section "when Nightjar cannot act" applies.

Approvals are the one thing that can be gated by group membership. If
`NIGHTJAR_OIDC_APPROVER_GROUPS` is set, approving a plan and attesting a manual
step require a token whose groups match; reading and filing stay open to any
authenticated caller. With `NIGHTJAR_REQUIRE_SEPARATION_OF_DUTIES=true`, the
person who filed the incident cannot be the person who approves it.

## Reading a case

The UI is at `/incidents/<id>`; the same information is one request:

```bash
curl -fsS "${auth[@]}" "$NIGHTJAR/v1/incidents/$ID" | jq '{
  status: .incident.status,
  actions: [.actions[] | {type: .action_type, target, risk}],
  states: .action_states,
  approvals: [.approvals[] | {id, status, requested_by}],
  manual: .manual_steps,
  verifications: [.verifications[] | {kind, passed, detail}]
}'
```

Approving, rejecting, and attesting:

```bash
# Approve (or reject) an R2 action.
curl -fsS -X POST "${auth[@]}" -H "x-nightjar-actor: $ACTOR" \
  -H 'content-type: application/json' \
  -d '{"approved": true, "comment": "checked the deploy, it is the bad one"}' \
  "$NIGHTJAR/v1/incidents/$ID/approvals/$APPROVAL_ID" | jq '.incident.status'

# Attest a manual step after doing it in the provider's dashboard.
curl -fsS -X POST "${auth[@]}" -H "x-nightjar-actor: $ACTOR" \
  -H 'content-type: application/json' \
  -d '{"note": "expired key sk_live_…AbC in the Stripe dashboard at 14:12Z"}' \
  "$NIGHTJAR/v1/incidents/$ID/manual-steps/$ACTION_ID/attest" | jq '.incident.status'

# Ask for another verification pass without changing anything.
curl -fsS -X POST "${auth[@]}" "$NIGHTJAR/v1/incidents/$ID/verify" | jq '.incident.status'
```

An attestation is not a resolution. It records who says the step was done and
then triggers the same verification as any other action, and only the verifier
decides whether the case closes.

## The ledger

Every case carries two hash chains, one over its timeline and one over its
evidence. Both are recomputed whenever evidence is added, and can be checked at
any time:

```bash
curl -fsS "${auth[@]}" "$NIGHTJAR/v1/incidents/$ID/chain" | jq .
nightjarctl verify-ledger --database-url "$NIGHTJAR_DATABASE_URL"
```

If either says `false`, stop treating the case file as evidence and say so in your
own incident notes. A break means a row was altered outside the product or the
database was restored from an unknown point. See
[../operations.md](../operations.md#verifying-the-ledger-directly).

## When something goes wrong mid-case

| Symptom | What it means | What to do |
| --- | --- | --- |
| `409` with `Retry-After` | another operator or replica holds the case lock | wait two seconds, retry; do not file a duplicate case |
| `429` with `Retry-After` | your caller exceeded the API request budget (per process, or shared across replicas with the `database` backend) | wait the number of seconds in the header |
| action `unknown`, execution `unknown`, event `action.outcome_unknown` | the provider accepted the request and never answered, so the change may or may not have landed | nothing at first: Nightjar immediately runs the required predicates. If they pass, the case resolves on proof. If they fail, the case fails with the action left `unknown` — check the provider console before filing a new incident, because a retry could apply the change twice |
| action `failed` | the provider refused the request and said so | read the execution error, fix the cause (scope, target, credential), then `POST /retry` |
| `awaiting_manual` with no attestation possible | the attestation account is not in the approver group | ask an approver, or note the group configuration problem in the case |
| case stuck in `verifying` | checks passed once and the stability window is still running | nothing; it resolves on its own, or fails and reopens if the check fails again |
| `reopened` | a check failed after resolution | the case re-runs the plan; a new approval is requested if the plan changed |

`POST /v1/incidents/{id}/retry` re-runs the plan from its current state. It does
not re-plan, and it does not skip policy or approval.
