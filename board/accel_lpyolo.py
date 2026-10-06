"""LPYOLO behind FINN's IODMAs, run as fast as it can go while staying bit-exact.

Two compounding wins over the naive FINNExampleOverlay(fclk_mhz=100, batch_size=1)
path, both measured on this board, both proved byte-for-byte identical to the
100 MHz golden outputs:

  CLOCK.  The bitstream was built for 100 MHz and holds at 125 MHz. It does NOT
  hold at 142.86: 22 of 36 runs came back with different bytes, while still
  producing plausible-looking boxes. That is the whole danger of overclocking a
  detector -- it does not fail loudly, it just gets quietly wrong. 166.67 MHz
  missed on all 36; 200 MHz hung the output DMA outright. PYNQ can only reach
  125.0 and 142.857 in that range (FCLK = 1000/8 and 1000/7), so 125 is the
  ceiling, worth 1.22x.

  BATCHING.  The accelerator is a dataflow pipeline, so its latency and its
  initiation interval are different numbers. Measured at 100 MHz, total time for
  N frames fits total_ms = 17.0 + 35.1*N almost exactly (52.10 / 87.19 / 157.41 /
  297.81 / 578.62 ms at N = 1 / 2 / 4 / 8 / 16). The 17 ms is pipeline fill and
  drain paid once per call; the 35.1 ms is the real per-frame cost. Submitting N
  frames in one call amortises the fill. FINN's IODMAs take the batch count in
  register 0x1C, so this is a native capability, not a trick (driver.py:356-365).

Measured end to end through infer(), every row checked against the 100 MHz
batch-1 golden over 27 frames, all with zero mismatches:

    100 MHz batch 1   57.12 ms/frame   17.5 fps   (baseline)
    100 MHz batch 6   41.14            24.3
    125 MHz batch 1   46.71            21.4
    125 MHz batch 6   33.61            29.8       <- 1.70x, byte-identical

The cost of batching is latency: a batch of 6 returns nothing until all 6 are
done. For a live overlay that is a few frames of lag, which is why BATCH
defaults to 6 rather than 16 -- past 8 the curve is flat anyway (37.23 ms/frame
at 8, 36.16 at 16) and the lag keeps growing.

Never raise CLOCK_MHZ without re-running the bit-exactness check in
board/speed_suite.py ("exact" stage) against a fresh golden set. "It still detects faces" is
not evidence.
"""

import os
import time

import numpy as np

# The LPYOLO assets -- bitstream, scale.npy, the golden set, runtime_weights --
# are third-party and 4 MB of binary, so they live outside the checkout, the way
# finn_examples/ and gallery/ already do. Everything here resolves from ASSETS so
# the clone runs wherever it sits and the board copy keeps working unchanged.
ASSETS = os.environ.get("LPYOLO_DIR", "/home/xilinx/lpyolo")
BITFILE = os.path.join(ASSETS, "bitfile", "finn-accel.bit")
SCALE_NPY = os.path.join(ASSETS, "bitfile", "scale.npy")

# Buffers belonging to a transfer that never completed are parked here FOREVER
# and deliberately never freed. See Accelerator.infer.
_QUARANTINE = []

# VERIFIED bit-exact: 125 MHz at batch 1, 36 runs over 9 inputs, every byte equal
# to the 100 MHz golden. Adopt freely.
CLOCK_MHZ = 125.0

# VERIFIED bit-exact: batch 6 at 125 MHz, 27 frames checked, 0 mismatches
# against the 100 MHz batch-1 golden. The accelerator is a dataflow pipeline, so
# its latency and its initiation interval are different numbers: at 100 MHz,
# execute_on_buffers timed 52.10 / 87.19 / 157.41 / 297.81 / 578.62 ms for
# N = 1 / 2 / 4 / 8 / 16, fitting total = 17.0 + 35.1*N. The 17 ms is pipeline
# fill and drain, paid once per call regardless of N; submitting 6 frames
# together amortises it away. FINN's IODMAs take the count in register 0x1C
# (driver.py:356-365), so this is a native capability, not a trick.
#
# Measured end to end through infer(): 57.12 -> 33.61 ms/frame, 17.5 -> 29.8 fps.
# Past 8 the curve flattens (37.23 at 8, 36.16 at 16) while latency keeps
# growing, so 6 is the useful point. The cost is latency, not accuracy: a batch
# of 6 returns nothing until all 6 are done. In facedemo that is free, because
# all 6 frames already exist on disk before the detect phase starts.
BATCH = 6
TIMEOUT_S = 5.0

