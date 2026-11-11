# Security policy

## Supported versions

Nightjar is pre-1.0. Security fixes land on `main` and in the latest tagged
release. There are no backports to older minor versions yet.

| Version | Supported |
| --- | --- |
| 0.1.x | yes |
| < 0.1 | no |

## Reporting a vulnerability

Do not open a public issue. Email the maintainer at
`crastatelvin@users.noreply.github.com` with:

- A description of the issue and its impact.
- Steps to reproduce, including the exact version or commit.
- Any proof-of-concept, logs, or affected endpoints.
- Whether you want credit in the advisory.

You will get an acknowledgement within 72 hours and a status update within seven
days. If the report is valid, we will coordinate a fix and a disclosure date.
Please give us a reasonable window before publishing.

## In scope

- Policy bypass: an action executing without the required decision or approval.
- False resolution: an incident reported resolved while verification should have
  failed.
- Prompt injection that changes a plan, an action, or the outcome.
- SSRF or private-network access through probes or connectors.
- Ledger tampering that is not detected by chain verification.
- Authentication bypass, secret disclosure, or remote code execution.
- Dependency vulnerabilities with a realistic exploit path.

## Out of scope

- Findings that require root on the Nightjar host.
- The self-asserted `x-nightjar-actor` header being spoofable by anyone holding the
  API token (documented limitation; deploy behind an authenticating proxy).
- Missing rate limiting in a single-tenant self-hosted deployment.
- Denial of service from an operator running the evaluation suite.
- Issues in the synthetic CornerShop lab that have no impact on the control plane.

## Safe harbour

Good-faith research on your own deployment or a local copy is welcome. Do not test
against systems you do not own or have permission to test, and do not access,
modify, or exfiltrate other people's data. We will not pursue legal action for
research that follows this policy.

## Hardening

See [docs/security/checklist.md](docs/security/checklist.md) for the deployment
checklist and [docs/security/threat-model.md](docs/security/threat-model.md) for the
threat model.
