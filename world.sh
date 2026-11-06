#!/usr/bin/env bash
# Own the opt-in live runtime in the `world` seat: bridge plus the 15-second renderer.
set -euo pipefail

WORLD_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# the Python the CLI runs on (it may be a venv's); a bare python3 only when run by hand
PYTHON=${CM_PYTHON:-python3}
# seconds between renders; the bridge reads the same variable to know how old a page may get
CONTAINER_RENDER_SECONDS=${CONTAINER_RENDER_SECONDS:-15}
if ! [[ $CONTAINER_RENDER_SECONDS =~ ^[0-9]+$ ]] || (( CONTAINER_RENDER_SECONDS < 1 )); then
  CONTAINER_RENDER_SECONDS=15
fi
export CONTAINER_RENDER_SECONDS
WORLD_BRIDGE_PID=''
WORLD_RENDER_PID=''

cleanup_world() {
  trap - EXIT INT TERM
  if [[ $WORLD_RENDER_PID =~ ^[0-9]+$ ]] && kill -0 "$WORLD_RENDER_PID" 2>/dev/null; then
    # a render in flight is the loop's child: stopping the loop alone would leave it running
    # (and, on Ctrl-C, printing after the prompt is back)
    render_children=$(pgrep -P "$WORLD_RENDER_PID" 2>/dev/null || true)
    kill "$WORLD_RENDER_PID" 2>/dev/null || true
    wait "$WORLD_RENDER_PID" 2>/dev/null || true
    # shellcheck disable=SC2086
    [[ -z $render_children ]] || kill $render_children 2>/dev/null || true
  fi
  if [[ $WORLD_BRIDGE_PID =~ ^[0-9]+$ ]] && kill -0 "$WORLD_BRIDGE_PID" 2>/dev/null; then
    kill "$WORLD_BRIDGE_PID" 2>/dev/null || true
    wait "$WORLD_BRIDGE_PID" 2>/dev/null || true
  fi
}
trap cleanup_world EXIT INT TERM

cd "$WORLD_DIR"
# where the bridge keeps its credentials: $BRIDGE_LIVE_PATH if set, else the per-root, per-port
# file it picks itself (config.default_live_path reads CONTAINER_ROOT and BRIDGE_PORT like it does)
if [[ -n ${BRIDGE_LIVE_PATH:-} ]]; then
  WORLD_LIVE_PATH=$BRIDGE_LIVE_PATH
else
  WORLD_LIVE_PATH=$("$PYTHON" -c 'import config; print(config.default_live_path())') \
    || WORLD_LIVE_PATH="$WORLD_DIR/.bridge-live"
fi
"$PYTHON" bridge.py &
WORLD_BRIDGE_PID=$!

ready=0
for _ in {1..100}; do
  if ! kill -0 "$WORLD_BRIDGE_PID" 2>/dev/null; then
    wait "$WORLD_BRIDGE_PID"
    exit $?
  fi
  if "$PYTHON" - "$WORLD_LIVE_PATH" "$WORLD_BRIDGE_PID" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding='utf-8') as handle:
        value = json.load(handle)
    raise SystemExit(0 if value.get('pid') == int(sys.argv[2]) else 1)
except (OSError, ValueError, TypeError):
    raise SystemExit(1)
PY
  then
    ready=1
    break
  fi
  sleep 0.1
done
if [[ $ready != 1 ]]; then
  echo 'world: bridge did not publish matching runtime metadata' >&2
  exit 1
fi

render_world() {
  # --loop-render: remember unchanged folders between cycles, and when nothing a render reads has
  # changed, only touch the page (the bridge serves it while it is fresh)
  while kill -0 "$WORLD_BRIDGE_PID" 2>/dev/null; do
    if ! "$PYTHON" render-game.py --loop-render; then
      echo 'world: render-game.py failed; retrying on the next cycle' >&2
    fi
    sleep "$CONTAINER_RENDER_SECONDS"
  done
}
render_world &
WORLD_RENDER_PID=$!

set +e
wait "$WORLD_BRIDGE_PID"
bridge_status=$?
set -e
exit "$bridge_status"
