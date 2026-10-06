# Making LPYOLO faster on the Pynq-Z2

**1.70x with byte-identical output, shipped. 3.4x available if a small box
shift is acceptable.** Every claim below is measured on the board, and every
speedup is either proved bit-exact against a golden set or explicitly marked as
not being so.

Baseline, measured: **23.2 ms preprocess + 57.1 ms inference + 1.8 ms postprocess
= 82.1 ms, 12.2 fps** in the serial detect loop. The accelerator alone accounts
for 52.2 ms of that.

The rule throughout: a face detector fed slightly-wrong data still draws
plausible boxes, so "it still detects faces" is not evidence. The only evidence
accepted here is byte-for-byte equality of the INT8 output tensor against a
100 MHz golden set (9 inputs: zeros, saturated, six seeded-random, one photo),
repeated, because setup-time violations are marginal and data-dependent.

## Where the time actually goes

`FINNExampleOverlay.throughput_test()` already instruments the split, and it is
decisive:

| | ms |
|---|---|
| `execute_on_buffers` (pure hardware, buffers resident) | **52.20** |
| `copy_input_data_to_device` (519 KB memcpy + cache flush) | 3.86 |
| `unpack_output` | 0.94 |
| `copy_output_data_from_device` | 0.30 |
| `fold_input` + `pack_input` + `unfold_output` | 0.38 |
| driver total | **5.47** |

So the driver is not the problem here. That is worth stating plainly, because
this project's prior experience pointed the other way: `board/accel.py` records
that for cnv-w1a1 FINN's driver cost *more* than halving the clock (6.35 ms vs
2.59 ms). It does not generalise. cnv-w1a1's input is 3072 bytes; LPYOLO's is
519168, and the packing takes the fast `ndarray.view()` path in both cases, so
the fixed driver cost stays roughly constant while the hardware time grows 20x.
A raw-DMA bypass is worth ~4 ms at most, not the 30 ms it was worth before.

## 1. Clock: 100 -> 125 MHz. VERIFIED, 1.22x

The bitstream was built for 100 MHz. Swept with bit-comparison against golden:

| requested | actual | ms/frame | result |
|---|---|---|---|
| 100 | 100.00 | 57.33 | EXACT |
| 111 | 111.11 | 52.19 | EXACT |
| **125** | **125.00** | **46.93** | **EXACT** |
| 142 | 142.86 | 41.64 | **MISMATCH on 22 of 36 runs** |
| 150 | 142.86 | 41.67 | MISMATCH on 18 of 36 |
| 166 | 166.67 | 36.53 | MISMATCH on 36 of 36 |
| 187 | 200.00 | — | **HUNG** — needed a power cycle |

142.86 MHz is the whole argument for bit-comparison in one line: it is 12%
faster, it still detects faces, and it is wrong 61% of the time. Nothing in the
output announces it.

PYNQ can only reach 125.0 and 142.857 in that range (FCLK = 1000/8 and 1000/7),
so **125 MHz is the ceiling** — there is nothing to tune between them.

## 2. Batching: VERIFIED bit-exact, 1.39x on its own

`execute_on_buffers` at 100 MHz, varying `batch_size` (FINN's IODMAs take the
count in register 0x1C, driver.py:356-365 — a native capability, not a trick):

| batch | total ms | ms/frame |
|---|---|---|
| 1 | 52.10 | 52.10 |
| 2 | 87.19 | 43.59 |
| 4 | 157.41 | 39.35 |
| 8 | 297.81 | 37.23 |
| 16 | 578.62 | 36.16 |

That fits `total = 17.0 + 35.1 x N` almost exactly. The 17 ms is dataflow
pipeline fill and drain paid once per call; 35.1 ms is the true per-frame
initiation interval. It is not Python overhead — `execute_on_buffers` is five
MMIO writes plus a poll, and 16 frames would cost 833 ms if nothing amortised.

An adversarial reviewer called this a dead end, arguing `numReps` only amortises
Python-side cost. The hardware disagreed, and the hardware is the authority: the
fit above has an R-squared indistinguishable from 1 over five points spanning 16x.

Past batch 8 the curve flattens while latency keeps growing, so 6 is the useful
point. The cost is **latency, not accuracy** — a batch of 6 returns nothing until
all 6 finish. In `facedemo` that is free, because all 6 frames already exist on
disk before the detect phase starts.

## The two together, measured end to end through `infer()`

Every row checked 27 frames against the 100 MHz batch-1 golden:

| clock | batch | ms/frame | fps | mismatches |
|---|---|---|---|---|
| 100 | 1 | 57.12 | 17.5 | **0** |
| 100 | 6 | 41.14 | 24.3 | **0** |
| 125 | 1 | 46.71 | 21.4 | **0** |
| 125 | 3 | 36.15 | 27.7 | **0** |
| **125** | **6** | **33.61** | **29.8** | **0** |

