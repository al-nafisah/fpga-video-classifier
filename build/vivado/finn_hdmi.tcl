# Block design: HDMI in and out from PYNQ's base overlay, plus the FINN accelerator.
# Then resume.tcl in a fresh session (2022.2 segfaults writing the wrapper here), then build.tcl.

set overlay_name "finn_hdmi"

create_project ${overlay_name} ./${overlay_name} -part xc7z020clg400-1 -force
set_property ip_repo_paths ../PYNQ/boards/ip [current_project]
update_ip_catalog -rebuild

source ./video_procs.tcl
source ./ps7_config.tcl

create_bd_design ${overlay_name}
current_bd_design ${overlay_name}


# Follows create_hier_cell_video in PYNQ's base.tcl, same M-port numbering.
proc create_hier_cell_video { parentCell nameHier } {

  set parentObj [get_bd_cells $parentCell]
  set oldCurInst [current_bd_instance .]
  current_bd_instance $parentObj

  set hier_obj [create_bd_cell -type hier $nameHier]
  current_bd_instance $hier_obj

  create_bd_intf_pin -mode Master -vlnv xilinx.com:interface:iic_rtl:1.0 DDC
  create_bd_intf_pin -mode Master -vlnv xilinx.com:interface:aximm_rtl:1.0 M_AXI
  create_bd_intf_pin -mode Slave -vlnv xilinx.com:interface:aximm_rtl:1.0 S_AXI
  create_bd_intf_pin -mode Slave -vlnv digilentinc.com:interface:tmds_rtl:1.0 TMDS_in
  create_bd_intf_pin -mode Master -vlnv digilentinc.com:interface:tmds_rtl:1.0 TMDS_out

  create_bd_pin -dir I -type clk clk_100M
  create_bd_pin -dir I clk_142M
  create_bd_pin -dir I -type clk clk_200M
  create_bd_pin -dir O -from 0 -to 0 hdmi_in_hpd
  create_bd_pin -dir O -from 0 -to 0 hdmi_out_hpd
  create_bd_pin -dir I -from 0 -to 0 -type rst ic_resetn_clk100M
  create_bd_pin -dir I -from 0 -to 0 -type rst ic_resetn_clk142M
  create_bd_pin -dir I -from 0 -to 0 -type rst periph_resetn_clk100M
  create_bd_pin -dir I -from 0 -to 0 -type rst periph_resetn_clk142M
  create_bd_pin -dir I -type rst system_resetn
  create_bd_pin -dir O -from 5 -to 0 video_irq

  set axi_interconnect_0 [ create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_interconnect_0 ]
  set_property -dict [list \
    CONFIG.M00_HAS_REGSLICE {1} CONFIG.M01_HAS_REGSLICE {1} \
    CONFIG.M02_HAS_REGSLICE {1} CONFIG.M03_HAS_REGSLICE {1} \
    CONFIG.M04_HAS_REGSLICE {1} CONFIG.M05_HAS_REGSLICE {1} \
    CONFIG.M06_HAS_REGSLICE {1} CONFIG.M07_HAS_REGSLICE {1} \
    CONFIG.M08_HAS_REGSLICE {1} CONFIG.M09_HAS_REGSLICE {1} \
    CONFIG.NUM_MI {10} CONFIG.S00_HAS_REGSLICE {1} \
  ] $axi_interconnect_0

  set axi_mem_intercon [ create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_mem_intercon ]
  set_property -dict [list CONFIG.NUM_MI {1} CONFIG.NUM_SI {2}] $axi_mem_intercon

  set axi_vdma [ create_bd_cell -type ip -vlnv xilinx.com:ip:axi_vdma:6.3 axi_vdma ]
  set_property -dict [list \
    CONFIG.c_m_axi_mm2s_data_width {32} \
    CONFIG.c_m_axis_mm2s_tdata_width {32} \
    CONFIG.c_mm2s_genlock_mode {1} \
    CONFIG.c_mm2s_linebuffer_depth {512} \
    CONFIG.c_mm2s_max_burst_length {32} \
    CONFIG.c_num_fstores {4} \
    CONFIG.c_s2mm_genlock_mode {0} \
    CONFIG.c_s2mm_linebuffer_depth {4096} \
    CONFIG.c_s2mm_max_burst_length {32} \
  ] $axi_vdma

  create_hier_cell_hdmi_in $hier_obj hdmi_in
  create_hier_cell_hdmi_out $hier_obj hdmi_out

  set proc_sys_reset_pixelclk [ create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 proc_sys_reset_pixelclk ]

  set xlconcat_0 [ create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat:2.1 xlconcat_0 ]
  set_property CONFIG.NUM_PORTS {6} $xlconcat_0

  connect_bd_intf_net [get_bd_intf_pins TMDS_out] [get_bd_intf_pins hdmi_out/TMDS_out]
  connect_bd_intf_net [get_bd_intf_pins TMDS_in] [get_bd_intf_pins hdmi_in/TMDS_in]
  connect_bd_intf_net [get_bd_intf_pins DDC] [get_bd_intf_pins hdmi_in/DDC]
  connect_bd_intf_net [get_bd_intf_pins S_AXI] [get_bd_intf_pins axi_interconnect_0/S00_AXI]
  connect_bd_intf_net [get_bd_intf_pins axi_interconnect_0/M00_AXI] [get_bd_intf_pins axi_vdma/S_AXI_LITE]
  connect_bd_intf_net [get_bd_intf_pins axi_interconnect_0/M01_AXI] [get_bd_intf_pins hdmi_out/S01_AXILite]
  connect_bd_intf_net [get_bd_intf_pins axi_interconnect_0/M02_AXI] [get_bd_intf_pins hdmi_out/S03_AXILite]
  connect_bd_intf_net [get_bd_intf_pins axi_interconnect_0/M03_AXI] [get_bd_intf_pins hdmi_out/S04_AXILite]
  connect_bd_intf_net [get_bd_intf_pins axi_interconnect_0/M04_AXI] [get_bd_intf_pins hdmi_out/S02_AXILite]
  connect_bd_intf_net [get_bd_intf_pins axi_interconnect_0/M05_AXI] [get_bd_intf_pins hdmi_out/S00_AXILite]
  connect_bd_intf_net [get_bd_intf_pins axi_interconnect_0/M06_AXI] [get_bd_intf_pins hdmi_in/S01_AXILite]
  connect_bd_intf_net [get_bd_intf_pins axi_interconnect_0/M07_AXI] [get_bd_intf_pins hdmi_in/S03_AXILite]
  connect_bd_intf_net [get_bd_intf_pins axi_interconnect_0/M08_AXI] [get_bd_intf_pins hdmi_in/S00_AXILite]
  connect_bd_intf_net [get_bd_intf_pins axi_interconnect_0/M09_AXI] [get_bd_intf_pins hdmi_in/S02_AXILite]
  connect_bd_intf_net [get_bd_intf_pins M_AXI] [get_bd_intf_pins axi_mem_intercon/M00_AXI]
  connect_bd_intf_net [get_bd_intf_pins axi_vdma/M_AXIS_MM2S] [get_bd_intf_pins hdmi_out/in_stream]
  connect_bd_intf_net [get_bd_intf_pins axi_mem_intercon/S01_AXI] [get_bd_intf_pins axi_vdma/M_AXI_MM2S]
  connect_bd_intf_net [get_bd_intf_pins axi_mem_intercon/S00_AXI] [get_bd_intf_pins axi_vdma/M_AXI_S2MM]
  connect_bd_intf_net [get_bd_intf_pins axi_vdma/S_AXIS_S2MM] [get_bd_intf_pins hdmi_in/out_stream]

  connect_bd_net [get_bd_pins ic_resetn_clk100M] [get_bd_pins axi_interconnect_0/ARESETN]
  connect_bd_net [get_bd_pins ic_resetn_clk142M] [get_bd_pins axi_mem_intercon/ARESETN]
  connect_bd_net [get_bd_pins clk_200M] [get_bd_pins hdmi_in/clk_200M]
  connect_bd_net [get_bd_pins clk_100M] \
    [get_bd_pins hdmi_in/clk_100M] [get_bd_pins hdmi_out/clk_100M] \
    [get_bd_pins axi_interconnect_0/ACLK] [get_bd_pins axi_interconnect_0/S00_ACLK] \
    [get_bd_pins axi_interconnect_0/M00_ACLK] [get_bd_pins axi_interconnect_0/M03_ACLK] \
    [get_bd_pins axi_interconnect_0/M04_ACLK] [get_bd_pins axi_interconnect_0/M05_ACLK] \
    [get_bd_pins axi_interconnect_0/M08_ACLK] [get_bd_pins axi_interconnect_0/M09_ACLK] \
    [get_bd_pins axi_vdma/s_axi_lite_aclk]
  connect_bd_net [get_bd_pins periph_resetn_clk100M] \
    [get_bd_pins hdmi_in/periph_resetn_clk100M] [get_bd_pins hdmi_out/periph_resetn_clk100M] \
    [get_bd_pins axi_interconnect_0/S00_ARESETN] [get_bd_pins axi_interconnect_0/M00_ARESETN] \
    [get_bd_pins axi_interconnect_0/M03_ARESETN] [get_bd_pins axi_interconnect_0/M04_ARESETN] \
    [get_bd_pins axi_interconnect_0/M05_ARESETN] [get_bd_pins axi_interconnect_0/M08_ARESETN] \
    [get_bd_pins axi_interconnect_0/M09_ARESETN] [get_bd_pins axi_vdma/axi_resetn]
  connect_bd_net [get_bd_pins clk_142M] \
    [get_bd_pins hdmi_in/clk_142M] [get_bd_pins hdmi_out/clk_142M] \
    [get_bd_pins axi_interconnect_0/M01_ACLK] [get_bd_pins axi_interconnect_0/M02_ACLK] \
    [get_bd_pins axi_interconnect_0/M06_ACLK] [get_bd_pins axi_interconnect_0/M07_ACLK] \
    [get_bd_pins axi_mem_intercon/ACLK] [get_bd_pins axi_mem_intercon/S00_ACLK] \
    [get_bd_pins axi_mem_intercon/M00_ACLK] [get_bd_pins axi_mem_intercon/S01_ACLK] \
    [get_bd_pins axi_vdma/m_axi_mm2s_aclk] [get_bd_pins axi_vdma/m_axis_mm2s_aclk] \
    [get_bd_pins axi_vdma/m_axi_s2mm_aclk] [get_bd_pins axi_vdma/s_axis_s2mm_aclk]
  connect_bd_net [get_bd_pins periph_resetn_clk142M] \
    [get_bd_pins hdmi_in/periph_resetn_clk142M] [get_bd_pins hdmi_out/periph_resetn_clk142M] \
    [get_bd_pins axi_interconnect_0/M01_ARESETN] [get_bd_pins axi_interconnect_0/M02_ARESETN] \
    [get_bd_pins axi_interconnect_0/M06_ARESETN] [get_bd_pins axi_interconnect_0/M07_ARESETN] \
    [get_bd_pins axi_mem_intercon/S00_ARESETN] [get_bd_pins axi_mem_intercon/M00_ARESETN] \
    [get_bd_pins axi_mem_intercon/S01_ARESETN]

  connect_bd_net [get_bd_pins hdmi_in/hdmi_in_hpd] [get_bd_pins hdmi_in_hpd]
  connect_bd_net [get_bd_pins hdmi_out/hdmi_out_hpd] [get_bd_pins hdmi_out_hpd]
  connect_bd_net [get_bd_pins system_resetn] [get_bd_pins proc_sys_reset_pixelclk/ext_reset_in]
  connect_bd_net [get_bd_pins hdmi_in/PixelClk] [get_bd_pins proc_sys_reset_pixelclk/slowest_sync_clk]
  connect_bd_net [get_bd_pins hdmi_in/aPixelClkLckd] [get_bd_pins proc_sys_reset_pixelclk/aux_reset_in]
  connect_bd_net [get_bd_pins proc_sys_reset_pixelclk/peripheral_reset] [get_bd_pins hdmi_in/vid_io_in_reset]
  connect_bd_net [get_bd_pins proc_sys_reset_pixelclk/peripheral_aresetn] [get_bd_pins hdmi_in/resetn]

  connect_bd_net [get_bd_pins axi_vdma/s2mm_introut] [get_bd_pins xlconcat_0/In0]
  connect_bd_net [get_bd_pins axi_vdma/mm2s_introut] [get_bd_pins xlconcat_0/In1]
  connect_bd_net [get_bd_pins hdmi_out/vtc_out_irq] [get_bd_pins xlconcat_0/In2]
  connect_bd_net [get_bd_pins hdmi_in/vtc_in_irq] [get_bd_pins xlconcat_0/In3]
  connect_bd_net [get_bd_pins hdmi_in/hdmi_in_hpd_irq] [get_bd_pins xlconcat_0/In4]
  connect_bd_net [get_bd_pins hdmi_out/hdmi_out_hpd_irq] [get_bd_pins xlconcat_0/In5]
  connect_bd_net [get_bd_pins xlconcat_0/dout] [get_bd_pins video_irq]

  current_bd_instance $oldCurInst
}


