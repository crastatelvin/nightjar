#!/usr/bin/env bash
# Encode the raw Playwright capture into the walkthrough committed at
# docs/assets/demo.webm.
#
# The raw capture is 1x 720p VP8 from Chromium's recorder at roughly 16 MB/min, and it
# is not what belongs in the repository. This is the second pass: VP9 at CRF 34, which
# keeps 9-11px interface text legible at about 3 MB for the full three-class run.
#
# Usage:
#   bash apps/web/scripts/encode-demo.sh [input] [output]
#
# DEMO_CRF overrides the quality (lower is better and larger; the default is 34).
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
input="${1:-$root/docs/assets/demo-raw.webm}"
output="${2:-$root/docs/assets/demo.webm}"
crf="${DEMO_CRF:-34}"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is required to encode the demo" >&2
  exit 1
fi
if [[ ! -f "$input" ]]; then
  echo "no capture at $input; run 'npm run demo:record' in apps/web first" >&2
  exit 1
fi

ffmpeg -y -v error -i "$input" \
  -an \
  -c:v libvpx-vp9 -crf "$crf" -b:v 0 \
  -row-mt 1 -deadline good -cpu-used 2 \
  "$output"

bytes=$(wc -c <"$output")
printf 'wrote %s (%.1f MB, crf %s)\n' "$output" "$(echo "$bytes" | awk '{print $1/1048576}')" "$crf"
