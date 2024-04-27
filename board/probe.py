"""What this board offers for face detection, and what the camera negotiates."""

import glob
import os

import cv2


def line(name, value):
    print("%-22s %s" % (name, value))


def main():
    line("opencv", cv2.__version__)

    for api in ("FaceDetectorYN", "FaceRecognizerSF"):
        line(api, "yes" if hasattr(cv2, api) else "NO")

    line("cv2.face (contrib)", "yes" if hasattr(cv2, "face") else "NO")
    line("cv2.dnn", "yes" if hasattr(cv2, "dnn") else "NO")

    roots = []
    if hasattr(cv2, "data"):
        roots.append(cv2.data.haarcascades)
    roots += ["/usr/share/opencv4/haarcascades/", "/usr/share/opencv4/lbpcascades/",
              "/usr/share/opencv/haarcascades/"]
    found = []
    for root in roots:
        if root and os.path.isdir(root):
            found += glob.glob(os.path.join(root, "*frontalface*.xml"))
    line("frontal-face cascades", len(set(found)))
    for path in sorted(set(found)):
        print("    %s" % path)

    camera = cv2.VideoCapture(0)
    if not camera.isOpened():
        line("camera", "NOT OPEN")
        return
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    fourcc = int(camera.get(cv2.CAP_PROP_FOURCC))
    line("camera fourcc", "".join(chr((fourcc >> 8 * i) & 0xFF) for i in range(4)))
    line("camera size", "%dx%d" % (camera.get(cv2.CAP_PROP_FRAME_WIDTH),
                                   camera.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    line("camera fps", camera.get(cv2.CAP_PROP_FPS))

    camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    fourcc = int(camera.get(cv2.CAP_PROP_FOURCC))
    line("after MJPG request", "".join(chr((fourcc >> 8 * i) & 0xFF) for i in range(4)))
    line("fps then", camera.get(cv2.CAP_PROP_FPS))
    camera.release()


if __name__ == "__main__":
    main()
