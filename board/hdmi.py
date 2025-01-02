"""HDMI bring-up without the accelerator.

    python3 board/hdmi.py [bars|motion|camera|probe|passthru|capture] [bitfile]
"""

import sys
import time

import cv2
import numpy as np

from live import open_camera, open_screen

BASE = "/usr/local/share/pynq-venv/lib/python3.10/site-packages/pynq/overlays/base/base.bit"

SECONDS = 20

# the base overlay's VDMA only reaches DDR from here up
WINDOW = 0x10000000


def start_screen(overlay):
    screen = open_screen(overlay)
    channel = screen._vdma.writechannel
    if not channel._mmio.read(0x04) & 0x1:
        return screen, []

    channel.reset()
    rejects = force_high_frames()
    return open_screen(overlay), rejects


def force_high_frames(window=WINDOW):
    # rejects stay allocated, or CMA hands the same low addresses back
    from pynq.lib.video import dma as videodma

    original = videodma._FrameCache.getframe
    rejects = []

    def getframe(self):
        for _ in range(400):
            frame = original(self)
            if frame.physical_address >= window:
                return frame
            rejects.append(frame)
        raise RuntimeError("no frame buffer above 0x%08x" % window)

    videodma._FrameCache.getframe = getframe
    return rejects


def bars(width, height):
    colours = [(255, 255, 255), (0, 255, 255), (255, 255, 0), (0, 255, 0),
               (255, 0, 255), (0, 0, 255), (255, 0, 0), (0, 0, 0)]
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    edge = width / len(colours)
    for i, colour in enumerate(colours):
        frame[:, int(i * edge):int((i + 1) * edge)] = colour
    cv2.rectangle(frame, (0, 0), (width - 1, height - 1), (0, 255, 0), 1)
    return frame


def pixel_clock_mhz(overlay, seconds=1.0):
    if "meter_gpio" not in overlay.ip_dict:
        return None
    from pynq import MMIO
    gpio = MMIO(overlay.ip_dict["meter_gpio"]["phys_addr"], 0x10000)
    first = gpio.read(0x00)
    start = time.perf_counter()
    time.sleep(seconds)
    elapsed = time.perf_counter() - start
    ticks = (gpio.read(0x00) - first) & 0xFFFFFFFF
    return ticks / elapsed / 1e6


def probe_input(overlay, samples=20, gap=0.3):
    # dvi2rgb's lock bit reads 1 on a floating input; VTC DTSTAT bit 0 is the real lock
    from pynq import MMIO

    ips = overlay.ip_dict
    gpio = MMIO(ips["video/hdmi_in/frontend/axi_gpio_hdmiin"]["phys_addr"], 0x10000)
    vtc = MMIO(ips["video/hdmi_in/frontend/vtc_in"]["phys_addr"], 0x10000)

    gpio.write(0x04, 0x0)   # channel 1 to outputs
    gpio.write(0x00, 0x1)   # assert hot-plug detect

    # register update + detector enable
    vtc.write(0x00, vtc.read(0x00) | 0x0A)
    time.sleep(1.0)

    seen = []
    for _ in range(samples):
        time.sleep(gap)
        seen.append({
            "lock": gpio.read(0x08) & 1,
            "dasize": vtc.read(0x20),
            "dtstat": vtc.read(0x24),
            "dhsize": vtc.read(0x30),
            "dvsize": vtc.read(0x34),
        })

    def active(v):
        return v & 0x3FFF, (v >> 16) & 0x3FFF

    last = seen[-1]
    width, height = active(last["dasize"])
    total_w, total_h = last["dhsize"] & 0x3FFF, last["dvsize"] & 0x3FFF

    detector_locked = all(s["dtstat"] & 1 for s in seen)
    stable = len({s["dasize"] for s in seen}) == 1
    plausible = 320 <= width <= 4096 and 240 <= height <= 2160
    framed = (total_w > width and total_h > height
              and total_w < width * 2 and total_h < height * 2)

    print("mmcm lock bit   %s   (means little: reads 1 on a floating input)"
          % sorted({s["lock"] for s in seen}))
    measured = pixel_clock_mhz(overlay)
    if measured is not None:
        print("pixel clock     %.3f MHz measured   (74.25 = 720p60 CEA, "
              "74.65 = 720p60 CVT, 25.18 = 640x480)" % measured)
    print("detector lock   %d of %d samples had DTSTAT bit 0 set"
          % (sum(s["dtstat"] & 1 for s in seen), samples))
    print("active          %dx%d   (DASIZE 0x%08x)" % (width, height, last["dasize"]))
    print("total frame     %dx%d   (DHSIZE 0x%08x DVSIZE 0x%08x)"
          % (total_w, total_h, last["dhsize"], last["dvsize"]))
    print("stability       %d distinct active sizes over %d samples"
          % (len({s["dasize"] for s in seen}), samples))

    if detector_locked and stable and plausible and framed:
        print("VERDICT         real video: %dx%d active in a %dx%d frame, detector "
              "locked on every sample" % (width, height, total_w, total_h))
        return True
    reasons = []
    if not detector_locked:
        reasons.append("the timing detector never reported lock")
    if not stable:
        reasons.append("the geometry drifts between reads")
    if not plausible:
        reasons.append("%dx%d is not a video mode" % (width, height))
    if not framed:
        reasons.append("a %dx%d frame cannot contain %dx%d of active video"
                       % (total_w, total_h, width, height))
    print("VERDICT         no video: %s" % "; ".join(reasons))
    return False