**1.70x, byte-for-byte identical.** Both knobs are live in
`board/accel_lpyolo.py`.

## 3. Preprocessing: no bit-exact win found

| variant | ms | identical? |
|---|---|---|
| **current: LINEAR 1280x720 -> 416x234 + cvtColor** | **19.8** | baseline |
| INTER_NEAREST | 7.8 | NO — aliases |
| two-step /2 then LINEAR | 25.8 | — |
| crop 1248x702 then exact /3 INTER_AREA | 47.1 | — |
| INTER_AREA (non-integer) | 118.6 | — |
| slice-reverse into prefilled canvas | 28.1 | yes, but slower |

The integer-factor trick that halved cnv-w1a1's downscale does **not** transfer:
INTER_AREA is simply slow in this OpenCV build even at an exact ratio. The
current path is already the best bit-exact option. The win here is not a faster
resize, it is **overlapping the resize with the accelerator** (below).

## 4. Overlap: the accelerator is a separate hardware unit

`execute_on_buffers(asynch=True)` exists and the loop just does not use it. While
the PL chews frame N, the CPU can preprocess frame N+1. That hides up to the
full 23.2 ms behind the 47 ms of inference, and changes nothing about what
reaches `idma0`.

One caveat found in the driver: `wait_until_finished` (driver.py:380-383) is an
unbounded busy-poll with no sleep. On a two-core A9 that spin competes with the
preprocessing thread. A short sleep in the poll frees the core; the poll
condition is untouched, so it stays bit-exact.

## 5. Frame packing: measured 2x, preserves every detection, NOT bit-exact

The letterbox puts 416x234 of picture inside a 416x416 input. **The other 182
rows -- 43.75% of every inference -- are the constant grey 114**, and the
accelerator convolves them at full cost.

Two frames fit in that budget. Crop 1280x720 to 1280x640 (dropping 11.1% of
vertical field of view), scale by the same 0.325 the current path already uses,
and each frame becomes 416x208; two stacked are exactly 416x416 with no padding.
**Face scale is unchanged** -- this is not a resolution trade.

The open question was the seam: a face near the bottom of the top frame now has
another scene below it rather than grey, and the network was trained on
letterboxed images. Tested on `sample.png` (12 faces), the same 416x208 content
placed solo, as the top half, and as the bottom half, matched greedily by IoU:

| | faces | matched at IoU>=0.5 | min IoU | max conf delta | spurious |
|---|---|---|---|---|---|
| solo | 12 | — | — | — | — |
| packed, top | 12 | **12/12** | 0.779 | 0.076 | 0 |
| packed, bottom | 12 | **12/12** | 0.551 | 0.090 | 0 |

**No face is lost and none is invented.** But it is *not* bit-exact: the network
sees different context, so boxes move -- min IoU 0.551 on the bottom half is a
visible shift, not a rounding difference. Adopt it if throughput matters more
than box precision; keep it off if "unchanged" has to mean unchanged.

A first attempt at this test was worthless and looked fine: run against camera
frames captured at 01:40 with nobody in front of the lens, it reported
"agree 3/3" -- comparing zero detections against zero detections. An agreement
test over an empty set proves only that packing invents nothing.

## 6. Outside the accelerator: what the demo actually spends its time on

`facedemo.py` wrote a 1280x720 PNG to the SD card **inside the loop it was
timing**. Measured directly on the board:

| | ms/frame |
|---|---|
| `cv2.imwrite` PNG (2.76 MB deflate + libpng row filtering + SD write) | **215.8** |
| `np.save` 2.76 MB to /dev/shm | 25.1 |
| `np.load` 2.76 MB from /dev/shm | 24.6 |
| letterbox | ~20 |
| **accelerator, 125 MHz batch 6** | **37.7** |

The PNG cost about six times the inference. It is now written after the timing
window and only when `FACEDEMO_PNG` is set.

**A correction worth recording.** I first estimated the PNG at ~3.3 s a frame, by
dividing the whole 24.9 s detect phase by its 6 frames. That was wrong: most of
that phase is Python and PYNQ import plus the 4.5 s bitstream download -- roughly
20 s of fixed cost per phase that has nothing to do with either the PNG or the
accelerator. Measuring the thing directly gave 215.8 ms. Dividing a total by a
count and naming the quotient after your suspect is not a measurement.

**The real remaining cost is the process boundary.** `np.load` + `np.save` of a
2.76 MB frame is 49.7 ms/frame -- now *larger than the inference itself*. That
accounts for the demo's measured 108 ms/frame wall almost exactly:

    24.6 load + 20 letterbox + 37.7 infer + 1.8 detect + 25.1 save = 109.2 ms

