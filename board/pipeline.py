"""HDMI in, cnv-w1a1, HDMI out, on one bitstream."""

import os
import signal
import sys
import time

import cv2
import numpy as np

from accel import Accelerator
from live import CLASSES

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECONDS = 0

# 704 = 22 * 32: INTER_AREA is twice as fast on a whole ratio (16 ms vs 32 ms)
CROP = 704


def to_nn_input(frame):
    top = (frame.shape[0] - CROP) // 2
    left = (frame.shape[1] - CROP) // 2
    square = frame[top:top + CROP, left:left + CROP]
    small = cv2.resize(square, (32, 32), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    return np.ascontiguousarray(rgb, dtype=np.uint8).reshape(1, 32, 32, 3)


def stop_on_sigterm():
    # a VDMA left running after exit writes into freed memory and crashes the board
    def handler(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGHUP, handler)


def run(bitfile, seconds=SECONDS):
    from pynq import Overlay
    from pynq.lib.video import PIXEL_BGR

    stop_on_sigterm()
    overlay = Overlay(bitfile)
    accel = Accelerator(overlay)

    source = overlay.video.hdmi_in
    screen = overlay.video.hdmi_out
    for side in (source, screen):
        if getattr(side, "_vdma", None) is None:
            side._vdma = overlay.video.axi_vdma

    source.configure(PIXEL_BGR)
    source.start()
    print("HDMI in: %s" % source.mode)

    screen.configure(source.mode, PIXEL_BGR)
    screen.start()
    print("HDMI out: %s" % screen.mode)

    read = infer = draw = 0.0
    frames = 0
    label = "?"
    start = time.perf_counter()
    try:
        while True:
            t0 = time.perf_counter()
            frame = source.readframe()

            t1 = time.perf_counter()
            label = CLASSES[accel.predict(to_nn_input(frame))]

            t2 = time.perf_counter()
            cv2.putText(frame, label, (24, 56), cv2.FONT_HERSHEY_SIMPLEX,
                        1.6, (0, 255, 0), 3)
            screen.writeframe(frame)

            t3 = time.perf_counter()
            read += t1 - t0
            infer += t2 - t1
            draw += t3 - t2
            frames += 1

            if frames == 30:
                total = read + infer + draw
                print("%-11s read %5.1f ms  infer %5.1f ms  out %5.1f ms  %4.1f fps"
                      % (label, read / 30 * 1e3, infer / 30 * 1e3,
                         draw / 30 * 1e3, 30 / total))
                read = infer = draw = 0.0
                frames = 0
            if seconds and time.perf_counter() - start > seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        source.stop()
        screen.stop()
        accel.close()


if __name__ == "__main__":
    default = os.path.join(ROOT, "bitstream", "finn_hdmi.bit")
    bitfile = sys.argv[1] if len(sys.argv) > 1 else default
    run(bitfile, int(sys.argv[2]) if len(sys.argv) > 2 else SECONDS)
