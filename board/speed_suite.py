"""One stage of the speedup suite, per process. Usage: speed_suite.py <stage> [args]

Every stage runs in its OWN process with its OWN bitstream download, because
this board has already been lost twice to state that survives a re-download:
setting FCLK on a live design wedges the output DMA permanently, and 200 MHz
hung it hard enough to need a power cycle. Rules encoded here:

  * FCLK is only ever set through FINNExampleOverlay's fclk_mhz, which applies
    it immediately after the download (driver.py:107-108). Never on a live design.
  * Never above 142.86 MHz. 166.67 returned wrong bytes on all 36 runs and
    200 MHz never came back.
  * Every wait is bounded. driver.py:380-383 is an unbounded busy-poll.
  * A timeout aborts the whole suite rather than cascading into the next stage.

Stages, in descending value:
  golden  <tag>                  capture 100 MHz reference outputs
  exact   <tag> <clk> <bs>       prove (clock, batch) is byte-identical, and time it
  pack    <tag> <clk>            two frames per inference: does the seam change boxes?
  async   <tag> <clk> <bs>       overlap CPU preprocessing with the accelerator

The bitstream comes from $LPYOLO_DIR (see board/accel_lpyolo.py), not the
command line -- passing a path that silently expanded to an empty string was a
real failure here.
"""
import json
import os
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cv2
import lpyolo
from finn_examples.driver import FINNExampleOverlay
from qonnx.core.datatype import DataType
from pynq.ps import Clocks
from accel_lpyolo import ASSETS, BITFILE

# Buffers of a transfer that never finished are parked here and never freed --
# see the long note in board/accel_lpyolo.py. Freeing them lets the still-armed
# DMA scribble into recycled CMA pages, which corrupts the kernel's heap and
# takes the board down in a way only a power cycle fixes.
_QUARANTINE = []

G = os.path.join(ASSETS, "golden")
os.makedirs(G, exist_ok=True)
TIMEOUT_S = 8.0
SCALE = 1.0 / 127.0


def io_dict():
    return {
        "idt": [DataType["UINT8"]], "odt": [DataType["INT8"]],
        "ishape_normal": [(1, 416, 416, 3)], "oshape_normal": [(1, 13, 13, 18)],
        "ishape_folded": [(1, 416, 416, 1, 3)], "oshape_folded": [(1, 13, 13, 18, 1)],
        "ishape_packed": [(1, 416, 416, 1, 3)], "oshape_packed": [(1, 13, 13, 18, 1)],
        "input_dma_name": ["idma0"], "output_dma_name": ["odma0"],
        "number_of_external_weights": 0, "num_inputs": 1, "num_outputs": 1,
    }


def load(bit, clk, bs):
    return FINNExampleOverlay(
        bitfile_name=bit, platform="zynq-iodma", io_shape_dict=io_dict(),
        batch_size=bs, fclk_mhz=clk,
        runtime_weight_dir=os.path.join(ASSETS, "runtime_weights/"))


def infer(a, batch, nalloc):
    n = batch.shape[0]
    if n < nalloc:
        batch = np.concatenate(
            [batch, np.zeros((nalloc - n,) + batch.shape[1:], batch.dtype)], 0)
    a.copy_input_data_to_device(
        a.pack_input(a.fold_input(np.ascontiguousarray(batch, np.uint8))))
    a.execute_on_buffers(asynch=True)
    t0 = time.perf_counter()
    while a.odma[0].read(0x00) & 0x2 == 0:
        if time.perf_counter() - t0 > TIMEOUT_S:
            _QUARANTINE.append((a.ibuf_packed_device, a.obuf_packed_device))
            a.ibuf_packed_device = None
            a.obuf_packed_device = None
            raise TimeoutError("odma never signalled done -- buffers quarantined; "
                               "REBOOT before running anything else")
    a.copy_output_data_from_device(a.obuf_packed[0])
    return np.asarray(a.unfold_output(a.unpack_output(a.obuf_packed[0])))[:n]