proc create_root_design { parentCell } {

  set parentObj [get_bd_cells $parentCell]
  if { $parentCell eq "" } { set parentObj [get_bd_cells /] }
  current_bd_instance $parentObj

  create_bd_intf_port -mode Slave -vlnv digilentinc.com:interface:tmds_rtl:1.0 hdmi_in
  create_bd_intf_port -mode Master -vlnv xilinx.com:interface:iic_rtl:1.0 hdmi_in_ddc
  create_bd_intf_port -mode Master -vlnv digilentinc.com:interface:tmds_rtl:1.0 hdmi_out
  create_bd_port -dir O -from 0 -to 0 hdmi_in_hpd
  create_bd_port -dir O -from 0 -to 0 hdmi_out_hpd

  set ps7_0 [ create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7_0 ]
  configure_ps7 $ps7_0
  set_property -dict [list \
    CONFIG.PCW_USE_M_AXI_GP1 {0} \
    CONFIG.PCW_USE_S_AXI_GP0 {0} \
    CONFIG.PCW_USE_S_AXI_HP2 {0} \
  ] $ps7_0

  set rst_ps7_0_fclk0 [ create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ps7_0_fclk0 ]
  set rst_ps7_0_fclk1 [ create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ps7_0_fclk1 ]

  set ps7_0_axi_periph [ create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 ps7_0_axi_periph ]
  set_property CONFIG.NUM_MI {2} $ps7_0_axi_periph

  # PYNQ needs an axi_intc or AxiVDMA fails to find its interrupt
  set system_interrupts [ create_bd_cell -type ip -vlnv xilinx.com:ip:axi_intc:4.1 system_interrupts ]

  create_hier_cell_video [current_bd_instance .] video

  connect_bd_intf_net [get_bd_intf_pins ps7_0/M_AXI_GP0] [get_bd_intf_pins ps7_0_axi_periph/S00_AXI]
  connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/M00_AXI] [get_bd_intf_pins system_interrupts/s_axi]
  connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/M01_AXI] [get_bd_intf_pins video/S_AXI]
  connect_bd_intf_net [get_bd_intf_pins ps7_0/S_AXI_HP0] [get_bd_intf_pins video/M_AXI]
  connect_bd_intf_net [get_bd_intf_ports hdmi_out] [get_bd_intf_pins video/TMDS_out]
  connect_bd_intf_net [get_bd_intf_ports hdmi_in] [get_bd_intf_pins video/TMDS_in]
  connect_bd_intf_net [get_bd_intf_ports hdmi_in_ddc] [get_bd_intf_pins video/DDC]

  connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] \
    [get_bd_pins ps7_0/M_AXI_GP0_ACLK] [get_bd_pins video/clk_100M] \
    [get_bd_pins ps7_0_axi_periph/ACLK] [get_bd_pins ps7_0_axi_periph/S00_ACLK] \
    [get_bd_pins ps7_0_axi_periph/M00_ACLK] [get_bd_pins ps7_0_axi_periph/M01_ACLK] \
    [get_bd_pins system_interrupts/s_axi_aclk] [get_bd_pins rst_ps7_0_fclk0/slowest_sync_clk]
  connect_bd_net [get_bd_pins ps7_0/FCLK_CLK1] \
    [get_bd_pins ps7_0/S_AXI_HP0_ACLK] [get_bd_pins video/clk_142M] \
    [get_bd_pins rst_ps7_0_fclk1/slowest_sync_clk]
  connect_bd_net [get_bd_pins ps7_0/FCLK_CLK2] [get_bd_pins video/clk_200M]
  connect_bd_net [get_bd_pins ps7_0/FCLK_RESET0_N] \
    [get_bd_pins video/system_resetn] \
    [get_bd_pins rst_ps7_0_fclk0/ext_reset_in] [get_bd_pins rst_ps7_0_fclk1/ext_reset_in]

  connect_bd_net [get_bd_pins rst_ps7_0_fclk0/interconnect_aresetn] \
    [get_bd_pins video/ic_resetn_clk100M] [get_bd_pins ps7_0_axi_periph/ARESETN]
  connect_bd_net [get_bd_pins rst_ps7_0_fclk0/peripheral_aresetn] \
    [get_bd_pins video/periph_resetn_clk100M] \
    [get_bd_pins ps7_0_axi_periph/S00_ARESETN] [get_bd_pins ps7_0_axi_periph/M00_ARESETN] \
    [get_bd_pins ps7_0_axi_periph/M01_ARESETN] [get_bd_pins system_interrupts/s_axi_aresetn]
  connect_bd_net [get_bd_pins rst_ps7_0_fclk1/interconnect_aresetn] [get_bd_pins video/ic_resetn_clk142M]
  connect_bd_net [get_bd_pins rst_ps7_0_fclk1/peripheral_aresetn] [get_bd_pins video/periph_resetn_clk142M]

  connect_bd_net [get_bd_pins video/hdmi_in_hpd] [get_bd_ports hdmi_in_hpd]
  connect_bd_net [get_bd_pins video/hdmi_out_hpd] [get_bd_ports hdmi_out_hpd]
  connect_bd_net [get_bd_pins video/video_irq] [get_bd_pins system_interrupts/intr]
  connect_bd_net [get_bd_pins system_interrupts/irq] [get_bd_pins ps7_0/IRQ_F2P]

  assign_bd_address -offset 0x41800000 -range 0x00010000 -target_address_space [get_bd_addr_spaces ps7_0/Data] [get_bd_addr_segs system_interrupts/S_AXI/Reg] -force
  assign_bd_address -offset 0x43000000 -range 0x00010000 -target_address_space [get_bd_addr_spaces ps7_0/Data] [get_bd_addr_segs video/axi_vdma/S_AXI_LITE/Reg] -force
  assign_bd_address -offset 0x43C10000 -range 0x00010000 -target_address_space [get_bd_addr_spaces ps7_0/Data] [get_bd_addr_segs video/hdmi_out/frontend/axi_dynclk/s00_axi/reg0] -force
  assign_bd_address -offset 0x43C20000 -range 0x00010000 -target_address_space [get_bd_addr_spaces ps7_0/Data] [get_bd_addr_segs video/hdmi_out/frontend/vtc_out/ctrl/Reg] -force
  assign_bd_address -offset 0x43C60000 -range 0x00010000 -target_address_space [get_bd_addr_spaces ps7_0/Data] [get_bd_addr_segs video/hdmi_out/color_convert/s_axi_control/Reg] -force
  assign_bd_address -offset 0x43C70000 -range 0x00010000 -target_address_space [get_bd_addr_spaces ps7_0/Data] [get_bd_addr_segs video/hdmi_out/pixel_unpack/s_axi_control/Reg] -force
  assign_bd_address -offset 0x41230000 -range 0x00010000 -target_address_space [get_bd_addr_spaces ps7_0/Data] [get_bd_addr_segs video/hdmi_out/frontend/hdmi_out_hpd_video/S_AXI/Reg] -force
  assign_bd_address -offset 0x41220000 -range 0x00010000 -target_address_space [get_bd_addr_spaces ps7_0/Data] [get_bd_addr_segs video/hdmi_in/frontend/axi_gpio_hdmiin/S_AXI/Reg] -force
  assign_bd_address -offset 0x43C30000 -range 0x00010000 -target_address_space [get_bd_addr_spaces ps7_0/Data] [get_bd_addr_segs video/hdmi_in/frontend/vtc_in/ctrl/Reg] -force
  assign_bd_address -offset 0x43C40000 -range 0x00010000 -target_address_space [get_bd_addr_spaces ps7_0/Data] [get_bd_addr_segs video/hdmi_in/pixel_pack/s_axi_control/Reg] -force
  assign_bd_address -offset 0x43C50000 -range 0x00010000 -target_address_space [get_bd_addr_spaces ps7_0/Data] [get_bd_addr_segs video/hdmi_in/color_convert/s_axi_control/Reg] -force

  # all of DDR, not the base overlay's 0x10000000 window
  assign_bd_address -offset 0x00000000 -range 0x20000000 -target_address_space [get_bd_addr_spaces video/axi_vdma/Data_MM2S] [get_bd_addr_segs ps7_0/S_AXI_HP0/HP0_DDR_LOWOCM] -force
  assign_bd_address -offset 0x00000000 -range 0x20000000 -target_address_space [get_bd_addr_spaces video/axi_vdma/Data_S2MM] [get_bd_addr_segs ps7_0/S_AXI_HP0/HP0_DDR_LOWOCM] -force

  source ./overrides.tcl

  validate_bd_design
  save_bd_design
}

create_root_design ""

puts "BLOCK DESIGN OK"
