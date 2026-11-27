#!/usr/bin/env bash
# Encode the raw Playwright capture into the walkthrough committed at
# docs/assets/demo.mp4, plus the short loop the README embeds.
#
# The raw capture is 1x 720p VP8 from Chromium's recorder at roughly 16 MB/min. H.264
# at CRF 28 keeps 9-11px interface text legible at about 2.5 MB for the full
# three-class run, and every browser plays it in the file view. A committed .webm
# turns into a download link on some clients, which is exactly the failure this
# replaces.
#
# GIF is the only format GitHub renders inside a README, so the preview loop is the
# hero image and the mp4 is what it links to. The loop is three moments: the deck
# counting up, a decision waiting on a human, and a case closing on proof. The window
# offsets are tuned to the current recording; if you re-record with
# `npm run demo:record` and the pacing changes, adjust DEMO_PREVIEW_WINDOWS.
#
# Usage:
#   bash apps/web/scripts/encode-demo.sh [input] [output]
#
# DEMO_CRF overrides the video quality (lower is better and larger; default 28).
# DEMO_PREVIEW_WINDOWS is a comma-separated list of start:end seconds.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
input="${1:-$root/docs/assets/demo-raw.webm}"
output="${2:-$root/docs/assets/demo.mp4}"
preview="$root/docs/assets/demo-preview.gif"
crf="${DEMO_CRF:-28}"
windows="${DEMO_PREVIEW_WINDOWS:-2:5,29.4:32.4,93:96}"

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
  -c:v libx264 -preset slow -crf "$crf" -pix_fmt yuv420p -movflags +faststart \
  "$output"

# One filter graph builds the loop from the same capture, so the preview cannot drift
# from the video it is previewing.
filter=""
labels=""
index=0
IFS=',' read -ra parts <<<"$windows"
for part in "${parts[@]}"; do
  start="${part%%:*}"
  end="${part##*:}"
  filter+="[0:v]trim=start=${start}:end=${end},setpts=PTS-STARTPTS,fps=12,scale=960:-1:flags=lanczos[v${index}];"
  labels+="[v${index}]"
  index=$((index + 1))
done
filter+="${labels}concat=n=${index}:v=1:a=0[out]"

scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

ffmpeg -y -v error -i "$input" -filter_complex "$filter" -map "[out]" \
  -c:v libx264 -crf 18 -pix_fmt yuv420p "$scratch/preview.mp4"
ffmpeg -y -v error -i "$scratch/preview.mp4" \
  -vf "fps=12,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" \
  "$preview"

for file in "$output" "$preview"; do
  bytes=$(wc -c <"$file")
  printf 'wrote %s (%.1f MB)\n' "$file" "$(echo "$bytes" | awk '{print $1/1048576}')"
done
