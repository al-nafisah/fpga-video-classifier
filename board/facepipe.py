"""Camera -> LPYOLO -> screen, continuously, on one bitstream.

This is what facedemo.py could not do. There, the detector and the video
pipeline were different bitstreams and the PL holds one, so the demo alternated:
live video with boxes frozen from the previous cycle, then twenty seconds of
black screen while the accelerator was loaded. The box never followed a face
because the detector was not in the chip while you were watching.

lpyolo_hdmi.bit carries both. Every frame is captured, letterboxed, run through
the accelerator, annotated and displayed, in one loop, with nothing swapped and
no gap.

Root + venv + XRT, and not from /home/xilinx:

    sudo -s
    source /etc/profile.d/pynq_venv.sh
    source /etc/profile.d/xrt_setup.sh
    cd ~/camnn && python3 board/facepipe.py [seconds]
"""

import collections
import os
import signal
import sys
import time

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "board"))

import lpyolo                                    # noqa: E402
from accel_face import Accelerator               # noqa: E402

BIT = os.path.join(ROOT, "bitstream", "lpyolo_hdmi.bit")
SCALE = 1.0 / 127.0
SECONDS = 0                                      # 0 runs until interrupted


def stop_on_sigterm():
    """Turn SIGTERM into an exception so the VDMA is stopped on the way out.

    Not tidiness. Python's default SIGTERM handling exits without unwinding, so
    a kill leaves both VDMA channels running with no owner; the kernel returns
    the frame buffers to the CMA pool, the VDMA keeps writing 2.7 MB frames into
    those pages, and hands them out as heap to whatever starts next. The symptom
    is unrelated processes dying, not a broken video pipeline.
    """
    def handler(signum, frame):
        raise KeyboardInterrupt
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, handler)


def run(bitfile=BIT, seconds=SECONDS):
    from pynq import Overlay
    from pynq.lib.video import PIXEL_BGR

    stop_on_sigterm()
    overlay = Overlay(bitfile)
    accel = Accelerator(overlay)

    src = overlay.video.hdmi_in
    screen = overlay.video.hdmi_out
    # HDMIWrapper only binds when the overlay looks like the base overlay's
    # video subsystem; this one also carries an accelerator, so hand the VDMA
    # down rather than rely on it.
    for side in (src, screen):
        if getattr(side, "_vdma", None) is None:
            side._vdma = overlay.video.axi_vdma

    src.configure(PIXEL_BGR)
    src.start()
    print("HDMI in : %s" % src.mode, flush=True)
    screen.configure(src.mode, PIXEL_BGR)
    screen.start()
    print("HDMI out: %s" % screen.mode, flush=True)

    stamps = collections.deque(maxlen=31)
    read = pre = infer = post = 0.0
    frames = 0
    hud = "measuring"
    start = time.perf_counter()
    try:
        while True:
            t0 = time.perf_counter()
            frame = src.readframe()

            t1 = time.perf_counter()
            lb, r, pad = lpyolo.letterbox(frame)
            rgb = np.ascontiguousarray(cv2.cvtColor(lb, cv2.COLOR_BGR2RGB), np.uint8)

            t2 = time.perf_counter()
            raw = accel.infer(rgb.reshape(1, 416, 416, 3))

            t3 = time.perf_counter()
            boxes = lpyolo.unletterbox(lpyolo.detect(raw, SCALE), r, pad, frame.shape)
            for x1, y1, x2, y2, conf in boxes:
                cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
                cv2.putText(frame, "%.2f" % conf, (int(x1), max(int(y1) - 6, 14)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            # One short line on a filled rectangle. A long string drawn twice for
            # an outline costs 7.1 ms a frame, measured -- more than the rest of
            # the annotation put together.
            cv2.rectangle(frame, (16, 20), (16 + 13 * len(hud), 56), (0, 0, 0), -1)
            cv2.putText(frame, hud, (24, 46), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
            # readframe hands over ownership of a VDMA buffer and writeframe
            # takes ownership of one, so the frame crosses without a 2.7 MB copy.
            screen.writeframe(frame)

            t4 = time.perf_counter()
            read += t1 - t0
            pre += t2 - t1
            infer += t3 - t2
            post += t4 - t3
            frames += 1
            stamps.append(t4)
            if len(stamps) > 1 and stamps[-1] > stamps[0]:
                hud = "%.1f fps  %d face(s)" % (
                    (len(stamps) - 1) / (stamps[-1] - stamps[0]), len(boxes))

            if frames % 60 == 0:
                total = read + pre + infer + post
                print("read %5.1f  pre %5.1f  infer %5.1f  post %5.1f ms  %5.1f fps"
                      % (read / 60 * 1e3, pre / 60 * 1e3, infer / 60 * 1e3,
                         post / 60 * 1e3, 60 / total), flush=True)
                read = pre = infer = post = 0.0
            if seconds and time.perf_counter() - start > seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        src.stop()
        screen.stop()
        accel.close()
        print("stopped cleanly: both VDMA channels halted", flush=True)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".bit") else BIT,
        int(sys.argv[-1]) if sys.argv[-1].isdigit() else SECONDS)