def corpus():
    rng = np.random.RandomState(12345)
    c = [np.zeros((416, 416, 3), np.uint8), np.full((416, 416, 3), 255, np.uint8)]
    for _ in range(6):
        c.append(rng.randint(0, 256, (416, 416, 3)).astype(np.uint8))
    img = cv2.imread(os.path.join(ASSETS, "sample.png"))
    if img is not None:
        c.append(np.ascontiguousarray(cv2.cvtColor(
            cv2.resize(img, (416, 416), interpolation=cv2.INTER_LINEAR), cv2.COLOR_BGR2RGB)))
    # Real camera frames matter here. Zeros, saturated and uniform-random do not
    # exercise the same activations as a photograph -- random noise in particular
    # keeps every layer far from the saturation and near-zero regions where a
    # marginal setup path is most likely to flip. A clock that is "exact" only on
    # synthetic data has not been tested on the data it will actually run on.
    for f in real_frames()[:6]:
        lb, _, _ = lpyolo.letterbox(f)
        c.append(np.ascontiguousarray(cv2.cvtColor(lb, cv2.COLOR_BGR2RGB), np.uint8))
    return np.stack(c, 0)


def real_frames():
    d = "/dev/shm/facedemo/raw"
    if os.path.isdir(d) and os.listdir(d):
        return [np.load(os.path.join(d, f)) for f in sorted(os.listdir(d))]
    return []


def emit(rec):
    print(json.dumps(rec))
    with open(os.path.join(ASSETS, "results.jsonl"), "a") as f:
        f.write(json.dumps(rec) + "\n")


stage = sys.argv[1]

if stage == "golden":
    tag = sys.argv[2]
    a = load(BITFILE, 100.0, 1)
    c = corpus()
    out = np.stack([infer(a, c[i:i + 1], 1)[0] for i in range(c.shape[0])], 0)
    np.save(G + "/in.npy", c)
    np.save(G + "/%s_100.npy" % tag, out)
    # determinism at the reference clock itself
    ok = all(np.array_equal(infer(a, c[i:i + 1], 1)[0], out[i]) for i in range(c.shape[0]))
    emit({"stage": "golden", "tag": tag, "n": int(c.shape[0]), "deterministic": bool(ok)})

elif stage == "exact":
    tag, clk, bs = sys.argv[2], float(sys.argv[3]), int(sys.argv[4])
    c = np.load(G + "/in.npy")
    g = np.load(G + "/%s_100.npy" % tag)
    a = load(BITFILE, clk, bs)
    bad = tot = 0
    for rep in range(3):
        for s in range(0, c.shape[0], bs):
            chunk = c[s:s + bs]
            o = infer(a, chunk, bs)
            for k in range(chunk.shape[0]):
                tot += 1
                bad += 0 if np.array_equal(o[k], g[s + k]) else 1
    infer(a, c[:bs], bs)
    t0 = time.perf_counter()
    for _ in range(5):
        infer(a, c[:bs], bs)
    per = (time.perf_counter() - t0) / 5 / bs * 1e3
    emit({"stage": "exact", "tag": tag, "clk": Clocks.fclk0_mhz, "batch": bs,
          "ms_per_frame": round(per, 2), "fps": round(1000.0 / per, 1),
          "checked": tot, "mismatches": bad, "exact": bad == 0})

