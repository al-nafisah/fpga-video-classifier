# Adds the accelerator, TLAST and an AXI DMA on HP2 to the video design.
#
# The stitched IP needs FINN's 62 child IP repos too, or finn_design_0 fails with
# "Cannot upgrade to invalid target ''". finn_ip/ is a copy of them from /var/tmp.

source ../finn_ip/ip_repo_paths.tcl
set_property ip_repo_paths [concat [list \
  ../PYNQ/boards/ip \
  ../../finn-examples/build/bnn-pynq/output_cnv-w1a1_Pynq-Z2/stitched_ip/ip \
] $finn_ip_repo_paths] [current_project]
update_ip_catalog -rebuild

# HPD high from reset
set_property -dict [list \
  CONFIG.C_DOUT_DEFAULT {0x00000001} \
  CONFIG.C_TRI_DEFAULT {0x00000000} \
] [get_bd_cells video/hdmi_in/frontend/axi_gpio_hdmiin]
set_property CONFIG.kClkRange {2} [get_bd_cells video/hdmi_in/frontend/dvi2rgb_0]

set_property CONFIG.PCW_USE_S_AXI_HP2 {1} [get_bd_cells ps7_0]

# 100 MHz: at 200 MHz MVAU_hls_3 misses by 2.4 ns next to the video pipeline
create_bd_cell -type ip -vlnv xilinx_finn:finn:finn_design:1.0 finn_design_0
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins finn_design_0/ap_clk]
connect_bd_net [get_bd_pins rst_ps7_0_fclk0/peripheral_aresetn] [get_bd_pins finn_design_0/ap_rst_n]

add_files -norecurse ./tlast_gen.v
update_compile_order -fileset sources_1
create_bd_cell -type module -reference tlast_gen tlast
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins tlast/aclk]
connect_bd_net [get_bd_pins rst_ps7_0_fclk0/peripheral_aresetn] [get_bd_pins tlast/aresetn]

create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:7.1 axi_dma_0
set_property -dict [list \
  CONFIG.c_include_sg {0} \
  CONFIG.c_sg_include_stscntrl_strm {0} \
  CONFIG.c_include_mm2s_dre {0} \
  CONFIG.c_include_s2mm_dre {0} \
  CONFIG.c_m_axis_mm2s_tdata_width {8} \
  CONFIG.c_s_axis_s2mm_tdata_width {8} \
  CONFIG.c_mm2s_burst_size {16} \
  CONFIG.c_s2mm_burst_size {16} \
] [get_bd_cells axi_dma_0]

connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXIS_MM2S] [get_bd_intf_pins finn_design_0/s_axis_0]
connect_bd_intf_net [get_bd_intf_pins finn_design_0/m_axis_0] [get_bd_intf_pins tlast/s_axis]
connect_bd_intf_net [get_bd_intf_pins tlast/m_axis] [get_bd_intf_pins axi_dma_0/S_AXIS_S2MM]

connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] \
  [get_bd_pins axi_dma_0/m_axi_mm2s_aclk] \
  [get_bd_pins axi_dma_0/m_axi_s2mm_aclk] \
  [get_bd_pins axi_dma_0/s_axi_lite_aclk] \
  [get_bd_pins ps7_0/S_AXI_HP2_ACLK]
connect_bd_net [get_bd_pins rst_ps7_0_fclk0/peripheral_aresetn] [get_bd_pins axi_dma_0/axi_resetn]

create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 accel_mem_intercon
set_property -dict [list CONFIG.NUM_MI {1} CONFIG.NUM_SI {2}] [get_bd_cells accel_mem_intercon]
connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXI_MM2S] [get_bd_intf_pins accel_mem_intercon/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXI_S2MM] [get_bd_intf_pins accel_mem_intercon/S01_AXI]
connect_bd_intf_net [get_bd_intf_pins accel_mem_intercon/M00_AXI] [get_bd_intf_pins ps7_0/S_AXI_HP2]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] \
  [get_bd_pins accel_mem_intercon/ACLK] \
  [get_bd_pins accel_mem_intercon/S00_ACLK] \
  [get_bd_pins accel_mem_intercon/S01_ACLK] \
  [get_bd_pins accel_mem_intercon/M00_ACLK]
connect_bd_net [get_bd_pins rst_ps7_0_fclk0/interconnect_aresetn] [get_bd_pins accel_mem_intercon/ARESETN]
connect_bd_net [get_bd_pins rst_ps7_0_fclk0/peripheral_aresetn] \
  [get_bd_pins accel_mem_intercon/S00_ARESETN] \
  [get_bd_pins accel_mem_intercon/S01_ARESETN] \
  [get_bd_pins accel_mem_intercon/M00_ARESETN]

set_property CONFIG.NUM_MI {3} [get_bd_cells ps7_0_axi_periph]
connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/M02_AXI] [get_bd_intf_pins axi_dma_0/S_AXI_LITE]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins ps7_0_axi_periph/M02_ACLK]
connect_bd_net [get_bd_pins rst_ps7_0_fclk0/peripheral_aresetn] [get_bd_pins ps7_0_axi_periph/M02_ARESETN]

assign_bd_address -offset 0x40400000 -range 0x00010000 -target_address_space [get_bd_addr_spaces ps7_0/Data] [get_bd_addr_segs axi_dma_0/S_AXI_LITE/Reg] -force
assign_bd_address -offset 0x00000000 -range 0x20000000 -target_address_space [get_bd_addr_spaces axi_dma_0/Data_MM2S] [get_bd_addr_segs ps7_0/S_AXI_HP2/HP2_DDR_LOWOCM] -force
assign_bd_address -offset 0x00000000 -range 0x20000000 -target_address_space [get_bd_addr_spaces axi_dma_0/Data_S2MM] [get_bd_addr_segs ps7_0/S_AXI_HP2/HP2_DDR_LOWOCM] -force
