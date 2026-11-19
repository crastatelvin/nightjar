#!/usr/bin/env bash
# File one lab incident through the composed stack and require it to resolve.
#
# This is the check that the images, the migration job, the environment wiring, and
# the API all agree with each other. Unit tests exercise the code; this exercises
# the deployment a person actually runs.
#
#   ./scripts/compose-smoke.sh                     # API on 127.0.0.1:8000
#   NIGHTJAR_SMOKE_URL=http://host:8000 ./scripts/compose-smoke.sh
#
# Exit 0 only when the incident reached `resolved` and both hash chains verify.
set -euo pipefail

API="${NIGHTJAR_SMOKE_URL:-http://127.0.0.1:8000}"
LAB="${NIGHTJAR_LAB_URL:-http://127.0.0.1:8100}"
SCENARIO="${NIGHTJAR_SMOKE_SCENARIO:-site_cache_poisoned}"
DEADLINE_SECONDS="${NIGHTJAR_SMOKE_TIMEOUT:-240}"

say() { printf '  %s\n' "$*"; }
die() { printf 'compose smoke failed: %s\n' "$*" >&2; exit 1; }

json() {
  # json <expression> reads a JSON document on stdin and prints one value. The
  # expressions are literals written below, never input from a server.
  python3 -c 'import json,sys; doc = json.load(sys.stdin); print(eval(sys.argv[1], {"doc": doc}))' "$1"
}

wait_for() {
  local url="$1" what="$2" deadline=$((SECONDS + DEADLINE_SECONDS))
  until curl -fsS -o /dev/null "$url" 2>/dev/null; do
    ((SECONDS < deadline)) || die "$what did not become ready at $url"
    sleep 1
  done
}

api() {
  local method="$1" path="$2"; shift 2
  curl -fsS -X "$method" -H 'content-type: application/json' "$@" "$API$path"
}

say "waiting for the API at $API"
wait_for "$API/readyz" "the API"
say "waiting for the lab at $LAB"
wait_for "$LAB/healthz" "the lab"

say "resetting the lab and starting $SCENARIO"
curl -fsS -X POST "$LAB/lab/reset" >/dev/null
seed="$(curl -fsS -X POST "$LAB/lab/scenario/$SCENARIO" -H 'content-type: application/json' -d '{}')"

payload="$(printf '%s' "$seed" | python3 -c '
import json, sys
seed = json.load(sys.stdin)
fields = ("title", "description", "incident_class", "severity", "source", "metadata")
print(json.dumps({key: seed[key] for key in fields if key in seed}))
')"

say "filing the incident"
incident="$(api POST "/v1/incidents?wait=true" -d "$payload")"
id="$(printf '%s' "$incident" | json 'doc["id"]')"
say "incident $id is $(printf '%s' "$incident" | json 'doc["status"]')"

deadline=$((SECONDS + DEADLINE_SECONDS))
while :; do
  case="$(api GET "/v1/incidents/$id")"
  status="$(printf '%s' "$case" | json 'doc["incident"]["status"]')"
  case "$status" in
    resolved) break ;;
    failed|cancelled) die "the incident ended $status: $(printf '%s' "$case" | json 'doc["incident"]["summary"]')" ;;
  esac

  # Approve anything pending, the way an operator would. The default scenario
  # needs no approval, but a compose stack configured for a real provider might.
  for approval in $(printf '%s' "$case" | python3 -c '
import json, sys
case = json.load(sys.stdin)
for approval in case["approvals"]:
    if approval["status"] == "pending":
        print(approval["id"])
'); do
    say "approving $approval"
    api POST "/v1/incidents/$id/approvals/$approval" -d '{"approved": true, "comment": "compose smoke"}' >/dev/null
  done

  ((SECONDS < deadline)) || die "the incident stayed $status after ${DEADLINE_SECONDS}s"
  sleep 2
done

chain="$(api GET "/v1/incidents/$id/chain")"
[[ "$(printf '%s' "$chain" | json 'str(doc["evidence_valid"]).lower()')" == "true" ]] \
  || die "the evidence chain is broken"
[[ "$(printf '%s' "$chain" | json 'str(doc["timeline_valid"]).lower()')" == "true" ]] \
  || die "the timeline chain is broken"

printf 'resolved: %s timeline events, %s evidence items, both chains intact\n' \
  "$(printf '%s' "$chain" | json 'doc["timeline_checked"]')" \
  "$(printf '%s' "$chain" | json 'doc["evidence_checked"]')"
