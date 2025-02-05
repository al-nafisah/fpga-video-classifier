#!/usr/bin/env bash
# Restart pipeline.py detached. Never kill -9 it: the VDMA keeps running.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
BIT="${1:-bitstream/finn_hdmi.bit}"

if pgrep -f "[p]ipeline.py" > /dev/null; then
    pkill -f "[p]ipeline.py"
    for _ in $(seq 1 20); do
        pgrep -f "[p]ipeline.py" > /dev/null || break
        sleep 0.5
    done
    if pgrep -f "[p]ipeline.py" > /dev/null; then
        echo "previous pipeline will not exit; refusing to start a second one" >&2
        echo "check it by hand rather than SIGKILL, which leaves the VDMA running" >&2
        exit 1
    fi
fi

setsid --fork bash -c "cd '$ROOT' && exec python3 -u board/pipeline.py '$BIT' > /tmp/pipeline.log 2>&1 < /dev/null"
sleep 20
echo "--- pids ---"; pgrep -af "[p]ipeline.py" || echo "not running"
echo "--- log ---";  tail -4 /tmp/pipeline.log