def open_input(overlay):
    if not hasattr(getattr(overlay, "video", None), "hdmi_in"):
        return None
    from pynq.lib.video import PIXEL_BGR
    source = overlay.video.hdmi_in
    if getattr(source, "_vdma", None) is None:
        source._vdma = overlay.video.axi_vdma
    source.configure(PIXEL_BGR)
    source.start()
    return source


def receive(overlay, mode):
    from pynq.lib.video import PIXEL_BGR

    source = open_input(overlay)
    if source is None:
        sys.exit("this bitstream has no hdmi_in")
    print("HDMI in locked: %s" % source.mode)

    screen = overlay.video.hdmi_out
    if getattr(screen, "_vdma", None) is None:
        screen._vdma = overlay.video.axi_vdma
    screen.configure(source.mode, PIXEL_BGR)
    screen.start()

    frames = 0
    start = time.perf_counter()
    try:
        if mode == "passthru":
            source.tie(screen)
            print("tied in the PL; frames are not passing through the PS")
            while time.perf_counter() - start < SECONDS:
                time.sleep(0.5)
        else:
            while time.perf_counter() - start < SECONDS:
                frame = source.readframe()
                screen.writeframe(frame)
                frames += 1
    except KeyboardInterrupt:
        pass
    finally:
        elapsed = time.perf_counter() - start
        if frames:
            print("%d frames in %.1f s, %.1f fps" % (frames, elapsed, frames / elapsed))
        source.stop()
        screen.stop()


def main(mode, bitfile):
    from pynq import Overlay

    overlay = Overlay(bitfile)
    if not hasattr(overlay, "video"):
        sys.exit("%s has no video hierarchy" % bitfile)
    if mode == "probe":
        return probe_input(overlay)
    if mode in ("passthru", "capture"):
        return receive(overlay, mode)
    screen, rejects = start_screen(overlay)
    width, height = screen.mode.width, screen.mode.height

    warm = [screen.newframe() for _ in range(5)]
    del warm
    cost = ("%d low frames held (%.0f MB)"
            % (len(rejects), len(rejects) * width * height * 3 / 2**20)
            if rejects else "whole of DDR reachable")
    print("HDMI out %dx%d at 0x%08x, %s"
          % (width, height, screen._vdma.writechannel._mmio.read(0x5C), cost))

    camera = None
    if mode == "camera":
        camera = open_camera()
    pattern = bars(width, height) if mode != "camera" else None

    frames = 0
    start = time.perf_counter()
    try:
        while time.perf_counter() - start < SECONDS:
            if mode == "camera":
                ok, frame = camera.read()
                if not ok:
                    continue
                if frame.shape[:2] != (height, width):
                    frame = cv2.resize(frame, (width, height))
            else:
                frame = pattern.copy() if mode == "motion" else pattern
                if mode == "motion":
                    x = int((time.perf_counter() * width) % width)
                    cv2.rectangle(frame, (x, 0), (min(x + 40, width), 40),
                                  (0, 0, 0), -1)
                    cv2.putText(frame, str(frames), (10, height - 20),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2)

            buffer = screen.newframe()
            buffer[:] = frame
            screen.writeframe(buffer)
            frames += 1
    except KeyboardInterrupt:
        pass
    finally:
        elapsed = time.perf_counter() - start
        print("%d frames in %.1f s, %.1f fps" % (frames, elapsed, frames / elapsed))
        if camera is not None:
            camera.release()
        screen.stop()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "bars"
    if mode not in ("bars", "motion", "camera", "passthru", "capture", "probe"):
        sys.exit("usage: hdmi.py [bars|motion|camera|passthru|capture|probe] [bitfile]")
    main(mode, sys.argv[2] if len(sys.argv) > 2 else BASE)
