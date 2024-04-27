"""Sweep exposure, then format and size at a pinned exposure, to find the frame rate cap."""

import time

import cv2

FRAMES = 25
PIN = 78  # 100 us steps
EXPOSURES = [None, 5, 39, 78, 156, 312, 625]
MODES = [("YUYV", 640, 480), ("YUYV", 320, 240), ("YUYV", 160, 120),
         ("MJPG", 640, 480), ("MJPG", 320, 240), ("MJPG", 1280, 720)]


def measure(fmt=None, size=None, exposure=PIN):
    camera = cv2.VideoCapture(0)
    if not camera.isOpened():
        return None
    try:
        if fmt:
            camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fmt))
        if size:
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])
        camera.set(cv2.CAP_PROP_FPS, 30)
        camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if exposure is None:
            camera.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3)
        else:
            camera.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)
            camera.set(cv2.CAP_PROP_EXPOSURE, exposure)

        for _ in range(10):
            camera.read()
        start = time.perf_counter()
        got, bright = 0, 0.0
        for _ in range(FRAMES):
            ok, frame = camera.read()
            if ok:
                got += 1
                bright += float(frame[::8, ::8].mean())
        elapsed = time.perf_counter() - start
        if not got:
            return None
        raw = int(camera.get(cv2.CAP_PROP_FOURCC))
        return {
            "got": "%s %dx%d" % ("".join(chr((raw >> 8 * i) & 0xFF) for i in range(4)).strip(),
                                 camera.get(cv2.CAP_PROP_FRAME_WIDTH),
                                 camera.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "ms": elapsed / got * 1e3,
            "fps": got / elapsed,
            "brightness": bright / got,
        }
    finally:
        camera.release()


def main():
    print("pass 1: exposure, at whatever format the driver defaults to")
    print("  %-9s %8s %7s %11s" % ("exposure", "ms/frame", "fps", "brightness"))
    for exposure in EXPOSURES:
        row = measure(exposure=exposure)
        if row:
            print("  %-9s %8.1f %7.1f %11.1f"
                  % ("auto" if exposure is None else exposure,
                     row["ms"], row["fps"], row["brightness"]))

    print("\npass 2: format and resolution, exposure pinned at %d" % PIN)
    print("  %-14s %-16s %8s %7s %11s"
          % ("asked", "actually got", "ms/frame", "fps", "brightness"))
    rows = []
    for fmt, width, height in MODES:
        row = measure(fmt, (width, height))
        if row:
            rows.append(row)
            print("  %-14s %-16s %8.1f %7.1f %11.1f"
                  % ("%s %dx%d" % (fmt, width, height), row["got"],
                     row["ms"], row["fps"], row["brightness"]))

    if rows:
        best = min(rows, key=lambda r: r["ms"])
        print("\nfastest mode: %s at %.1f ms (%.1f fps)" % (best["got"], best["ms"], best["fps"]))
        if best["fps"] < 20:
            print("under 20 fps everywhere: the cap is the camera or USB, not a setting")


if __name__ == "__main__":
    main()
