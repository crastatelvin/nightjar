# Runbook: exposed credential

Use this when a secret appears somewhere it should not: a token in a public repo or
gist, a key in a screenshot or support ticket, a credential in a build log or a
client bundle, or a secret-scanning alert.

Nightjar files this as `credential_exposure`. The class decides what counts as
proof: a check that **presents the exposed credential** and fails. "I revoked it"
is a claim; a 401 from the provider is evidence.

## What Nightjar does without you

1. Identifies the provider from the credential's shape (a prefix, a fingerprint),
   without writing the secret into the case file.
2. Collects read-only context: where it was found, when it first appeared, which
   identity or key id it belongs to, and whether the provider exposes last-used
   information.
3. Plans one action: `revoke_credential`, targeted at that credential.

Nightjar stores a **reference** to the credential (`env:NAME` or `file:/path`), not
the secret. The verifier resolves the reference at verification time, during the
short window it needs, and never records the value.

If you are pasting evidence into the case yourself, paste the first and last four
characters and the provider's key id — never the middle. The ledger is permanent,
hash-chained, and copied by every backup.

## What it will ask you to approve

Revocation is risk R2: it is reversible in principle, and it breaks whatever was
using the credential, which may be production. Expect an approval request, and
expect to have to check which deployments use the key before you say yes.

| Provider | Action | Automated | What verification reads back |
| --- | --- | --- | --- |
| GitHub | `revoke_credential` | yes | `GET /user` with the token must return `401` |
| Microsoft 365 | `revoke_credential` | yes | `signInActivity` shows no successful sign-in after revocation |
| Stripe | `revoke_credential` | **no — manual step** | `GET /v1/balance` with the key must return `401` |

### GitHub

Nightjar calls the revocation endpoint and then treats the credential as unproven
until the provider refuses it. Two response codes are worth knowing because they
look like success and are not:

- `202 Accepted` from the revoke call means *queued*, not *revoked*. It is never
  treated as proof.
- `403 Forbidden` when presenting the token means GitHub **accepted** the
  credential and refused the scope. The token is alive. Not revoked.

### Stripe

Stripe has no API to expire a key; the dashboard is the only place. Nightjar
records a manual step with the key's fingerprint and name, and the case waits in
`awaiting_manual`. You then:

1. Open <https://dashboard.stripe.com/apikeys>.
2. Find the key by name and fingerprint (the step shows both).
3. ⋯ → **Expire key** → confirm.
4. Attest the step in Nightjar, with a note that says what you did and when.

The attestation does not resolve the case. It tells the verifier to present the
key and require a `401`; only that decides the outcome. Attesting without expiring
the key produces a failed verification and the case stays open — which is the
correct outcome, not a bug to work around.

### Microsoft 365

`revoke_credential` revokes the user's sessions rather than a key, and it does not
disable the account: the user can sign in again immediately with a fresh session.
Verification reads `signInActivity` and requires no successful sign-in after the
revocation. If the tenant does not expose that field, the check **fails closed**: it
needs `AuditLog.Read.All` and Entra ID P1. That is deliberate — an unverifiable
session revocation is not proof, and the case stays open until the reading is
available. If the account itself is compromised, disable it in Entra as well and
record that as a note; v1 has no typed action for it.

## When Nightjar cannot act

- **No provider for `revoke_credential`.** If `GET /v1/meta/capabilities` shows no
  provider, the credential is identified but cannot be revoked through an API.
  Revoke it in the provider's dashboard, then add a note and run `verify` — the
  verification predicate still works if it was planned, and if it was not, the
  case cannot close on a claim. File a second case with a manually written proof
  only after reading *Check it yourself* below.
- **The credential is not a reference Nightjar can resolve.** The verifier needs
  `env:NAME` or `file:/path` to present the credential to the provider. If the
  secret lives in a vault Nightjar cannot read, the verification cannot be run by
  the product; do it by hand, record the command output as evidence, and expect
  the case to need an operator decision rather than a verifier pass.
- **The credential is already dead.** Verification still runs, still gets a 401,
  and still counts. You do not have to pretend the revocation caused it; the
  timeline shows what happened and when.

## Check it yourself (two minutes)

Present the credential to the provider and read the status code. This is the same
test Nightjar runs; doing it yourself is how you confirm the product is measuring
the right thing.

```bash
# GitHub: 401 means the token is dead. 403 means it is alive but under-scoped.
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $GITHUB_TOKEN" https://api.github.com/user

# Stripe: the key goes in as the username with an empty password.
curl -s -o /dev/null -w '%{http_code}\n' \
  -u "$STRIPE_KEY:" https://api.stripe.com/v1/balance
```

Unset the variables and clear your shell history afterwards
(`history -d`/`HISTCONTROL=ignorespace`), and do not paste the command you ran into
the case file with the key still in it.

Then answer the second question, which revocation does not answer: **was it used?**

- GitHub: the token's last-used time, the org audit log, and any new SSH keys,
  deploy keys, or repository access created around the exposure window.
- Stripe: the events log — look for charges, refunds, payout changes, and key
  creation; check the account's team and webhook endpoints too.
- Microsoft 365: Entra sign-in logs for the account, including successful and
  failed attempts, and mailbox rule changes.

If there was use, this is no longer a revocation case. Open a follow-on case with
what you found; do not close this one as "key rotated".

## Rolling back the rollback

There is no un-revoke. A revoked key stays revoked, a revoked session stays
revoked, and that is the point of the action.

The way forward:

1. Issue a new credential with the narrowest scope that works.
2. Put it in the secret store the deployment reads from, not in a config file that
   is itself in a repository.
3. Redeploy or restart whatever consumed the old one, and confirm it works.
4. Record the new credential's key id (not the value) in the case.

If revocation broke production, that is a sequencing mistake, not a reason to
weaken the verification: rotate first when the key is not being actively abused,
revoke first when it is. Either way, the case records which choice was made.

## Closing the case

The case is resolved when:

- a required predicate that presents the credential fails the way a dead credential
  fails — `401`, not `403`, not a timeout;
- that predicate is marked `required` and carries `credential_ref` or
  `credential_id`, which is what the class-level proof rule checks for.

Before you close your own ticket:

- confirm the old credential is dead by your own check, not only Nightjar's;
- confirm the replacement works;
- record whether the credential was used, and what you found;
- add a note if the exposure was caused by something still true (a workflow that
  prints secrets, a build artifact that bundles `.env`) — revoking one key does not
  fix the pipeline that leaked it.
