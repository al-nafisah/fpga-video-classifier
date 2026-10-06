"""Camera -> LPYOLO on the FPGA -> screen, on a board that holds one bitstream.

LPYOLO's bitstream has no video pipeline at all. Its .hwh carries three
StreamingDataflowPartitions, the PS7, an interconnect and a reset -- no VDMA, no
v_tc, no dvi2rgb/rgb2dvi. Loading it removes the HDMI receiver *and* the
transmitter, so a streaming camera-to-screen loop with this accelerator is
impossible until both are built into one bitstream, the way finn_hdmi.bit
already does for cnv-w1a1.

So this alternates. Each cycle:

  finn_hdmi.bit    LIVE video on the monitor, with the last cycle's boxes drawn
                   over it, while capturing the next batch of frames
  finn-accel.bit   detect faces in that batch on the accelerator

The picture is live at ~30 fps for the whole video phase. Only the BOXES lag,
by one cycle, because the detector physically cannot run while the video
pipeline is in the PL. Running a detector slower than the camera and drawing its
most recent result over live frames is the normal arrangement; what is not
normal here is the gap, during which the monitor loses signal entirely because
the bitstream that drives it has been swapped out.

Each phase runs as its OWN PROCESS. Loading a second Overlay in one process
unmaps the first one's MMIO windows while Python still holds objects pointing
into them; touching one then is an "external abort on non-linefetch", which
reaches the shell as a bus error and leaks whatever CMA the process held. A
process boundary is the only reliable teardown here. Frames cross as .npy in
/dev/shm.

Root + venv + XRT, and not from /home/xilinx (a pynq/ directory there shadows
the installed package):

    cd ~/camnn && python3 board/facedemo.py [cycles]

The LPYOLO bitstream and its scale.npy are third-party and live outside the
checkout, at $LPYOLO_DIR (default /home/xilinx/lpyolo), the way finn_examples/
and gallery/ already do.
"""

import collections
import json
import os
import signal
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def _skip_ipython():
    """Stub IPython before pynq imports it. Worth 3.1 s of every phase.

    pynq -> pl_server -> embedded_device -> pynqmetadata.frontends ->
    visualisations -> `from IPython.display import display, Javascript, HTML`.
    Those three are used to draw block diagrams in a notebook and are never
    called here, but importing IPython costs 3.14 s measured on this board, and
    the demo pays it twice a cycle because each bitstream needs its own process
    (loading a second Overlay in one process bus-errors -- tested).

    Only the three names that module imports are provided, so if anything ever
    does try to use IPython for real it fails loudly on an attribute rather than
    quietly doing nothing.
    """
    import types
    if "IPython" in sys.modules:
        return
    ip = types.ModuleType("IPython")
    disp = types.ModuleType("IPython.display")
    for name in ("display", "Javascript", "HTML"):
        setattr(disp, name, lambda *a, **k: None)
    ip.display = disp
    sys.modules["IPython"] = ip
    sys.modules["IPython.display"] = disp


def _hdmi_bit():
    """finn_hdmi.bit, from this checkout if we are in one, else the board's clone.

    This file is run both from the repo (board/facedemo.py) and from the assets
    directory it was historically copied into, so neither location can be assumed.
    """
    local = os.path.join(os.path.dirname(HERE), "bitstream", "finn_hdmi.bit")
    if os.path.isfile(local):
        return local
    return os.path.join(os.environ.get("CAMNN_ROOT", "/home/xilinx/camnn"),
                        "bitstream", "finn_hdmi.bit")


HDMI_BIT = _hdmi_bit()
SHM = "/dev/shm/facedemo"

NFRAMES = 6          # frames handed to the detector per cycle
# Seconds of live video per cycle. The gap between them is fixed at roughly
# 35 s -- two Python starts, two bitstream downloads -- and cannot be removed
# while the detector and the video pipeline need different bitstreams. So this
# is the one knob that trades box freshness against how much of the time the
# screen is live: 25 s gives about 40% live, 90 s about 70%.
LIVE_S = float(os.environ.get("FACEDEMO_LIVE_S", "60"))


def stop_on_sigterm():
    def handler(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGHUP, handler)


# --------------------------------------------------------------- phases ----


def _rate(stamps):
    """Frames per second over a deque of timestamps."""
    if len(stamps) > 1 and stamps[-1] > stamps[0]:
        return (len(stamps) - 1) / (stamps[-1] - stamps[0])
    return 0.0