elif stage == "pack":
    # Two frames per inference at the SAME face scale. The letterbox currently
    # wastes 182 of 416 rows (43.75%) on constant grey; cropping 720->640 rows
    # and stacking two 416x208 frames fills the input with picture instead.
    tag, clk = sys.argv[2], float(sys.argv[3])
    fr = real_frames()
    if len(fr) < 2:
        emit({"stage": "pack", "error": "need >=2 captured frames in /dev/shm/facedemo/raw"})
        sys.exit(0)
    a = load(BITFILE, clk, 1)

    def solo(f):
        im = cv2.resize(f[40:680], (416, 208), interpolation=cv2.INTER_LINEAR)
        o = np.full((416, 416, 3), 114, np.uint8)
        o[104:312] = im
        return np.ascontiguousarray(cv2.cvtColor(o, cv2.COLOR_BGR2RGB))

    def pack(f0, f1):
        o = np.empty((416, 416, 3), np.uint8)
        o[0:208] = cv2.resize(f0[40:680], (416, 208), interpolation=cv2.INTER_LINEAR)
        o[208:416] = cv2.resize(f1[40:680], (416, 208), interpolation=cv2.INTER_LINEAR)
        return np.ascontiguousarray(cv2.cvtColor(o, cv2.COLOR_BGR2RGB))

    def band(raw, y0, y1):
        b = lpyolo.detect(raw[None], SCALE)
        if not len(b):
            return b
        cy = (b[:, 1] + b[:, 3]) / 2
        b = b[(cy >= y0) & (cy < y1)].copy()
        if len(b):
            b[:, [1, 3]] -= y0
        return b

    rows = []
    for i in range(0, len(fr) - 1, 2):
        s0 = band(infer(a, solo(fr[i])[None], 1)[0], 104, 312)
        p = infer(a, pack(fr[i], fr[i + 1])[None], 1)[0]
        t = band(p, 0, 208)
        b = band(p, 208, 416)
        s1 = band(infer(a, solo(fr[i + 1])[None], 1)[0], 104, 312)
        rows.append({"i": i, "solo0": len(s0), "packed_top": len(t),
                     "solo1": len(s1), "packed_bot": len(b),
                     "agree": len(s0) == len(t) and len(s1) == len(b)})
    img = pack(fr[0], fr[1])[None]
    infer(a, img, 1)
    t0 = time.perf_counter()
    for _ in range(5):
        infer(a, img, 1)
    dt = (time.perf_counter() - t0) / 5
    # An agreement test over an empty set proves only that packing invents
    # nothing. Say so, loudly, rather than reporting "agree 3/3" from three
    # comparisons of zero detections against zero -- which is what this stage
    # did when it first ran against frames captured with nobody in front of the
    # lens, and it read exactly like a pass.
    found = sum(r["solo0"] + r["solo1"] for r in rows)
    emit({"stage": "pack", "tag": tag, "clk": Clocks.fclk0_mhz,
          "ms_per_call": round(dt * 1e3, 2),
          "ms_per_frame": round(dt / 2 * 1e3, 2), "fps": round(2 / dt, 1),
          "agree": sum(r["agree"] for r in rows), "pairs": len(rows),
          "faces_in_reference": found,
          "verdict": ("INCONCLUSIVE: no faces in the captured frames, so this "
                      "compared empty against empty. Point the camera at a face "
                      "and re-run." if found == 0 else "meaningful"),
          "detail": rows})

elif stage == "async":
    # Overlap the 23 ms of CPU preprocessing with the accelerator. execute_on_buffers
    # already exposes asynch=True; the only reason the current loop is serial is
    # that it calls the blocking form.
    tag, clk, bs = sys.argv[2], float(sys.argv[3]), int(sys.argv[4])
    fr = real_frames()
    if not fr:
        emit({"stage": "async", "error": "no captured frames"})
        sys.exit(0)
    while len(fr) < 12:
        fr = fr + fr
    fr = fr[:12]
    a = load(BITFILE, clk, bs)

    def prep(f):
        lb, r, pd = lpyolo.letterbox(f)
        return np.ascontiguousarray(cv2.cvtColor(lb, cv2.COLOR_BGR2RGB), np.uint8), r, pd

    # serial reference
    t0 = time.perf_counter()
    for f in fr:
        img, r, pd = prep(f)
        o = infer(a, img[None], bs)
        lpyolo.unletterbox(lpyolo.detect(o[0][None], SCALE), r, pd, f.shape)
    serial = (time.perf_counter() - t0) / len(fr) * 1e3

    # threaded: preprocessing of frame N+1 runs while the PL chews frame N
    box = {}

    def producer():
        for i, f in enumerate(fr):
            box[i] = prep(f)

    t0 = time.perf_counter()
    th = threading.Thread(target=producer, daemon=True)
    th.start()
    for i, f in enumerate(fr):
        while i not in box:
            time.sleep(0.001)
        img, r, pd = box.pop(i)
        o = infer(a, img[None], bs)
        lpyolo.unletterbox(lpyolo.detect(o[0][None], SCALE), r, pd, f.shape)
    th.join(timeout=5)
    thr = (time.perf_counter() - t0) / len(fr) * 1e3
    emit({"stage": "async", "tag": tag, "clk": Clocks.fclk0_mhz, "batch": bs,
          "serial_ms": round(serial, 2), "threaded_ms": round(thr, 2),
          "serial_fps": round(1000 / serial, 1), "threaded_fps": round(1000 / thr, 1)})
else:
    sys.exit("unknown stage %s" % stage)
