# CornerShop evaluation scenarios

`cornershop.yaml` is the source of truth for Nightjar's evaluation catalogue.
Run it with:

```bash
nightjar-eval --suite all
```

| Suite | Cases | What it proves |
| --- | ---: | --- |
| `cornershop-30` | 30 | Five v1 failure modes, six variants each, end-to-end |
| `relapse-6` | 6 | A passing T+0 check that does not survive the stability window must reopen |
| `injection-10` | 10 | Untrusted evidence carrying instructions cannot reach the plan or actions |

The headline metric is **false resolution**: an incident marked resolved while the
synthetic environment is still unhealthy. The harness also reports outcome
accuracy, verification coverage, approval handling, policy violations, prompt
injection resistance, hash-chain integrity, token use, cost, and latency.

Reports are written to `evals/reports/` and are never committed. See
`docs/evals.md` for methodology and thresholds.
