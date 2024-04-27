"""Face detection and recognition on the CPU. No FPGA.

    python3 faces.py enroll <name>
    python3 faces.py

Watch at http://<board>:8000/
"""

import glob
import json
import os
import sys
import time

import cv2
import numpy as np

from live import Stream, open_camera, serve

CASCADE = "/usr/share/opencv4/lbpcascades/lbpcascade_frontalface_improved.xml"
GALLERY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gallery")
MODEL = os.path.join(GALLERY, "lbph.yml")
NAMES = os.path.join(GALLERY, "names.json")
FACE = (100, 100)

DETECT_SHRINK = 2
PREVIEW_SHRINK = 2
DETECT_EVERY = 3
MAX_DISTANCE = 70.0  # LBPH distance, lower is closer


def detector():
    if not os.path.exists(CASCADE):
        sys.exit("no cascade at %s (apt install opencv-data)" % CASCADE)
    cascade = cv2.CascadeClassifier(CASCADE)
    if cascade.empty():
        sys.exit("cascade at %s failed to load" % CASCADE)
    return cascade


def find_faces(cascade, frame):
    gray = cv2.equalizeHist(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    small = cv2.resize(gray, None, fx=1.0 / DETECT_SHRINK, fy=1.0 / DETECT_SHRINK,
                       interpolation=cv2.INTER_AREA)
    boxes = cascade.detectMultiScale(small, scaleFactor=1.25, minNeighbors=5,
                                     minSize=(30, 30))
    boxes = [tuple(int(v) * DETECT_SHRINK for v in box) for box in boxes]
    return gray, sorted(boxes, key=lambda b: -b[2] * b[3])


def face_crop(gray, box):
    x, y, w, h = box
    return cv2.resize(gray[y:y + h, x:x + w], FACE)


def identify(model, names, gray, box):
    label, distance = model.predict(face_crop(gray, box))
    return names[label], distance


def train():
    images, labels, names = [], [], []
    for name in sorted(os.listdir(GALLERY)):
        shots = sorted(glob.glob(os.path.join(GALLERY, name, "*.png")))
        if not shots:
            continue
        names.append(name)
        for shot in shots:
            images.append(cv2.imread(shot, cv2.IMREAD_GRAYSCALE))
            labels.append(len(names) - 1)
    if not names:
        sys.exit("nothing enrolled yet")

    model = cv2.face.LBPHFaceRecognizer_create()
    model.train(images, np.array(labels))
    model.write(MODEL)
    with open(NAMES, "w") as handle:
        json.dump(names, handle)
    print("trained on %d shots of %s" % (len(images), ", ".join(names)))


def enroll(name, wanted=20):
    cascade = detector()
    folder = os.path.join(GALLERY, name)
    os.makedirs(folder, exist_ok=True)
    camera = open_camera()
    kept = 0
    print("look at the camera. Move your head a little between shots.")
    try:
        while kept < wanted:
            ok, frame = camera.read()
            if not ok:
                continue
            gray, boxes = find_faces(cascade, frame)
            if not boxes:
                continue
            cv2.imwrite(os.path.join(folder, "%02d.png" % kept), face_crop(gray, boxes[0]))
            kept += 1
            print("  %d/%d" % (kept, wanted))
            time.sleep(0.3)
    finally:
        camera.release()
    train()


def watch():
    cascade = detector()
    if not os.path.exists(MODEL):
        sys.exit("enrol someone first: python3 faces.py enroll <name>")
    model = cv2.face.LBPHFaceRecognizer_create()
    model.read(MODEL)
    with open(NAMES) as handle:
        names = json.load(handle)

    camera = open_camera()
    stream = Stream()
    serve(stream)
    print("watch at http://%s:8000/" % os.uname().nodename)

    capture = detect = recognise = encode = 0.0
    frames = 0
    tick = 0
    found = []
    try:
        while True:
            t0 = time.perf_counter()
            ok, frame = camera.read()
            if not ok:
                continue

            t1 = time.perf_counter()
            fresh = tick % DETECT_EVERY == 0
            tick += 1
            if fresh:
                gray, boxes = find_faces(cascade, frame)

            t2 = time.perf_counter()
            if fresh:
                found = [(box,) + identify(model, names, gray, box) for box in boxes]

            t3 = time.perf_counter()
            for box, name, distance in found:
                known = distance < MAX_DISTANCE
                colour = (0, 255, 0) if known else (0, 0, 255)
                x, y, w, h = box
                cv2.rectangle(frame, (x, y), (x + w, y + h), colour, 2)
                cv2.putText(frame, "%s %.0f" % (name if known else "?", distance),
                            (x, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.7, colour, 2)

            preview = cv2.resize(frame, None, fx=1.0 / PREVIEW_SHRINK,
                                 fy=1.0 / PREVIEW_SHRINK, interpolation=cv2.INTER_AREA)
            ok, jpeg = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 60])
            if ok:
                stream.put(jpeg.tobytes())
            t4 = time.perf_counter()

            capture += t1 - t0
            detect += t2 - t1
            recognise += t3 - t2
            encode += t4 - t3
            frames += 1
            if frames == 15:
                total = capture + detect + recognise + encode
                print("faces %d  capture %5.1f  detect %5.1f  recognise %5.1f  "
                      "encode %5.1f ms   %4.1f fps"
                      % (len(found), capture / 15 * 1e3, detect / 15 * 1e3,
                         recognise / 15 * 1e3, encode / 15 * 1e3, 15 / total))
                capture = detect = recognise = encode = 0.0
                frames = 0
    finally:
        camera.release()


if __name__ == "__main__":
    os.makedirs(GALLERY, exist_ok=True)
    if len(sys.argv) == 3 and sys.argv[1] == "enroll":
        enroll(sys.argv[2])
    elif len(sys.argv) == 2 and sys.argv[1] == "train":
        train()
    elif len(sys.argv) == 1:
        watch()
    else:
        sys.exit("usage: faces.py [enroll <name> | train]")
