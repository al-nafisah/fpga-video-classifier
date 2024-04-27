"""USB camera, cnv-w1a1, then HDMI out or MJPEG on :8000 if the bitstream has no video."""

import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CLASSES = ("airplane", "automobile", "bird", "cat", "deer",
           "dog", "frog", "horse", "ship", "truck")

WIDTH, HEIGHT = 640, 480
STREAM_PORT = 8000

# 100 us steps. Auto exposure halves the frame rate.
EXPOSURE = 312


def to_nn_input(frame):
    # anything but contiguous uint8 (1, 32, 32, 3) sends FINN down a path ~1000x slower
    side = min(frame.shape[:2])
    top = (frame.shape[0] - side) // 2
    left = (frame.shape[1] - side) // 2
    square = frame[top:top + side, left:left + side]
    small = cv2.resize(square, (32, 32), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    return np.ascontiguousarray(rgb, dtype=np.uint8).reshape(1, 32, 32, 3)


def open_camera():
    camera = cv2.VideoCapture(0)
    if not camera.isOpened():
        sys.exit("no camera on /dev/video0")
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    camera.set(cv2.CAP_PROP_FPS, 30)
    camera.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)  # manual
    camera.set(cv2.CAP_PROP_EXPOSURE, EXPOSURE)
    camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return camera


class Stream:
    def __init__(self):
        self._frame = None
        self._new = threading.Condition()

    def put(self, jpeg):
        with self._new:
            self._frame = jpeg
            self._new.notify_all()

    def take(self):
        with self._new:
            self._new.wait()
            return self._frame


def serve(stream, port=STREAM_PORT):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            try:
                while True:
                    jpeg = stream.take()
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n")
                    self.wfile.write(jpeg)
                    self.wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def open_screen(overlay):
    if not hasattr(overlay, "video"):
        return None
    from pynq.lib.video import PIXEL_BGR, VideoMode
    screen = overlay.video.hdmi_out
    # HDMIWrapper only binds the VDMA when there is an input side too
    if getattr(screen, "_vdma", None) is None:
        screen._vdma = overlay.video.axi_vdma
    screen.configure(VideoMode(WIDTH, HEIGHT, 24), PIXEL_BGR)
    screen.start()
    return screen


def main(bitfile):
    from finn_examples.driver import FINNExampleOverlay
    from qonnx.core.datatype import DataType

    io_shape_dict = {
        "idt": [DataType["UINT8"]],
        "odt": [DataType["UINT8"]],
        "ishape_normal": [(1, 32, 32, 3)],
        "oshape_normal": [(1, 1)],
        "ishape_folded": [(1, 32, 32, 3, 1)],
        "oshape_folded": [(1, 1, 1)],
        "ishape_packed": [(1, 32, 32, 3, 1)],
        "oshape_packed": [(1, 1, 1)],
        "input_dma_name": ["idma0"],
        "output_dma_name": ["odma0"],
        "number_of_external_weights": 0,
        "num_inputs": 1,
        "num_outputs": 1,
    }

    accel = FINNExampleOverlay(bitfile, "zynq-iodma", io_shape_dict, fclk_mhz=200.0)

    camera = open_camera()
    screen = open_screen(accel)
    stream = None
    if screen is None:
        stream = Stream()
        serve(stream)
        print("no video pipeline in this bitstream, so nothing reaches HDMI")
        print("watch it at http://%s:%d/ instead" % (os.uname().nodename, STREAM_PORT))

    capture = infer = display = 0.0
    frames = 0
    try:
        while True:
            t0 = time.perf_counter()
            ok, frame = camera.read()
            if not ok:
                continue

            t1 = time.perf_counter()
            label = CLASSES[int(accel.execute(to_nn_input(frame)).flat[0])]

            t2 = time.perf_counter()
            cv2.putText(frame, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                        1.0, (0, 255, 0), 2)
            if screen is not None:
                buffer = screen.newframe()
                buffer[:] = frame
                screen.writeframe(buffer)
            else:
                ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
                if ok:
                    stream.put(jpeg.tobytes())

            t3 = time.perf_counter()
            capture += t1 - t0
            infer += t2 - t1
            display += t3 - t2
            frames += 1

            if frames == 30:
                total = capture + infer + display
                print("%-11s capture %5.1f ms  infer %5.1f ms  display %5.1f ms  %4.1f fps"
                      % (label, capture / 30 * 1e3, infer / 30 * 1e3,
                         display / 30 * 1e3, 30 / total))
                capture = infer = display = 0.0
                frames = 0
    finally:
        camera.release()
        if screen is not None:
            screen.stop()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: live.py <bitfile>")
    main(sys.argv[1])
