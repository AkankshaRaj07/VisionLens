#!/usr/bin/env bash
# run.sh — Process all CCTV clips and feed events into the API
# Usage: bash pipeline/run.sh [--api-url http://localhost:8000]
set -euo pipefail

API_URL="${1:-}"
CLIPS_DIR="data/clips"
EVENTS_DIR="data/events"
LAYOUT="data/store_layout.json"

mkdir -p "$EVENTS_DIR"

if [ ! -d "$CLIPS_DIR" ]; then
  echo "ERROR: $CLIPS_DIR not found. Place your CCTV clips there first."
  exit 1
fi

process_clip() {
  local store_id="$1"
  local camera_id="$2"
  local camera_type="$3"
  local clip_path="$4"

  local output="$EVENTS_DIR/${store_id}_${camera_id}.jsonl"
  echo "▶ Processing $clip_path → $output"

  local cmd="python -m pipeline.detect \
    --clip \"$clip_path\" \
    --store-id \"$store_id\" \
    --camera-id \"$camera_id\" \
    --camera-type \"$camera_type\" \
    --layout \"$LAYOUT\" \
    --output \"$output\""

  if [ -n "$API_URL" ]; then
    cmd="$cmd --api-url \"$API_URL\""
  fi

  eval "$cmd"
}

# ── Iterate over clips directory structure ───────────────────────────────────
# Expected layout: data/clips/STORE_BLR_002/CAM_ENTRY_01.mp4
for store_dir in "$CLIPS_DIR"/*/; do
  store_id=$(basename "$store_dir")
  echo ""
  echo "═══ Store: $store_id ═══"

  for clip in "$store_dir"*.mp4 "$store_dir"*.avi 2>/dev/null; do
    [ -f "$clip" ] || continue
    filename=$(basename "$clip" | sed 's/\.[^.]*$//')

    # Infer camera type from filename
    if echo "$filename" | grep -qi "ENTRY\|entry"; then
      camera_type="entry"
    elif echo "$filename" | grep -qi "BILLING\|billing\|BILL"; then
      camera_type="billing"
    else
      camera_type="floor"
    fi

    process_clip "$store_id" "$filename" "$camera_type" "$clip"
  done
done

echo ""
echo "✅ All clips processed. Events in $EVENTS_DIR/"

echo "▶ Correlating POS transactions..."
# Find the first available POS/transaction CSV file in data directory
POS_FILE=$(ls data/*.csv 2>/dev/null | grep -iE "pos|brigade" | head -n 1 || true)
if [ -z "$POS_FILE" ]; then
  POS_FILE="data/pos_transactions.csv"
fi

if [ -n "$API_URL" ]; then
  python pipeline/correlate_pos.py --events-dir "$EVENTS_DIR" --pos-file "$POS_FILE" --api-url "$API_URL"
else
  python pipeline/correlate_pos.py --events-dir "$EVENTS_DIR" --pos-file "$POS_FILE"
fi

# If API_URL not set, offer to ingest now
if [ -z "$API_URL" ]; then
  echo ""
  echo "To ingest events into the API, run:"
  echo "  bash pipeline/run.sh http://localhost:8000"
  echo ""
  echo "Or replay events manually:"
  echo "  python pipeline/replay.py --events-dir data/events --api-url http://localhost:8000"
fi