_IO = {
    "ishape_normal": [(1, 416, 416, 3)], "oshape_normal": [(1, 13, 13, 18)],
    "ishape_folded": [(1, 416, 416, 1, 3)], "oshape_folded": [(1, 13, 13, 18, 1)],
    "ishape_packed": [(1, 416, 416, 1, 3)], "oshape_packed": [(1, 13, 13, 18, 1)],
    "input_dma_name": ["idma0"], "output_dma_name": ["odma0"],
    "number_of_external_weights": 0, "num_inputs": 1, "num_outputs": 1,
}


def io_shape_dict():
    from qonnx.core.datatype import DataType
    d = dict(_IO)
    d["idt"] = [DataType["UINT8"]]
    d["odt"] = [DataType["INT8"]]
    return d


class Accelerator:
    """Batched LPYOLO. Feed it (N, 416, 416, 3) uint8 RGB, get (N, 13, 13, 18) int8."""

    def __init__(self, bitfile=None, batch=BATCH, clock_mhz=CLOCK_MHZ,
                 runtime_weight_dir=None):
        from finn_examples.driver import FINNExampleOverlay

        if runtime_weight_dir is None:
            runtime_weight_dir = os.path.join(ASSETS, "runtime_weights/")

        # fclk is set through the driver, which applies it immediately after the
        # bitstream download (driver.py:107-108). Setting FCLK on an already
        # running design leaves it wedged -- every subsequent transfer then times
        # out with the output DMA never going idle, and a re-download does not
        # clear it. Only a reboot does. So set it here, once, and never again.
        self.accel = FINNExampleOverlay(
            bitfile_name=bitfile or BITFILE, platform="zynq-iodma", io_shape_dict=io_shape_dict(),
            batch_size=batch, fclk_mhz=clock_mhz, runtime_weight_dir=runtime_weight_dir)
        self.batch = batch
        self.clock_mhz = clock_mhz

    def submit(self, batch):
        """Copy a batch in and arm both IODMAs. Returns immediately; pair with collect().

        NOTE the driver's docstring for execute_on_buffers (driver.py:339-342)
        is INVERTED with respect to its own code (driver.py:377-379):

            if asynch is False:
                self.wait_until_finished()

        so asynch=True is the NON-blocking one, the opposite of what the
        docstring says. Anyone trusting the prose gets a serial pipeline while
        believing it is overlapped.

        Splitting submit from collect lets the CPU letterbox the next frame, or
        post-process the previous one, while the PL is busy -- worth up to the
        full ~20 ms preprocess, since the accelerator is separate hardware.
        """
        a = self.accel
        n = batch.shape[0]
        if n > self.batch:
            raise ValueError("batch of %d exceeds allocated %d" % (n, self.batch))
        if n < self.batch:
            pad = np.zeros((self.batch - n,) + batch.shape[1:], batch.dtype)
            batch = np.concatenate([batch, pad], 0)
        folded = a.fold_input(np.ascontiguousarray(batch, np.uint8))
        a.copy_input_data_to_device(a.pack_input(folded))
        a.execute_on_buffers(asynch=True)
        return n

    def collect(self, n):
        """Block until the armed transfer finishes, then return its (n,13,13,18) int8.

        Bounded, unlike the driver's wait_until_finished() (driver.py:380-383),
        which is an unbounded busy-poll and would hang forever if the accelerator
        ever stopped completing -- which is exactly what 200 MHz did.
        """
        a = self.accel
        t0 = time.perf_counter()
        while a.odma[0].read(0x00) & 0x2 == 0:
            if time.perf_counter() - t0 > TIMEOUT_S:
                # The output DMA is still armed and still intends to write into
                # obuf_packed_device. If this object is garbage collected the
                # kernel returns those physical pages to the CMA pool, the DMA
                # writes into them anyway, and the pages come back as ordinary
                # heap for whatever starts next. That is not theoretical: it
                # cost this board twice. The symptom names nothing -- fresh
                # processes segfault during interpreter startup, ssh drops, and
                # the board eventually stops responding and needs a power cycle.
                # It is the same hazard CLAUDE.md documents for the video VDMA,
                # and it applies just as much to FINN's IODMAs.
                #
                # So the buffers are parked, not freed. Leaking a few hundred KB
                # of CMA is enormously cheaper than corrupting the kernel's heap.
                _QUARANTINE.append((a.ibuf_packed_device, a.obuf_packed_device))
                a.ibuf_packed_device = None
                a.obuf_packed_device = None
                raise TimeoutError(
                    "output DMA never signalled done after %.1f s. Its buffers "
                    "have been quarantined, not freed. REBOOT THE BOARD before "
                    "running anything else -- a re-download does not clear this."
                    % TIMEOUT_S)
        a.copy_output_data_from_device(a.obuf_packed[0])
        out = np.asarray(a.unfold_output(a.unpack_output(a.obuf_packed[0])))
        return out[:n]

    def infer(self, batch):
        """(N, 416, 416, 3) uint8 -> (N, 13, 13, 18) int8. The serial composition."""
        return self.collect(self.submit(batch))
