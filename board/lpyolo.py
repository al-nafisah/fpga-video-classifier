"""LPYOLO pre/post-processing in pure numpy.

The upstream repo does this half in torch (models.Detect) and half in
torchvision (ops.nms). This board has no torch and installing an armv7 wheel for
two tensor ops is not worth it, so both are reimplemented here against numpy
1.21, which PYNQ already ships.

Faithful to finn-quantized-yolo/src/deploy/models.py Detect.forward and
utils.non_max_suppression:

  xy   = (raw + 0.5 + grid) * stride
  wh   = (raw + 1.0)^2 * anchor
  conf = raw / 2 + 0.5          (replaces the sigmoid the paper swapped out)
  conf = obj_conf * cls_conf

The accelerator emits INT8; scale.npy (1/127) puts it back in [-1, 1].
"""

import cv2
import numpy as np

STRIDE = 32
GRID = 13
NA = 3            # anchors
NO = 6            # 4 box + 1 obj + 1 class
NET = 416
ANCHORS = np.array([[10.0, 14.0], [23.0, 27.0], [37.0, 58.0]], dtype=np.float32)

# grid[i, j] = (x, y) = (j, i), matching torch.meshgrid(..., indexing="ij")
# followed by stack((xv, yv), 2) in Detect._make_grid.
_yv, _xv = np.meshgrid(np.arange(GRID), np.arange(GRID), indexing="ij")
_GRID_XY = np.stack((_xv, _yv), -1).astype(np.float32)


def letterbox(img, new=NET, colour=(114, 114, 114)):
    """Resize preserving aspect ratio, pad to new x new. Returns img, ratio, (dw, dh)."""
    h, w = img.shape[:2]
    r = min(new / h, new / w)
    unpad = (int(round(w * r)), int(round(h * r)))
    dw, dh = (new - unpad[0]) / 2, (new - unpad[1]) / 2
    if (w, h) != unpad:
        img = cv2.resize(img, unpad, interpolation=cv2.INTER_LINEAR)
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    img = cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=colour)
    return img, r, (dw, dh)


def decode(raw, scale):
    """(1, 13, 13, 18) int8-valued output -> (N, 6) [cx, cy, w, h, obj, cls] in 416-space."""
    y = (np.asarray(raw, dtype=np.float32) * scale).transpose(0, 3, 1, 2)   # (1,18,13,13)
    y = y.reshape(-1, NA, NO, GRID, GRID).transpose(0, 1, 3, 4, 2)          # (1,3,13,13,6)
    y = y.copy()
    y[..., 0:2] = (y[..., 0:2] + 0.5 + _GRID_XY) * STRIDE
    y[..., 2:4] = (y[..., 2:4] + 1.0) ** 2 * ANCHORS.reshape(1, NA, 1, 1, 2)
    y[..., 4:] = y[..., 4:] / 2.0 + 0.5
    return y.reshape(-1, NO)


def xywh2xyxy(x):
    y = np.empty_like(x)
    y[:, 0] = x[:, 0] - x[:, 2] / 2
    y[:, 1] = x[:, 1] - x[:, 3] / 2
    y[:, 2] = x[:, 0] + x[:, 2] / 2
    y[:, 3] = x[:, 1] + x[:, 3] / 2
    return y


def nms(boxes, scores, iou_thres):
    """Greedy IoU suppression. Same result as torchvision.ops.nms, ~500 boxes max here."""
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    order = scores.argsort()[::-1]
    keep = []
    while order.size:
        i = order[0]
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        iou = inter / (areas[i] + areas[rest] - inter + 1e-9)
        order = rest[iou <= iou_thres]
    return np.array(keep, dtype=int)


def detect(raw, scale, conf_thres=0.20, iou_thres=0.10, max_det=100):
    """Accelerator output -> (n, 5) array of [x1, y1, x2, y2, conf] in 416-space."""
    pred = decode(raw, scale)
    pred = pred[pred[:, 4] > conf_thres]
    if not pred.shape[0]:
        return np.zeros((0, 5), dtype=np.float32)
    conf = pred[:, 4] * pred[:, 5]                  # obj_conf * cls_conf, nc == 1
    boxes = xywh2xyxy(pred[:, :4])
    keep_conf = conf > conf_thres
    boxes, conf = boxes[keep_conf], conf[keep_conf]
    if not boxes.shape[0]:
        return np.zeros((0, 5), dtype=np.float32)
    keep = nms(boxes, conf, iou_thres)[:max_det]
    return np.concatenate([boxes[keep], conf[keep, None]], 1).astype(np.float32)


def unletterbox(boxes, r, pad, shape):
    """Map boxes from 416-space back to the original frame, clipped to it."""
    if not boxes.shape[0]:
        return boxes
    out = boxes.copy()
    out[:, [0, 2]] = (out[:, [0, 2]] - pad[0]) / r
    out[:, [1, 3]] = (out[:, [1, 3]] - pad[1]) / r
    out[:, [0, 2]] = out[:, [0, 2]].clip(0, shape[1] - 1)
    out[:, [1, 3]] = out[:, [1, 3]].clip(0, shape[0] - 1)
    return out


def draw(frame, boxes, colour=(0, 255, 0)):
    for x1, y1, x2, y2, conf in boxes:
        p1, p2 = (int(x1), int(y1)), (int(x2), int(y2))
        cv2.rectangle(frame, p1, p2, colour, 2)
        cv2.putText(frame, "%.2f" % conf, (p1[0], max(p1[1] - 6, 14)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2)
    return frame