def lpyolo_draw(frame, boxes, hud="", colour=(0, 255, 0)):
    """Boxes and the HUD onto a live frame, in place.

    Deliberately does not import the accelerator module: that pulls in pynq's
    FINN driver, and this runs in the video process which must not touch it.
    """
    import cv2
    for x1, y1, x2, y2, conf in boxes:
        cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), colour, 2)
        cv2.putText(frame, "%.2f" % conf, (int(x1), max(int(y1) - 6, 14)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2)
    cv2.putText(frame, "LPYOLO on PL: %d face(s)" % len(boxes), (24, 44),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)
    if hud:
        # A filled rectangle behind one short string, NOT a long string drawn
        # twice for an outline. Measured on real frames: baseline 16.9 ms,
        # +short text on a filled rect 16.7 ms (free), +75-char text drawn twice
        # 24.0 ms. The outline alone cost 7.1 ms a frame, which is 56 fps down
        # to 42. Legibility over a bright wall and a dark bookshelf still needs
        # a backing, so it gets a rectangle instead of a second text pass.
        cv2.rectangle(frame, (16, 60), (16 + 13 * len(hud), 96), (0, 0, 0), -1)
        cv2.putText(frame, hud, (24, 86), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)


def phase_video():
    """Live video on the monitor with the last cycle's boxes; capture the next batch."""
    _skip_ipython()
    import cv2
    from pynq import Overlay
    from pynq.lib.video import PIXEL_BGR

    stop_on_sigterm()
    overlay = Overlay(HDMI_BIT)
    src = overlay.video.hdmi_in
    screen = overlay.video.hdmi_out
    for side in (src, screen):
        if getattr(side, "_vdma", None) is None:
            side._vdma = overlay.video.axi_vdma

    boxes = np.zeros((0, 5), np.float32)
    bpath = SHM + "/boxes.npy"
    if os.path.isfile(bpath):
        try:
            boxes = np.load(bpath)
        except Exception:
            pass
    detect_hud = "detector: waiting"
    try:
        with open(SHM + "/stats.json") as f:
            st = json.load(f)
        detect_hud = "| accel %.0f ms @%d MHz" % (st["accel_ms"], st["clock_mhz"])
    except Exception:
        pass

    started = []
    try:
        # Bringing the link up is inside the try because a stop can land here:
        # configure() blocks until the source is transmitting, which is exactly
        # where an idle demo spends its time when the camera is unplugged.
        # Nothing is running yet, so there is nothing to halt -- but the finally
        # must not fault on half-built objects either, hence `started`.
        src.configure(PIXEL_BGR)
        src.start()
        started.append(src)
        screen.configure(src.mode, PIXEL_BGR)
        screen.start()
        started.append(screen)
        print("   video: in %s, %d box(es) from last cycle"
              % (src.mode, len(boxes)), flush=True)

        # Live video, every frame, for the whole phase. The detector runs on a
        # different bitstream and cannot run at the same time, so its boxes are
        # necessarily from the previous cycle -- but the PICTURE must not be.
        # Showing six stills for a second each, which is what this did before,
        # looks like a slideshow of old photographs, because that is what it is.
        # Drawing stale boxes over live video is the normal arrangement for any
        # detector slower than its camera.
        captured = 0
        frames = distinct = 0
        # One rate, and it is the DISTINCT-frame rate. The HDMI link is
        # 1280x720@60 and the camera runs at 30, so every camera frame goes onto
        # the wire about twice and readframe() returns duplicates. Quoting the
        # link rate would claim a throughput the camera never supplied: you
        # cannot push more distinct pictures to the screen than you were given.
        cam = collections.deque(maxlen=31)
        prev_sig = None
        hud = "measuring"
        t_end = time.perf_counter() + LIVE_S
        t0 = time.perf_counter()
        while time.perf_counter() < t_end:
            frame = src.readframe()
            # Capture the detector's next batch from the live stream, spread
            # across the phase so the six frames are not six copies of one moment.
            if captured < NFRAMES and frames % 12 == 0:
                np.save(SHM + "/raw/%02d.npy" % captured, np.array(frame))
                captured += 1

            # Duplicate detection on a 660-byte stride sample rather than the
            # whole 2.76 MB frame. Measured free: 16.87 ms against a 16.9 ms
            # baseline. Comparing every pixel would cost more than the rest of
            # the loop; a 64-pixel lattice tells a new frame from a repeat.
            sig = frame[::64, ::64].copy()
            if prev_sig is None or not np.array_equal(sig, prev_sig):
                cam.append(time.perf_counter())
                prev_sig = sig
                distinct += 1

            # Recomputed every frame, which is free -- the windows are what make
            # the number readable, not a refresh interval.
            hud = "%.1f fps  %s" % (_rate(cam), detect_hud)

            lpyolo_draw(frame, boxes, hud)
            # readframe hands over ownership of a VDMA buffer and writeframe
            # takes ownership of one, so the frame crosses without a 2.7 MB copy.
            screen.writeframe(frame)
            frames += 1
        wall = time.perf_counter() - t0
        print("   video: %d distinct of %d frames in %.1f s = %.1f fps to screen, captured %d"
              % (distinct, frames, wall, distinct / wall if wall else 0, captured),
              flush=True)
    except KeyboardInterrupt:
        print("   video: stopped", flush=True)
    finally:
        for side in started:
            side.stop()


def phase_detect():
    """Run every raw/*.npy through LPYOLO and publish the newest boxes.

    Two speedups over the obvious loop, both bit-exact -- the boxes are
    identical to the 100 MHz serial version, which is checked by
    board/speed_suite.py rather than assumed:

    1. 125 MHz instead of the 100 MHz the bitstream was built for. Verified byte
       -for-byte over 36 runs. 142.86 MHz is NOT safe: it is 12% faster, it still
       draws plausible boxes, and it returns different bytes on 22 of 36 runs.
    2. Preprocessing runs on a second thread, so the ~20 ms letterbox of frame
       N+1 happens while the PL is still working on frame N. cv2.resize releases
       the GIL, so this is real parallelism on the second A9 core, and it changes
       only WHEN the CPU works -- idma0 receives the same bytes in the same order.
    """
    import threading

    _skip_ipython()
    import cv2
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, HERE)
    import lpyolo
    import accel_lpyolo
    from accel_lpyolo import Accelerator

    stop_on_sigterm()
    scale = float(np.load(accel_lpyolo.SCALE_NPY))
    names = sorted(os.listdir(SHM + "/raw"))
    accel = Accelerator(batch=max(len(names), 1))

    done = []
    t_start = time.perf_counter()

    # Preprocess every frame first, across both cores, then hand the whole lot to
    # the accelerator in ONE call. Batching is where the hardware win is: the
    # accelerator is a dataflow pipeline whose 17 ms of fill and drain is paid
    # per call, not per frame, so six frames in one call cost 33.61 ms each
    # instead of 57.12. In a live loop that would cost latency, because nothing
    # returns until the whole batch is done -- here it costs nothing, because all
    # six frames are already sitting on disk before this phase starts.
    prepped = [None] * len(names)
    meta = [None] * len(names)
    frames = [None] * len(names)

    def preprocess(lo, hi):
        for i in range(lo, hi):
            frame = np.load(SHM + "/raw/" + names[i])
            lb, r, pad = lpyolo.letterbox(frame)
            frames[i] = frame
            meta[i] = (r, pad)
            prepped[i] = np.ascontiguousarray(
                cv2.cvtColor(lb, cv2.COLOR_BGR2RGB), dtype=np.uint8)

    half = len(names) // 2
    worker = threading.Thread(target=preprocess, args=(half, len(names)), daemon=True)
    worker.start()
    preprocess(0, half)          # cv2.resize releases the GIL, so this is real
    worker.join(timeout=10)      # parallelism on the second A9 core

    t_infer = time.perf_counter()
    raw = accel.infer(np.stack(prepped, 0))
    infer_ms = (time.perf_counter() - t_infer) / max(len(names), 1) * 1e3

    faces = 0
    latest = np.zeros((0, 5), np.float32)
    for i, name in enumerate(names):
        boxes = lpyolo.unletterbox(lpyolo.detect(raw[i][None], scale),
                                   meta[i][0], meta[i][1], frames[i].shape)
        faces += len(boxes)
        # Newest non-empty result wins. Taking the newest frame's boxes
        # unconditionally throws away a real detection whenever the last frame of
        # the batch happens to miss -- which in a dim room is most of them, and
        # it showed up immediately as "0 box(es) from last cycle" after a cycle
        # that had found a face. The batch spans about two seconds, so the
        # staleness this trades for is small against a box that never appears.
        if len(boxes):
            latest = boxes
        if os.environ.get("FACEDEMO_PNG"):
            done.append((name, lpyolo.draw(frames[i].copy(), boxes)))

    # Publish BOXES, not annotated pictures. The video phase draws them over
    # live frames; handing it finished images is what made the screen a
    # slideshow of a scene that had already moved on.
    tmp = SHM + "/boxes.tmp.npy"
    np.save(tmp, latest.astype(np.float32))
    os.replace(tmp, SHM + "/boxes.npy")        # atomic, so a half-written file
                                               # is never read by the next phase

    # The video phase has no way to time the accelerator -- it is not in the PL
    # while video is -- so the detector reports its own numbers here for the HUD.
    tmp = SHM + "/stats.tmp.json"
    with open(tmp, "w") as f:
        json.dump({"accel_ms": round(infer_ms, 2),
                   "clock_mhz": int(accel.clock_mhz),
                   "batch": int(accel.batch),
                   "faces": int(faces),
                   "frames": len(names)}, f)
    os.replace(tmp, SHM + "/stats.json")

    wall = time.perf_counter() - t_start
    n = max(len(names), 1)
    print("   detect: %d frames, %.1f ms each on the accelerator, %.1f ms/frame "
          "wall (%.1f fps), %d face(s)"
          % (len(names), infer_ms, wall / n * 1e3, n / wall, faces))

    # PNGs are written AFTER the timing window, and only on request. Encoding a
    # 1280x720x3 PNG is a 2.76 MB deflate plus libpng's per-row filtering plus an
    # SD writeback, on a 650 MHz A9 with no NEON. It was inside this loop, and it
    # dominated the loop: measured directly on this board it is 215.8 ms a frame,
    # about six times the 37.7 ms the accelerator spends. Nothing reads these
    # files except a human debugging.
    if os.environ.get("FACEDEMO_PNG"):
        for name, img in done:
            cv2.imwrite(os.path.join(HERE, "out_" + name.replace(".npy", ".png")), img)


