#!/bin/bash
# Speedup suite, highest-value stages first so a short window still pays.
#
#   cd ~/camnn && bash board/run_speed_suite.sh
#
# Root + venv + XRT, and not from /home/xilinx. Each stage is a fresh process
# with its own bitstream download, and a failure ABORTS the run rather than
# cascading: a stage that times out leaves the output DMA armed, and the next
# stage would then run against a wedged accelerator.
set -uo pipefail
cd "$(dirname "$0")"
RESULTS="${LPYOLO_DIR:-/home/xilinx/lpyolo}/results.jsonl"
: > "$RESULTS"

run () {  # run <label> <stage-args...>
  echo "--- $1 ---"
  shift
  # The status must come from python, not from the filter it is piped through.
  # Piping to grep and testing $? tests grep, which succeeds even when the stage
  # died -- that is exactly how an earlier version reported eight clean-looking
  # failures in a row and then printed an empty results file.
  timeout 300 python3 speed_suite.py "$@" 2>&1 | grep -vi 'warn'
  local rc=${PIPESTATUS[0]}
  if [ "$rc" -ne 0 ]; then
    echo "STAGE FAILED (exit $rc): $* -- aborting before the next stage runs"
    exit 1
  fi
}

run "golden @100MHz (reference outputs)"        golden repo
run "batch 1 @100 (baseline)"                   exact  repo 100 1
run "batch 6 @100 (is batching bit-exact?)"     exact  repo 100 6
run "batch 1 @125 (is 125MHz bit-exact?)"       exact  repo 125 1
run "batch 6 @125 (both together)"              exact  repo 125 6
run "batch 3 @125"                              exact  repo 125 3
run "frame packing @125 (2 frames/inference)"   pack   repo 125
run "async overlap @125"                        async  repo 125 1
echo
echo "=== results ==="
cat "$RESULTS"
