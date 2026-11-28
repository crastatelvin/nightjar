# README media

Every file here is a capture of this build running against the lab connector. None of
it is a mockup, and none of it is hand-drawn except the logo.

| File | What it is | Regenerate with |
| --- | --- | --- |
| `logo.png` | The emblem: a faceted nightjar with spread wings inside a hexagonal shield. 512px, alpha intact. | export at 512px from the master artwork |
| `demo.mp4` | The three-class walkthrough, 1:46, H.264 with `+faststart`. | `npm run demo:encode` in `apps/web` |
| `demo-preview.gif` | The nine-second loop the README embeds. GitHub plays GIF inline and does not play committed video. | same command |
| `dashboard.png` | The command deck after three incidents were filed. | `npm run screenshots` in `apps/web` |
| `case-file.png` | A case file at the moment it is waiting on a human decision. | same |
| `verification.png` | The predicate list at T+0 and again after the stability window. | same |
| `studio.png` | The CornerShop lab with a fault scenario selected. | `SHOTS_ONLY=studio npm run screenshots` |
| `settings.png` | Connection settings and the control-plane metadata it read back. | `SHOTS_ONLY=settings npm run screenshots` |

## Constraints worth keeping

- **`demo.mp4` stays under 3 MB.** The full capture is a 720p VP9 screen recording at
  roughly 16 MB per minute; CRF 28 lands at 2.3 MB with 9–11px interface text still
  legible. Raise `DEMO_CRF` only if a future recording has more motion than this one.
- **The GIF stays under 1.5 MB.** It is a 960px, 12fps, 128-colour loop built from three
  windows of the same capture, so it cannot drift from the video it previews.
- **Screenshots are 1440×900 at deviceScaleFactor 2.** That is the width the deck is
  designed for, and 2x keeps the ledger's monospace text sharp on a retina display.
- **The captures run with `NIGHTJAR_MODEL_NAME=mock-rules-v1`.** The default
  `gpt-5.4-mini` would make the header claim a model that did not produce the run.

## What is not committed

`demo-raw.webm` — the 16 MB Playwright capture — is git-ignored. Re-record it with
`npm run demo:record`, which needs the API, the web app and the lab connector running;
`apps/web/scripts/record-demo.mjs` documents the exact stack.

The scripts live in `apps/web/scripts/`: `record-demo.mjs`, `encode-demo.sh` and
`screenshots.mjs`. Each starts with the reason it exists, because the next person to
touch them will be deciding whether to keep them.
