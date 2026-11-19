# Runbook: reported phishing message

Use this when someone reports an email that asks for credentials, a payment change,
or a document login — or when a mail gateway alert names a message that reached a
mailbox.

Nightjar files this as `phishing`. The class decides what counts as proof: **two**
connector checks, one that the message is contained and one that the sender is
filtered. A label that was added is not proof that the message left the inbox; the
filter that was created is not proof that it matches the sender. Both are read back.

## What Nightjar does without you

1. Records the report itself as evidence: who reported it, when, and what they
   said. The message body, headers, and links are noted as references.
2. Plans containment for the mailbox: quarantine the message, block the sender.
3. Notifies the reporter or the on-call channel (`notify`, R1).

Nothing here opens a link. Nightjar does not fetch URLs from a suspected phishing
message: an attacker who can log a request learns that the message was reported and
which address is being watched. If a link needs inspection, do it in an isolated
browser or a URL-scanning service, never from a corporate network.

## What it will ask you to approve

| Action | Risk | Runs without approval | Effect | Read back on verification |
| --- | --- | --- | --- | --- |
| `quarantine_message` | R1 | yes | Google: add label `Nightjar-Quarantine`, remove `INBOX` and `UNREAD`. Microsoft: move the message to the `Nightjar Quarantine` folder | the message is no longer in the inbox and carries the container (label or folder), read back from the provider |
| `block_sender` | R2 | no | Google: a Gmail filter matching the sender that applies the quarantine label and skips the inbox. Microsoft: an inbox `messageRules` entry | the filter or rule exists and matches the sender, read back from the provider |
| `notify` | R1 | yes | post to the configured channel | the notification response |

`block_sender` is R2 because a filter on a real sender affects every future message
from them. Approve it after checking the sender is not a partner or a mailing list
you need. "Block the sender" on a spoofed `From` blocks the display name, not the
attacker; that is still worth doing, and it is not the whole job.

Two things the mail connector always does, which can look surprising in the action
parameters:

- The **mailbox is the target**, not the message. An action against one message in
  one person's inbox still names that person's mailbox, and the mailbox must be in
  `NIGHTJAR_MANAGED_TARGETS` in production.
- On Microsoft 365, a quarantine is a folder move, and Graph returns a **new
  message id** after the move. The verification follows the move and checks the
  destination folder; the old id is not the thing to look for. The verification
  also never creates the quarantine folder: if the folder is missing, the check
  fails closed rather than silently passing on a message that did not move.

## What you need before it can act

Containment needs a message id and a mailbox. Reports do not always carry both:

- **Gmail:** the message id is in *Show original* (`Message-ID` header) or from
  Admin console → Reports → Email search. The mailbox is the recipient's address.
- **Microsoft 365:** use the message id from the message's properties in Outlook on
  the web, or from a compliance/content search. If you only have the subject and
  sender, a content search finds it.
- **Only a screenshot?** You can still block the sender if the address is visible.
  You cannot prove containment without the message, and the case cannot close on a
  filter alone — that is the two-check rule doing its job.

File the missing detail as a note on the case, not as a new case. The note is what
the next person reads.

## When Nightjar cannot act

- **No mail provider configured.** `GET /v1/meta/capabilities` will show no provider
  for `quarantine_message` and `block_sender`. Do it by hand: move the message out
  of the inbox, and block the sender at the tenant level (Gmail Admin → Apps →
  Google Workspace → Gmail → Compliance → Blocked senders; Microsoft 365 Defender →
  Policies → Anti-spam → blocked senders). Then note what you did and press
  *verify* — but understand that without the connector the two required checks
  cannot run, so the case will not resolve on its own. That is the honest outcome:
  Nightjar will not claim containment it cannot see. Note also that a tenant-level
  admin block is a different mechanism from the per-mailbox filter Nightjar
  creates, so a later case will not find it where it looks.
- **Mailbox not in the managed list.** Production refuses the action with
  `target is not in NIGHTJAR_MANAGED_TARGETS`. Add the mailbox to
  `NIGHTJAR_MANAGED_TARGETS` and restart the API.
- **Google Workspace configured without mailboxes**, or Microsoft 365 with a partial
  credential set: startup refuses the configuration rather than running half a
  connector. See `docs/operations.md` and the provider setup notes in `.env.example`.
- **The message is already gone.** Deleted messages cannot be quarantined. If the
  user deleted it and emptied the trash, record that; block the sender anyway if
  the address is known.

## Check it yourself (two minutes)

Search the mailbox the way an attacker-proof reader would — by sender and subject,
not by trusting the label:

```
from:(sender@example.com) subject:("your invoice")
```

- Gmail: the message should be absent from Inbox and present under the
  `Nightjar-Quarantine` label. Check **All Mail**, not just Inbox.
- Microsoft 365: the message should be in `Nightjar Quarantine` and absent from
  Inbox. Check *Recoverable items* if it seems to have vanished entirely.

Then confirm the filter:

- Gmail: the filter appears in that user's **Settings → Filters and blocked
  addresses**, not in the admin console. The admin console's blocked-senders list
  is a tenant-wide mechanism and is not what Nightjar creates.
- Microsoft 365: the rule appears in the mailbox's **Settings → Mail → Rules**,
  which is also where the connector writes it (`messageRules` on the inbox folder).

Finally, the part no connector can do: **were there other recipients?** A phishing
message is rarely sent to one person. Search the tenant for the sender and the
subject, quarantine the copies by hand (or file one case per mailbox), and tell the
people who received it. If anyone entered credentials, this becomes a credential
case — open one, do not append to this one.

## Rolling back the rollback

A false positive is the case to prepare for, because quarantine is invisible to the
user and a blocked sender is invisible to everyone.

| Action | Undo |
| --- | --- |
| Google quarantine | remove the `Nightjar-Quarantine` label and add `INBOX` back (Gmail → the message → Labels) |
| Microsoft quarantine | move the message from `Nightjar Quarantine` back to Inbox |
| Google sender block | delete the per-mailbox filter (Gmail → Settings → Filters and blocked addresses) |
| Microsoft sender block | delete the `messageRules` entry (Outlook → Settings → Mail → Rules, or the admin console) |

Before releasing a message, confirm it is genuinely benign. A message that was
quarantined for a good reason and then released because someone was impatient is a
worse outcome than a support ticket, and the timeline will show who released it.

If Nightjar blocked a partner's address, remove the filter, then check whether the
message that triggered the block was spoofing that partner. If it was, the block was
correct and the partner's real mail was collateral; consider a narrower rule
(sender plus subject) rather than lifting the block entirely.

## Closing the case

The case is resolved when both required connector checks pass:

- the message is out of the inbox and in the quarantine container, read back from
  the provider;
- a filter or rule matching the sender exists, read back from the provider.

Optional checks are recorded and never decide the outcome. A verification pass
followed by the stability window is what closes the case, so expect a delay of
about fifteen minutes between "it looks contained" and `resolved`.

Before you close your own ticket:

- confirm there were no other recipients, or that their copies were handled;
- confirm nobody entered credentials, or hand off to the credential runbook;
- record the sender, subject, and message id in a note — the case file is the only
  place the next analyst will look;
- if the message came from a compromised internal account, this is not a sender
  block problem: the account is the incident.
