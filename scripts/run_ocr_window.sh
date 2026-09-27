#!/bin/bash
# One OCR window: serve GLM-OCR with the box's own llama.cpp, work the `ocr_page` queue for a
# bounded time, stop the server. Then the box is idle again for the text model.
#
# Why a script rather than a flag on `rlm mine run`: this box holds **one model at a time**, so a
# window has to *own* the model — start it, use it, stop it. The CLI does the queue work; this
# owns the server's lifetime. Measured 2026-09-26: GLM-OCR Q8_0 + mmproj load in ~4 s and take
# 1.7-2.5 GiB of RSS, and a page costs ~3.6 minutes at 96 DPI on these four cores
# (`docs/20260927-0400-...`), so the window length is the operator's, not the task's.
#
# Usage (on lunacode):
#   scripts/run_ocr_window.sh --for 2h [--dpi 96] [--max-items 20] [--corpus-index PATH]
#
# It refuses to start while another llama-server is serving: two models on 15 GiB is the
# swap-storm the owner already ruled out.
set -u

MODEL_DIR="${RLM_OCR_MODEL_DIR:-$HOME/Misc/quantized_multimodal/models/glm-ocr}"
MODEL_FILE="${RLM_OCR_MODEL:-GLM-OCR-Q8_0.gguf}"
MMPROJ_FILE="${RLM_OCR_MMPROJ:-mmproj-GLM-OCR-Q8_0.gguf}"
LLAMA_BIN="${RLM_LLAMA_SERVER:-$HOME/Sources/llama.cpp/build/bin/llama-server}"
CORPUS_ROOT="${RLM_CORPUS_ROOT:-/srv/corpus}"
CORPUS_INDEX="${RLM_CORPUS_INDEX:-$HOME/rlm-derived/corpus.sqlite}"
PORT="${RLM_OCR_PORT:-55845}"
DPI=150
WINDOW="1h"
MAX_ITEMS=""
FORCE=0
# Measured 2026-09-26: capping the *image* tokens makes a 150-DPI page cost what a 96-DPI page
# costs (187 s against 600 s) while the model still receives the 150-DPI rendering, and the
# transcription of the test page came out identical. 0 disables the cap.
MAX_IMAGE_TOKENS="${RLM_OCR_MAX_IMAGE_TOKENS:-1024}"

while [ $# -gt 0 ]; do
  case "$1" in
    --for) WINDOW="$2"; shift 2 ;;
    --dpi) DPI="$2"; shift 2 ;;
    --max-items) MAX_ITEMS="--max-items $2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --corpus-index) CORPUS_INDEX="$2"; shift 2 ;;
    --image-max-tokens) MAX_IMAGE_TOKENS="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
EXTRA=""
[ "$MAX_IMAGE_TOKENS" -gt 0 ] 2>/dev/null && EXTRA="--image-max-tokens $MAX_IMAGE_TOKENS"

for f in "$MODEL_DIR/$MODEL_FILE" "$MODEL_DIR/$MMPROJ_FILE" "$LLAMA_BIN"; do
  [ -e "$f" ] || { echo "missing: $f" >&2; exit 2; }
done

# One model at a time: the *router* is always running, so counting processes is the wrong test.
# What matters is a **resident model** — the router keeps every model it has served in memory —
# because a second one on 15 GiB is the swap-storm the owner already ruled out. So the test is
# RSS: anything large that is not the router itself. `--force` is the explicit override.
resident=""
for pid in $(pgrep -f '[l]lama-server' 2>/dev/null); do
  rss_kb=$(awk '/VmRSS/ {print $2}' "/proc/$pid/status" 2>/dev/null || echo 0)
  [ -n "$rss_kb" ] || rss_kb=0
  if [ "$rss_kb" -gt 512000 ]; then
    resident="$resident pid=$pid rss=$((rss_kb/1024))MiB"
  fi
done
if [ -n "$resident" ] && [ "$FORCE" -ne 1 ]; then
  echo "refusing to start: a model is already resident:$resident" >&2
  echo "This box holds one model at a time. Drop it (restart the router service) or pass --force." >&2
  exit 3
fi
[ -n "$resident" ] && echo "warning: starting anyway, with a model resident:$resident"

WORK=$(mktemp -d /tmp/rlm-ocr-window.XXXXXX)
cleanup() {
  [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null
  sleep 1
  rm -rf "$WORK"
}
trap cleanup EXIT

export LD_LIBRARY_PATH="$(dirname "$LLAMA_BIN")"
"$LLAMA_BIN" -m "$MODEL_DIR/$MODEL_FILE" --mmproj "$MODEL_DIR/$MMPROJ_FILE" \
  --host 127.0.0.1 --port "$PORT" -c 8192 --threads 4 --threads-batch 4 --no-warmup --jinja \
  $EXTRA > "$WORK/server.log" 2>&1 &
SERVER_PID=$!

ready=0
for _ in $(seq 1 300); do
  if grep -qE 'model loaded|listening on http' "$WORK/server.log" 2>/dev/null; then ready=1; break; fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  echo "the OCR server did not load; last lines:" >&2
  tail -5 "$WORK/server.log" >&2
  exit 4
fi
echo "glm-ocr serving on 127.0.0.1:$PORT (rss $(ps -o rss= -p "$SERVER_PID" | awk '{printf "%.2f GiB", $1/1048576}')) image_max_tokens=${MAX_IMAGE_TOKENS:-none}"

cd "$(dirname "$0")/.." || exit 1
uv run python -m rlm_local.cli mine run \
  --corpus-root "$CORPUS_ROOT" --corpus-index "$CORPUS_INDEX" \
  --tasks ocr_page --for "$WINDOW" $MAX_ITEMS \
  --ocr-endpoint "http://127.0.0.1:$PORT" --ocr-model glm-ocr --ocr-dpi "$DPI" \
  --no-coverage-scan
status=$?
echo "window exit=$status"
exit $status
