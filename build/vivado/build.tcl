# Synth, impl, .bit and .hwh.

set overlay_name "finn_hdmi"

set_param general.maxThreads 3

open_project ./${overlay_name}/${overlay_name}.xpr
open_bd_design ./${overlay_name}/${overlay_name}.srcs/sources_1/bd/${overlay_name}/${overlay_name}.bd

set_property strategy Performance_Auto_1 [get_runs impl_1]
launch_runs impl_1 -to_step write_bitstream -jobs 3
wait_on_run impl_1

if {[get_property PROGRESS [get_runs impl_1]] != "100%"} {
    puts "ERROR: implementation did not finish"
    exit 1
}

file copy -force ./${overlay_name}/${overlay_name}.runs/impl_1/${overlay_name}_wrapper.bit ./${overlay_name}.bit
file copy -force ./${overlay_name}/${overlay_name}.gen/sources_1/bd/${overlay_name}/hw_handoff/${overlay_name}.hwh ./${overlay_name}.hwh

open_run impl_1
puts "=== UTILISATION ==="
report_utilization -hierarchical -file ./utilisation.rpt
puts [report_utilization -return_string]

set fd [open ./${overlay_name}/${overlay_name}.runs/impl_1/${overlay_name}_wrapper_timing_summary_routed.rpt r]
set timing_met 0
while { [gets $fd line] >= 0 } {
    if [string match {All user specified timing constraints are met.} $line] { set timing_met 1; break }
}
close $fd
if {$timing_met == 0} {
    puts "TIMING NOT MET"
} else {
    puts "TIMING MET"
}
puts "BUILD OK"
