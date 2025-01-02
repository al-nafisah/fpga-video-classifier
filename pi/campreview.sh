#!/usr/bin/env bash
# Camera to this Pi's HDMI output. 16:9 so the preview covers the console.
exec rpicam-hello -t 0 --fullscreen \
     --width 1280 --height 720 --framerate 30 \
     --info-text ""
