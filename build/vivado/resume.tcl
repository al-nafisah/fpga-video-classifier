# Wrapper, in a fresh session.
set overlay_name "finn_hdmi"
open_project ./${overlay_name}/${overlay_name}.xpr
open_bd_design ./${overlay_name}/${overlay_name}.srcs/sources_1/bd/${overlay_name}/${overlay_name}.bd

make_wrapper -files [get_files ./${overlay_name}/${overlay_name}.srcs/sources_1/bd/${overlay_name}/${overlay_name}.bd] -top
add_files -norecurse ./${overlay_name}/${overlay_name}.gen/sources_1/bd/${overlay_name}/hdl/${overlay_name}_wrapper.v
add_files -fileset constrs_1 -norecurse ./constraints.xdc
set_property top ${overlay_name}_wrapper [current_fileset]
update_compile_order -fileset sources_1
puts "WRAPPER OK"
