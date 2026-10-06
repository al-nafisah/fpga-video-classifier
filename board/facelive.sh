#!/usr/bin/env bash
# Start and stop the live face demo.
#
#   bash board/facelive.sh start     camera -> FPGA -> screen, until stopped
#   bash board/facelive.sh stop
#   bash board/facelive.sh status
#   bash board/facelive.sh log       follow it
#
# Root + venv + XRT, and not from /home/xilinx. See the README.
#
# Two things here are load-bearing, both inherited from runlive.sh.
#
# The stop and the start live in this file rather than on an ssh command line,
# because `pkill -f` matches the whole command line: a one-liner that both kills
# by name and launches by name matches, and kills, itself.
#
# The stop WAITS for the process to exit, and never escalates to SIGKILL.
# facedemo.py turns SIGTERM into an exception so its finally halts both VDMA
# channels, but that takes a moment. A killed video process leaves a VDMA
# writing 2.7 MB frames into CMA pages the kernel has already handed to
# something else; the symptom is unrelated processes segfaulting and a board
# that looks like failing hardware. Refusing to start is the safe failure.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PAT="[f]acedemo.py"
LOG=/tmp/facedemo.log

stop_it () {
    pgrep -f "$PAT" > /dev/null || { echo "not running"; return 0; }
    echo "stopping..."
    pkill -f "$PAT"
    for _ in $(seq 1 60); do                 # up to 30 s: a cycle can be mid-phase
        pgrep -f "$PAT" > /dev/null || { echo "stopped cleanly"; return 0; }
        sleep 0.5
    done
    echo "it will not exit. Check it by hand rather than SIGKILL, which leaves" >&2
    echo "the VDMA running and corrupts the CMA pool:  pgrep -af '$PAT'" >&2
    return 1
}

case "${1:-start}" in
  start)
    stop_it || exit 1
    if [ ! -f "${LPYOLO_DIR:-/home/xilinx/lpyolo}/bitfile/finn-accel.bit" ]; then
        echo "no LPYOLO bitstream at \${LPYOLO_DIR:-/home/xilinx/lpyolo}/bitfile/" >&2
        exit 1
    fi
    setsid --fork bash -c "cd '$ROOT' && exec python3 -u board/facedemo.py 0 > $LOG 2>&1 < /dev/null"
    echo "started. First frames reach the screen after ~40 s (two bitstream loads)."
    sleep 8
    pgrep -f "$PAT" > /dev/null || { echo "it died immediately:"; tail -20 $LOG; exit 1; }
    echo "follow it with:  bash board/facelive.sh log"
    ;;
  stop)   stop_it ;;
  status)
    if pgrep -f "$PAT" > /dev/null; then
        echo "running:"; pgrep -af "$PAT"
        echo "--- last lines ---"; tail -5 $LOG 2>/dev/null
    else
        echo "not running"
    fi
    ;;
  log)    tail -f $LOG ;;
  *)      echo "usage: $0 {start|stop|status|log}" >&2; exit 2 ;;
esac
