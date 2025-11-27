# Rebuilding the bitstream

Vivado 2022.2, Vitis HLS and Docker on x86.

## 1. FINN

From the repo root, about 47 minutes:

```bash
git clone https://github.com/Xilinx/finn-examples
cp -r build/finn/* finn-examples/build/bnn-pynq/
cd finn-examples/build && ./get-finn.sh
cd finn && ./run-docker.sh build_custom $PWD/../bnn-pynq
```

`get-finn.sh` pins FINN at `274785308`. FINN main no longer builds for the Pynq-Z2.

## 2. Vivado

`build/vivado` expects `../PYNQ` (PYNQ v3.1) and `../finn_ip` (FINN's IP repos) beside it.

```bash
cd build/vivado
source /opt/Xilinx/Vivado/2022.2/settings64.sh
vivado -mode batch -source finn_hdmi.tcl -notrace
vivado -mode batch -source resume.tcl -notrace
vivado -mode batch -source build.tcl -notrace
```

28,397 LUT (53%), 113 of 140 BRAM (81%), accelerator at 100 MHz, WNS +0.167 ns.
