# Runbook: site or checkout outage

Use this when a customer-facing page or the checkout flow is failing: 5xx from the
edge or origin, a blank or error page, a checkout that hangs at payment, or an
alert source reporting elevated errors.

Nightjar files this as `site_outage`. The class decides what counts as proof of
recovery: a user-visible HTTP check **and** a functional check of the thing that
was broken. A 200 from the HTML page does not close a case where the checkout API
was returning 502.

## What Nightjar does without you

1. Collects read-only evidence: the failing URL (status, timing, body excerpt), DNS
   records, TLS certificate expiry, and whatever the configured provider exposes
   about the current deployment.
2. Attributes the failure: edge versus origin, deploy-related versus load-related,
   one URL versus the whole site.
3. Proposes the smallest plan that the evidence supports, and names a provider only
   when that provider is configured and healthy.

Everything in step 1 and 2 is risk R0 and needs no approval. The plan is where you
come in.

## What it will ask you to approve

| Action | Risk | Runs without approval | Effect | Read back on verification |
| --- | --- | --- | --- | --- |
| `purge_cache` | R1 | yes | purge the zone's cache for the named URLs | the required HTTP check, plus cache headers if configured |
| `rollback_deployment` | R2 | no | promote a previous deployment back to production | Vercel: deployment `READY`, targeted at production, reachable through the production alias. Cloudflare Pages: the rollback deployment is the project's production deployment. Render: deploy state for the rolled-back deploy |
| `restart_service` | R2 | no | restart a service or machine | Render: service is live again after restart. Fly: machine state `started` and update time advanced |
| `enable_under_attack` | R2 | no | raise the zone's security level to *I'm Under Attack* | the zone's security level, read back from Cloudflare |

Approval is per action, not per plan. Approve the rollback and the restart
separately if the plan contains both; rejecting one does not cancel the others.

`enable_under_attack` is the one that changes what customers see: browsers get an
interstitial challenge, and some legitimate traffic will fail. Approve it when the
alternative is being offline, and remember to lower the security level afterwards
— Nightjar does not raise it and lower it for you.

A rollback target is checked before anything changes: Vercel refuses a deployment
that is not `READY`, and Cloudflare Pages refuses one that is not a successful
production deployment. If the target is wrong, the action fails immediately
instead of promoting something broken.

## When Nightjar cannot act

`GET /v1/meta/capabilities` is the answer. Common gaps and what to do:

- **No CDN or host connector configured.** Nightjar still probes and tells you
  which layer is failing; you purge, roll back, or restart by hand. Then press
  *verify* (or `POST /v1/incidents/$ID/verify`) so the same checks decide the
  outcome. Say what you did in a note — the timeline is the record.
- **The target is not in the managed list.** In production every action must name a
  target in `NIGHTJAR_MANAGED_TARGETS`; a refusal reads
  `target is not in NIGHTJAR_MANAGED_TARGETS`. That is deliberate: a plan cannot
  invent a URL and have it acted on. Add the project/service/app to the allowlist
  (Cloudflare and Vercel call them *projects*, Render *services*, Fly *apps*) and
  restart the API.
- **Two providers can do the same action.** The planner has to name one
  (`params.connector`), otherwise the case stops before approval rather than
  guessing. Check that the right provider is the intended one before approving.
- **R3 actions.** None exist in v1. If you find yourself wanting Nightjar to
  delete a database or move money, stop: this product will not, and the runbook
  ends here.

## Check it yourself (five minutes)

Do this even when Nightjar says resolved, at least the first few times you use it.
You are checking two different things: that the site is up, and that the thing
that was broken is working.

```bash
# 1. The page a customer loads.
curl -sS -o /dev/null -w 'status=%{http_code} time=%{time_total}s\n' \
  https://shop.example.com/checkout

# 2. The functional endpoint the page calls. Substitute your own.
curl -sS https://shop.example.com/api/checkout/health | jq .

# 3. A real test transaction, with a test card, in the payment provider's test mode.
#    A 200 on the page and a green health endpoint do not prove a payment works.
```

Then confirm what is actually deployed:

```bash
vercel ls --prod                 # or: vercel inspect <deployment-url>
fly status -a <app>              # machine state and image
render services                  # deploy status
# Cloudflare dashboard → Pages → project → Deployments
```

If Nightjar reports resolved and your own check disagrees, reopen it: add a note
with your command output, then `POST /v1/incidents/$ID/verify`. A disagreement
between the product and reality is a bug worth reporting, and the case file is
what makes it debuggable.

## Rolling back the rollback

A rollback promotes an older build; it does not revert anything in place. So
undoing it is the same operation with the other target — promote the deployment
you came from.

| Provider | Undo |
| --- | --- |
| Vercel | promote the newer deployment back to production (dashboard → Deployments → ⋯ → *Promote to Production*, or `vercel promote <url>`) |
| Cloudflare Pages | Deployments → rollback to the deployment that was production before the rollback |
| Render | Deploys → the deploy you displaced → *Rollback* |
| Fly | a restart does not change the image; to change code, `fly deploy` the previous image tag |
| Cloudflare cache | nothing to undo: the cache refills from origin. If a purge caused an origin surge, that is load, not state |

Rolling forward is usually better than rolling back twice. If the older build is
itself broken, fix forward.

## Closing the case

The case is resolved when:

- the required HTTP predicate passes and keeps passing for the stability window
  (15 minutes by default);
- the required functional check passes — a JSON assertion, or a connector read-back
  that says the deployment is the one you expect;
- those are the predicates marked `required`; optional checks are recorded but
  never decide the outcome.

A case that resolves and then fails a check reopens by itself and re-runs the
plan, with a fresh approval if the plan changed. That is expected behaviour, not a
reason to file a second case.

Before you close your own ticket, record in the Nightjar case:

- what the customer saw, and the time range;
- which change caused it (deploy id, config change, cache rule);
- what you did that Nightjar could not do;
- whether the detection was fast enough to matter.

`GET /v1/incidents/$ID/chain` should say both chains are valid. If it does not,
read [the ledger section](README.md#the-ledger) before treating the case file as
evidence.
