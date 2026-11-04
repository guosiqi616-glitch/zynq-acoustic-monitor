# ============================================================================
# run_board.tcl  --  Zynq-7010 audio monitor: download PL bitstream + run PS app
#
# Usage (from any directory):
#     D:\Xilinx\Vitis\2020.2\bin\xsct.bat D:\Xilinx\S\project\project_1\run_board.tcl
#
# Prerequisites:
#   1. Board powered, JTAG cable connected, USB plugged in
#   2. No other program holding hw_server (close Vivado Hardware Manager / Vitis debug)
#   3. Open the CH340 COM port at 115200 8N1 in XCOM BEFORE running this script
#      (port number changes after replug: was COM6, then COM5 -- check Device Manager)
#
# NOTE: all console messages are ASCII on purpose, so they stay readable
#       regardless of the Windows code page (chcp 936 / 65001).
# ============================================================================

# bitstream: prefer the newest implementation output, fall back to the exported copy
set BIT_RUN "D:/Xilinx/S/project/project_1/project_1.runs/impl_1/design_1_wrapper.bit"
set BIT_HW  "D:/Xilinx/S/project/project_1/vitis_workspace/design_1_wrapper/hw/design_1_wrapper.bit"
if {[file exists $BIT_RUN]} { set BIT $BIT_RUN } else { set BIT $BIT_HW }

set ELF  "D:/Xilinx/S/project/project_1/vitis_workspace/anomaly_detect_app/Debug/anomaly_detect_app.elf"

foreach f [list $BIT $ELF] {
    if {![file exists $f]} { puts "\[FATAL\] file not found: $f"; exit 1 }
}
puts "\[INFO\] BIT = $BIT"
puts "\[INFO\] BIT mtime = [clock format [file mtime $BIT] -format {%Y-%m-%d %H:%M:%S}]"
puts "\[INFO\] ELF = $ELF"
puts "\[INFO\] ELF mtime = [clock format [file mtime $ELF] -format {%Y-%m-%d %H:%M:%S}]"

connect

puts "\n\[INFO\] ===== JTAG chain ====="
puts [jtag targets]

# ---- 1. reset the whole system so the TAP starts from a clean state --------
targets -set -filter {name =~ "APU*"}
puts "\n\[INFO\] rst -system ..."
rst -system
after 3000

# ---- 2. program the PL -----------------------------------------------------
puts "\[INFO\] downloading bitstream ..."
fpga -file $BIT
after 1000

puts "\n\[INFO\] ===== JTAG chain after download ====="
puts [jtag targets]

# ---- 2.5 init PS (DDR / PLL / MIO). Skip this and dow fails with
#          "Cannot access DDR: the controller is held in reset"
set PS7  "D:/Xilinx/S/project/project_1/vitis_workspace/design_1_wrapper/hw/ps7_init.tcl"
if {![file exists $PS7]} { puts "\[FATAL\] ps7_init.tcl not found: $PS7"; exit 1 }
targets -set -filter {name =~ "APU*"}
puts "\n\[INFO\] ps7_init ..."
source $PS7
ps7_init
ps7_post_config
after 500

# ---- 3. download ELF and run ----------------------------------------------
puts "\n\[INFO\] downloading ELF ..."
targets -set -filter {name =~ "ARM*#0"}
rst -processor
dow $ELF
puts "\[INFO\] starting CPU ..."
con

puts "\n\[INFO\] RUNNING. Now watch the CH340 COM port at 115200 (currently COM5)."
puts "\[INFO\] Expected console output:"
puts "\[INFO\]   Zynq-7010 v1.0 protocol ready"
puts "\[INFO\]   \[INFO\] SHT30 I2C ready at 0x44"
puts "\[INFO\]   \[INFO\] T=50 Hz, I/S=1 Hz, UART=115200, v1 frames"
puts "\[INFO\]   \[INFO\] AXI GPIO audio energy at 0x41200000"
puts "\[INFO\]   \[DIAG\] TRI1=... TRI2=... (expect FFFFFFFF FFFFFFFF)"
puts "\[INFO\]   \[INFO\] first energy low=... high=... (hex ... ...)"
puts "\[INFO\]   \[RAW\] 0 ... ...   (4 lines within the first 2 seconds)"
puts "\[INFO\]   T,0,<us>,<t100>,<h100>,<db_low>,<db_high>,<ber>,<state>,<lock>,<det>,0"
exit
