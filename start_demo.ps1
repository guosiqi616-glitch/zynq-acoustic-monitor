# 一键启动演示系统：检测串口 -> 烧写 -> 起服务 -> 开浏览器
# 用法：双击 启动演示.bat

$ErrorActionPreference = "Stop"
$Root = "D:\Xilinx\S\project\project_1"
$Xsct = "D:\Xilinx\Vitis\2020.2\bin\xsct.bat"
$Py   = "$Root\.venv\Scripts\python.exe"
$Relay = "$Root\host_software\relay\server.py"

function Info($m) { Write-Host $m -ForegroundColor Cyan }
function Warn($m) { Write-Host $m -ForegroundColor Yellow }
function Fail($m) { Write-Host $m -ForegroundColor Red; Read-Host "按回车退出"; exit 1 }

Write-Host ""
Write-Host "===============================================" -ForegroundColor DarkGray
Write-Host "  机械异响监测系统  一键启动" -ForegroundColor White
Write-Host "===============================================" -ForegroundColor DarkGray
Write-Host ""

# ---- 1. 找 CH340 串口 ----
Info "[1/4] 查找板载串口 ..."
$dev = Get-CimInstance Win32_PnPEntity | Where-Object { $_.Name -match 'COM\d+' -and $_.Name -match 'CH340|USB-SERIAL|USB Serial' }
if (-not $dev) {
    Warn "没有找到 CH340 串口，说明板子的 USB 线没插、或者板子没上电。"
    Warn "请确认：① USB 线插在板子的 USB-UART 口 ② DC 电源已接、PWR 灯亮"
    Read-Host "按回车重试，或关闭窗口"
    exit 1
}
$port = ([regex]::Match($dev[0].Name, 'COM\d+')).Value
Info "      找到串口: $port"

# ---- 2. 烧写 ----
Info "[2/4] 烧写 PL + PS（约 20 秒）..."
Push-Location $Root
& $Xsct run_board.tcl 2>&1 | Select-String -Pattern 'RUNNING|FATAL|error' | ForEach-Object { "      " + $_.Line }
Pop-Location
if ($LASTEXITCODE -ne 0) { Warn "      烧写脚本返回非零，继续尝试启动服务" }

# ---- 3. 起服务 ----
Info "[3/4] 启动上位机服务 ..."
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'relay\\server\.py' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

Start-Process -FilePath $Py `
    -ArgumentList $Relay, "--source", "serial:$port@115200", "--port", "8080", "--mode", "board" `
    -WorkingDirectory "$Root\host_software" -WindowStyle Hidden `
    -RedirectStandardOutput "$Root\records\relay.out.log" `
    -RedirectStandardError  "$Root\records\relay.err.log"

# ---- 4. 等就绪并打开浏览器 ----
Info "[4/4] 等待数据 ..."
$ok = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 800
    try {
        $h = (Invoke-WebRequest "http://127.0.0.1:8080/api/health" -UseBasicParsing -TimeoutSec 2).Content | ConvertFrom-Json
        if ($h.connected -and $h.rx -gt 100) { $ok = $true; break }
    } catch { }
}

if ($ok) {
    Write-Host ("      OK  帧率 " + $h.fps + " Hz  丢帧 " + $h.lost) -ForegroundColor Green
} else {
    Warn "      服务启动了，但还没收到数据。检查板子是否在运行。"
}

Start-Process "http://127.0.0.1:8080/dashboard.html"
Write-Host ""
Write-Host "已打开浏览器。按 Ctrl+Shift+R 可强制刷新。" -ForegroundColor Green
Write-Host ""
Start-Sleep -Seconds 2
