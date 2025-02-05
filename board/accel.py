"""cnv-w1a1 behind an AXI DMA."""

import numpy as np

IN_BYTES = 32 * 32 * 3
OUT_BYTES = 1


class Accelerator:
    def __init__(self, overlay, dma_name="axi_dma_0"):
        from pynq import allocate

        self.dma = getattr(overlay, dma_name)
        self._in = allocate(shape=(IN_BYTES,), dtype=np.uint8)
        self._out = allocate(shape=(OUT_BYTES,), dtype=np.uint8)

    def predict(self, tensor):
        flat = np.ascontiguousarray(tensor, dtype=np.uint8).reshape(-1)
        if flat.size != IN_BYTES:
            raise ValueError("expected %d bytes, got %d" % (IN_BYTES, flat.size))

        self._in[:] = flat
        self._in.flush()

        # arm the receive first, or the result can arrive before it
        self.dma.recvchannel.transfer(self._out)
        self.dma.sendchannel.transfer(self._in)
        self.dma.sendchannel.wait()
        self.dma.recvchannel.wait()

        self._out.invalidate()
        return int(self._out[0])

    def close(self):
        self._in.freebuffer()
        self._out.freebuffer()
