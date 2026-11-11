# Governance

Nightjar is a small, open project. This document says who decides what, so
contributors do not have to guess.

## Model

- **Benevolent maintainer** for 0.x. The maintainer has final say on roadmap,
  architecture, and releases, and is accountable for security.
- **Contributors** propose changes through pull requests and issues. Substantial
  changes should start as an issue or an ADR.
- **Committers** (added as the project grows) can merge in their area after
  review. Commit access is earned through sustained, high-quality contributions.

## Decisions

- Day-to-day changes: lazy consensus in the pull request.
- Architecture, policy, action catalogue, and evaluation changes: an ADR in
  `docs/adr/`, reviewed before merge.
- Changes to the safety invariants require explicit maintainer approval and a
  passing full evaluation run.
- If consensus fails, the maintainer decides and records the reasoning in the ADR
  or issue.

## Releases

- Semantic versioning. 0.x may break; breaking changes get a changelog entry and a
  migration note.
- Every release is tagged, has a changelog, and passes lint, types, tests, policy
  tests, and the full evaluation suite.
- The default branch is always releasable.

## Contribution terms

- Apache-2.0, inbound = outbound.
- Developer Certificate of Origin 1.1; sign off every commit. No CLA.
- No contributor license assignment, no open-core split, no proprietary edition.

## Security

Security reports follow [SECURITY.md](SECURITY.md). The maintainer coordinates
fixes and disclosure. Security fixes may be merged privately before disclosure.

## Becoming a committer

Sustained contributions over time, good judgement on safety-sensitive changes, and
review participation. There is no fixed count; the maintainer will ask.

## Removing access

Commit access may be removed for inactivity, repeated policy violations, or
conduct that makes the project hostile. This is rare and will be explained
privately where possible.

## Changing this document

Open a pull request. Governance changes need maintainer approval and a one-week
comment period for anything that affects contribution terms.