# ------------------------------------------------------------ orchestrator --


_child = None
_stopping = False


def run(phase):
    """Run one phase as a child process, forwarding a stop request to it.

    The child owns a bitstream and, in the video phase, two running VDMA
    channels. It must be allowed to unwind: its finally halts both channels, and
    a channel left running writes into CMA pages the kernel later hands to
    something else. So on shutdown we terminate and WAIT, and never SIGKILL.
    """
    global _child
    _child = subprocess.Popen([sys.executable, os.path.abspath(__file__), phase])
    try:
        rc = _child.wait()
    except KeyboardInterrupt:
        _child.terminate()
        try:
            _child.wait(timeout=30)
        except subprocess.TimeoutExpired:
            print("phase %s will not exit; leaving it rather than SIGKILL -- "
                  "a killed video process corrupts the CMA pool" % phase)
        raise
    finally:
        _child = None
    # pkill reaches the child too, so a SIGTERM'd phase during shutdown is
    # expected, not a failure.
    if rc != 0 and not _stopping:
        raise SystemExit("phase %s failed with %d" % (phase, rc))
    return rc


def main():
    """Cycle forever by default; pass a count to stop after that many."""
    global _stopping

    def shutdown(signum, frame):
        global _stopping
        _stopping = True
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGHUP, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    os.makedirs(SHM + "/raw", exist_ok=True)
    cycles = int(sys.argv[1]) if len(sys.argv) > 1 else 0   # 0 = until stopped
    print("starting; %s" % ("running until stopped" if cycles == 0
                            else "%d cycles" % cycles), flush=True)
    cycle = 0
    try:
        while cycles == 0 or cycle < cycles:
            t0 = time.perf_counter()
            run("video")
            t1 = time.perf_counter()
            run("detect")
            print("cycle %d: video %.1f s, detect %.1f s\n"
                  % (cycle, t1 - t0, time.perf_counter() - t1), flush=True)
            cycle += 1
        run("video")      # final pass so the last results reach the monitor
    except KeyboardInterrupt:
        print("\nstopping after cycle %d; both VDMA channels halted" % cycle, flush=True)
        return
    print("done", flush=True)


if __name__ == "__main__":
    # A phase is stopped by SIGTERM from facelive.sh, which stop_on_sigterm
    # turns into KeyboardInterrupt so the finally blocks run. That is a normal
    # shutdown, not a crash, so it should not print a traceback.
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "video":
            phase_video()
        elif len(sys.argv) > 1 and sys.argv[1] == "detect":
            phase_detect()
        else:
            main()
    except KeyboardInterrupt:
        print("   stopped", flush=True)