That handoff exists only because LPYOLO's bitstream has no video pipeline, so
capture and inference must live in separate processes with separate bitstreams.
It would vanish entirely in an integrated build. Optimising it further is
optimising an artefact of the constraint rather than the system.

## Where this lands

| | ms/frame | fps |
|---|---|---|
| accelerator path, 100 MHz batch 1 | 57.12 | 17.5 | baseline |
| + 125 MHz | 46.71 | 21.4 | **bit-exact** |
| + batching 6 | **33.54** | **29.8** | **bit-exact, 45 frames checked** |
| + frame packing | 16.77 | 59.6 | all detections kept, boxes shift |

**1.70x is bit-exact and shipped. 3.4x is available if a box shift is acceptable.**

Separately, overlapping preprocessing with the accelerator measured 71.72 ->
51.95 ms/frame (13.9 -> 19.2 fps) on the batch-1 path -- that win is subsumed by
batching, which hides the same CPU time behind a longer PL call.

Those are accelerator-path numbers. End to end the demo measures 108 ms/frame,
because a 2.76 MB frame crosses a process boundary twice per frame -- an
artefact of the one-bitstream constraint, not of the inference.

## Not available without the ONNX

The six `*_deploy.zip` files give bitstreams only. Rebuilding the accelerator is
blocked on the ONNX, which is on OneDrive behind a JS-rendered SharePoint page
that 403s every non-browser client. With it, the obvious architectural win is a
**416x256 network**: a 13x8 output grid instead of 13x13 removes the padding at
the source for ~1.63x, on top of everything above, at identical accuracy on the
visible content. That is a short fine-tune from the existing weights, not a
retrain.

## A trap that cost the board twice: the IODMA has the same hazard as the VDMA

CLAUDE.md documents that killing a video process leaves the VDMA writing into
CMA pages the kernel has already recycled, and that the symptom names nothing --
unrelated processes segfault, ssh drops its own MAC check, and the board looks
like failing hardware.

**FINN's output IODMA does exactly the same thing.** A bounded wait was added
here so a bad clock could not hang forever, and the bound was the problem: when
it fired, the process raised and exited with `odma0` still armed and still
pointing at `obuf_packed_device`. Python freed that buffer, CMA took the pages
back, and the DMA wrote into them anyway.

The evidence is the signature CLAUDE.md predicts. After the timeout, a
*freshly rebooted* board ran one process which died with

```
Segmentation fault (core dumped) python3 combo.py 100
Read from remote host 192.168.8.100: Connection timed out
```

and then went off the network entirely -- no ping, ARP FAILED, gateway fine --
needing a power cycle.

`board/accel_lpyolo.py` and `board/speed_suite.py` now park the buffers of any
transfer that did not complete in a module-level `_QUARANTINE` list and never
free them. Leaking a few hundred KB of CMA is enormously cheaper than corrupting
the kernel's heap. The timeout message says to reboot, because a bitstream
re-download does **not** clear it -- that was tested, and the next process timed
out the same way.

Two related rules, both learned the same expensive way:

- **Never set FCLK on a live design.** Probing achievable frequencies with
  `Clocks.fclk0_mhz = x` while a design was loaded wedged the output DMA
  permanently. Set it only via `FINNExampleOverlay(fclk_mhz=...)`, which applies
  it immediately after the download resets the PL.
- **Never load a second Overlay in one process.** It unmaps the first one's MMIO
  while Python still holds objects into it; touching one is an `external abort
  on non-linefetch`, which reaches the shell as a bus error. `board/facedemo.py`
  runs each bitstream in its own process for this reason.

## A footgun in the driver's own documentation

`execute_on_buffers`'s docstring (driver.py:339-342) says:

> `asynch=True` will block until all transfers are complete.
> `asynch=False` won't block, use `wait_until_finished()` to check completion

Its code, thirty lines below at driver.py:377-379, says the opposite:

```python
if asynch is False:
    self.wait_until_finished()
```

**`asynch=True` is the non-blocking one.** Anyone who overlaps CPU work by
trusting the prose gets a fully serial pipeline while believing it is
overlapped, and the timings will look merely disappointing rather than wrong.
`board/accel_lpyolo.py` uses `asynch=True` plus its own bounded poll.

## Reproducing

```bash
# on the board, root + venv + XRT, from /home/xilinx/lpyolo
bash run_speed_suite.sh          # every stage, highest value first
python3 speed_suite.py exact /home/xilinx/lpyolo/bitfile/finn-accel.bit repo 125 6
```

Each stage runs in its own process with its own bitstream download. That is not
tidiness: **setting FCLK on a live design wedges the output DMA permanently**,
and a re-download does not clear it — only a reboot does. And 200 MHz hung the
board hard enough to need a power cycle. Both are encoded as rules in
`speed_suite.py`.
