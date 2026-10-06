"""Run LPYOLO over still images and save annotated copies.

    cd ~/camnn && python3 board/detect_still.py [glob]

Root + venv + XRT. Defaults to the frames facedemo.py captured into $LPYOLO_DIR.
Uses the same Accelerator as the live demo, so it runs at the verified 125 MHz.
"""
import glob
import os
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))   # repo root, for finn_examples/
sys.path.insert(0, HERE)

import accel_lpyolo                          # noqa: E402
import lpyolo                                # noqa: E402

accel = accel_lpyolo.Accelerator(batch=1)
scale = float(np.load(accel_lpyolo.SCALE_NPY))
pattern = sys.argv[1] if len(sys.argv) > 1 else os.path.join(accel_lpyolo.ASSETS, "frame_*.png")

for path in sorted(glob.glob(pattern)):
    frame = cv2.imread(path)
    if frame is None:
        continue
    t0 = time.perf_counter()
    lb, r, pad = lpyolo.letterbox(frame)
    rgb = np.ascontiguousarray(cv2.cvtColor(lb, cv2.COLOR_BGR2RGB), np.uint8)
    t1 = time.perf_counter()
    raw = accel.infer(rgb.reshape(1, 416, 416, 3))
    t2 = time.perf_counter()
    boxes = lpyolo.unletterbox(lpyolo.detect(raw[0][None], scale), r, pad, frame.shape)
    t3 = time.perf_counter()

    cv2.imwrite(path.rsplit(".", 1)[0] + "_det.png", lpyolo.draw(frame.copy(), boxes))
    print("%-30s %2d face(s)  pre %5.1f  infer %5.1f  post %4.1f ms  conf %s"
          % (os.path.basename(path), len(boxes), (t1 - t0) * 1e3, (t2 - t1) * 1e3,
             (t3 - t2) * 1e3, np.round(boxes[:, 4], 2).tolist() if len(boxes) else "-"))
