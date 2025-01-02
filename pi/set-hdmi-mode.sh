#!/usr/bin/env bash
# Pin HDMI to CEA 720p60 (74.25 MHz) and force it on without hot-plug detect.
#
#   sudo ./set-hdmi-mode.sh [--apply]
set -euo pipefail

F=/boot/firmware/cmdline.txt
[ -f "$F" ] || F=/boot/cmdline.txt
[ -f "$F" ] || { echo "no cmdline.txt found"; exit 1; }

old=$(cat "$F")
new=$(printf '%s' "$old" | sed -E 's#video=HDMI-A-1:[^ ]*#video=HDMI-A-1:1280x720@60De#')

if [ "$old" = "$new" ]; then
    echo "already set:"; printf '  %s\n' "$new"; exit 0
fi
if [ "$(printf '%s' "$new" | wc -l)" -ne 0 ]; then
    echo "refusing: result is not a single line"; exit 1
fi
case "$new" in
    *"video=HDMI-A-1:1280x720@60De"*) ;;
    *) echo "refusing: substitution did not produce the expected option"; exit 1 ;;
esac

echo "from: $old"
echo
echo "to:   $new"
if [ "${1:-}" != "--apply" ]; then
    echo
    echo "dry run. re-run with --apply to write it."
    exit 0
fi

cp "$F" "${F}.bak.$(date +%Y%m%d-%H%M%S)"
printf '%s\n' "$new" > "$F"
sync
echo
echo "written. backup alongside it. reboot for it to take effect."
