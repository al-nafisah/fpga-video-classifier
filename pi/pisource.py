"""Runs on the Pi that feeds the board's HDMI input.

    python3 pisource.py           # cameras, and the HDMI mode actually being sent
    python3 pisource.py shot
    python3 pisource.py stream
    python3 pisource.py serve     # ffplay tcp://<pi>:8888
"""

import glob
import os
import re
import subprocess
import sys


def run(*cmd):
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def read(*parts):
    try:
        with open(os.path.join(*parts)) as handle:
            return handle.read().strip()
    except OSError:
        return ""


def csi_cameras():
    out = run("rpicam-hello", "--list-cameras") or run("libcamera-hello", "--list-cameras")
    if not out or "No cameras available" in out:
        return []
    return [line.strip() for line in out.splitlines() if re.match(r"\s*\d+\s*:", line)]


def usb_cameras():
    found = []
    for path in sorted(glob.glob("/sys/class/video4linux/video*")):
        vendor = read(path, "device", "..", "idVendor")
        if vendor:
            found.append("%s  %s  [usb %s:%s]" % (
                os.path.basename(path), read(path, "name"),
                vendor, read(path, "device", "..", "idProduct")))
    return found


def active_mode():
    # Crtc 3 (97) 1280x720@60.00 74.250 1280/110/40/220/+ 720/5/5/20/+ 60 ...
    out = run("kmsprint")
    if not out:
        return None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Crtc ") and "@" in line:
            parts = line.split()[2:]
            if parts and parts[0].startswith("("):
                parts = parts[1:]
            return " ".join(parts)
    return None


def framebuffer():
    return read("/sys/class/graphics/fb0/virtual_size").replace(",", "x")


def hdmi_state():
    for path in sorted(glob.glob("/sys/class/drm/card*-HDMI-A-*")):
        modes = read(path, "modes").split()
        yield (os.path.basename(path), read(path, "status"),
               read(path, "enabled"), read(path, "dpms"),
               modes[0] if modes else "no EDID from the sink")


def desktop_env():
    # an ssh session has no display; point rpicam at the desktop's
    env = dict(os.environ)
    if not env.get("WAYLAND_DISPLAY") and not env.get("DISPLAY"):
        env["XDG_RUNTIME_DIR"] = "/run/user/%d" % os.getuid()
        env["WAYLAND_DISPLAY"] = "wayland-0"
    env["QT_QPA_PLATFORM"] = "wayland"
    return env


def compositor_running():
    return any(os.path.exists(p) and not p.endswith(".lock")
               for p in glob.glob("/run/user/%d/wayland-*" % os.getuid()))


def screen_size(default="1280x720"):
    for _, status, mode in hdmi_state():
        if status == "connected" and "x" in mode:
            return mode
    return default


def main(action):
    if action == "list":
        for line in csi_cameras() or ["none"]:
            print("csi    %s" % line)
        for line in usb_cameras() or ["none"]:
            print("usb    %s" % line)
        for name, status, enabled, dpms, mode in hdmi_state():
            print("hdmi   %s %s/%s/dpms %s, sink offers %s"
                  % (name, status, enabled, dpms, mode))
        print("sending %s" % (active_mode() or "nothing - no crtc is driving a mode"))
        print("fb     %s" % (framebuffer() or "none"))
        print("cmdline %s" % read("/proc/cmdline"))
        return

    if not csi_cameras():
        sys.exit("no CSI camera; check the ribbon seating and `pisource.py` output")
    if action == "shot":
        subprocess.run(["rpicam-still", "-n", "-t", "1000", "-o", "shot.jpg"], check=True)
        print("wrote shot.jpg (%d bytes)" % os.path.getsize("shot.jpg"))
    elif action == "stream":
        if compositor_running():
            width, height = screen_size().split("x")
            subprocess.run(["rpicam-hello", "-t", "0", "--qt-preview",
                            "-p", "0,0,%s,%s" % (width, height)],
                           env=desktop_env(), check=True)
        else:
            subprocess.run(["rpicam-hello", "-t", "0", "-f"], check=True)
    elif action == "serve":
        subprocess.run(["rpicam-vid", "-t", "0", "--inline", "--width", "1296",
                        "--height", "972", "--framerate", "30",
                        "--listen", "-o", "tcp://0.0.0.0:8888"], check=True)


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "list"
    if action not in ("list", "shot", "stream", "serve"):
        sys.exit("usage: pisource.py [list|shot|stream|serve]")
    main(action)
