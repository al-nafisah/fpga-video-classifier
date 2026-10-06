"""Drive LPYOLO when it shares a bitstream with the video pipeline.

The standalone path uses FINNExampleOverlay, which talks to the IODMAs FINN
generates in its own Zynq shell. The integrated overlay has none: it instantiates
FINN's stitched IP, whose only interfaces are one AXI-Stream in and one out, and
feeds it with an ordinary AXI DMA. So the transport is pynq.lib.dma and the
packing is a memcpy, exactly as board/accel.py does for cnv-w1a1.

Two numbers differ from that case and both are load-bearing:

  The input stream is 24 bits, not 8. LPYOLO's first layer takes SIMD=3 lanes of
  UINT8, so the DMA's MM2S side is 24 bits wide. In memory that is still a flat
  byte array in the order the network expects -- three channels of one pixel per
  beat -- so the buffer is written the same way regardless.

  A transfer is 519168 bytes, not 3072. The AXI DMA's length register defaults
  to 14 bits, which tops out at 16383, and a 416x416x3 frame is thirty times
  that. The block design sets c_sg_length_width to 20. If a frame ever comes
  back truncated rather than wrong, look there first.

The accelerator has no control registers. Weights are compiled into the
bitstream, so there is nothing to configure and nothing to start: bytes in,
bytes out.
"""

import numpy as np

IN_BYTES = 416 * 416 * 3        # 519168, one letterboxed RGB frame
OUT_BYTES = 13 * 13 * 18        # 3042, the YOLO grid as INT8
TIMEOUT_S = 5.0


class Accelerator:
    """LPYOLO behind an AXI DMA, in the overlay that also drives HDMI."""

    def __init__(self, overlay, dma_name="axi_dma_0"):
        from pynq import allocate

        self.dma = getattr(overlay, dma_name)
        # Allocated once and reused. Both buffers are contiguous and physically
        # addressed, which is what the DMA needs; reallocating a 519 KB buffer
        # per frame would cost more than the inference.
        self._in = allocate(shape=(IN_BYTES,), dtype=np.uint8)
        self._out = allocate(shape=(OUT_BYTES,), dtype=np.int8)

    def infer(self, tensor):
        """(1, 416, 416, 3) uint8 RGB -> (1, 13, 13, 18) int8.

        The output is signed. Reading it as uint8 and reinterpreting later is a
        silent way to get boxes in the wrong places, because the YOLO decode
        treats negative objectness as "no face" and unsigned would make every
        one of those a strong detection.
        """
        flat = np.ascontiguousarray(tensor, dtype=np.uint8).reshape(-1)
        if flat.size != IN_BYTES:
            raise ValueError("expected %d bytes, got %d" % (IN_BYTES, flat.size))

        self._in[:] = flat
        self._in.flush()

        # Start the receive side first. The accelerator is a pipeline with no
        # handshake of its own, so its output can appear while the input is
        # still being sent, and a receive armed afterwards can miss it.
        self.dma.recvchannel.transfer(self._out)
        self.dma.sendchannel.transfer(self._in)
        self.dma.sendchannel.wait()
        self.dma.recvchannel.wait()

        self._out.invalidate()
        return np.array(self._out, dtype=np.int8).reshape(1, 13, 13, 18)

    def close(self):
        self._in.freebuffer()
        self._out.freebuffer()
