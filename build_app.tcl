setws D:/Xilinx/S/project/project_1/vitis_workspace
puts "=== apps ==="
puts [app list]
app build anomaly_detect_app
puts "=== elf check ==="
set elf D:/Xilinx/S/project/project_1/vitis_workspace/anomaly_detect_app/Debug/anomaly_detect_app.elf
puts "ELF exists = [file exists $elf]  mtime = [file mtime $elf]"
puts "APP_BUILD_DONE"
exit
