#!/usr/bin/env bash
# door.sh — open a door: start a seat whose working directory is an area of the container.
#
#   door.sh <area|path> [--as <worker>] [--dry-run]
#
# <area>  a name from areas.json (a subdirectory of the work root, "~name" for $HOME/name) or
# any directory path. The seat is a new tmux window in the container's tmux session
# ($CONTAINER_TMUX_SESSION, else the session this shell is in), named after the area, started IN
# that directory, running the worker's command from areas.json (or a plain shell when no
# worker is assigned). A Claude-style harness started there loads that directory's
# CLAUDE.md / AGENTS.md on its own: that is what makes the door a door.
#
# This script NEVER types into the new window and never sends a prompt. It opens the door
# and prints what it did. The game panel shows this exact command; nothing in the game runs
# it on your behalf yet (that is the bridge's job, in a later slice, and it will exec this
# file rather than reimplement it).
set -euo pipefail

HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
AREAS=${CONTAINER_AREAS_JSON:-$HERE/areas.json}
WORK_ROOT=${CONTAINER_WORK_ROOT:-$HOME/work}
SESSION=${CONTAINER_TMUX_SESSION:-$(tmux display-message -p '#S' 2>/dev/null || true)}
[[ -n $SESSION ]] || { echo "door: no tmux session (set CONTAINER_TMUX_SESSION or run inside tmux)" >&2; exit 1; }
target=${1:-}; shift || true
worker_override=""; dry=0
while [[ $# -gt 0 ]]; do
  case $1 in
    --as) worker_override=${2:-}; shift 2;;
    --dry-run) dry=1; shift;;
    *) echo "door: unknown argument $1" >&2; exit 2;;
  esac
done
[[ -n $target ]] || { echo "usage: door.sh <area|path> [--as <worker>] [--dry-run]" >&2; exit 2; }

# resolve the area name to a path exactly as districts.py does
case $target in
  '~'*)      path="$HOME/${target#\~}"; name=${target#\~};;
  /*)        path=$target; name=$(basename -- "$target");;
  *)         path="$WORK_ROOT/$target"; name=$target;;
esac
[[ -d $path ]] || { echo "door: no such directory: $path" >&2; exit 1; }

read -r worker cmd < <(python3 - "$AREAS" "$target" "$worker_override" <<'PY'
import json, sys
areas_path, target, override = sys.argv[1:4]
try:
    cfg = json.load(open(areas_path))
except Exception:
    cfg = {"workers": {}, "areas": {}}
area = cfg.get("areas", {}).get(target, {}) if isinstance(cfg.get("areas"), dict) else {}
worker = override or area.get("worker") or ""
cmd = cfg.get("workers", {}).get(worker, {}).get("cmd", "") if worker else ""
if worker and not cmd:
    print("", "")
    sys.exit(0)
print(worker or "-", cmd or "-")
PY
)
if [[ -z ${worker:-} ]]; then
  echo "door: worker is not in areas.json workers" >&2; exit 1
fi
[[ $cmd == '-' ]] && cmd=''
[[ $worker == '-' ]] && worker=''
window="door-${name}"
if [[ -n $cmd ]]; then
  [[ -x $cmd ]] || { echo "door: worker command is not executable: $cmd" >&2; exit 1; }
  desc="as $worker ($cmd)"
else
  desc="as a plain shell (no worker assigned in areas.json)"
fi
if (( dry )); then
  echo "would open tmux window '$window' in $path $desc"
  exit 0
fi
tmux has-session -t "$SESSION" 2>/dev/null || { echo "door: tmux session '$SESSION' is not running" >&2; exit 1; }
if [[ -n $cmd ]]; then
  tmux new-window -t "$SESSION" -n "$window" -c "$path" -- "$cmd"
else
  tmux new-window -t "$SESSION" -n "$window" -c "$path"
fi
echo "opened tmux window '$window' in $path $desc"
