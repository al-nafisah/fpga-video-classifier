"""Checks to_nn_input on a laptop: pip install numpy opencv-python-headless"""

import numpy as np

from live import to_nn_input


def main():
    frame = np.zeros((480, 640, 3), np.uint8)
    frame[:, :, 0] = 255
    frame[:, :80] = 0
    frame[:, 560:] = 0

    x = to_nn_input(frame)

    assert x.shape == (1, 32, 32, 3), x.shape
    assert x.dtype == np.uint8, x.dtype
    assert x.flags["C_CONTIGUOUS"]
    assert x.nbytes == 3072, x.nbytes
    assert (x[0, :, :, 2] == 255).all(), "blue must land in the red-green-blue slot"
    assert (x[0, :, :, 0] == 0).all(), "channels are still in camera order"

    print("input contract holds: %s %s, %d bytes, contiguous, RGB"
          % (x.shape, x.dtype, x.nbytes))


if __name__ == "__main__":
    main()
