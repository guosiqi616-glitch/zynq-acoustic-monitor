# PC 渚у伐鍏峰寘锛坔ost_software锛?
鏈烘寮傚搷鐩戞祴绯荤粺鐨?PC 渚ц蒋浠讹紝涓?Vivado / Vitis 宸ョ▼鍒嗗紑瀛樻斁锛岄伩鍏嶅湪宸ョ▼鐩綍閲屾壘涓嶅埌銆?
**鎵€鏈夊懡浠ら兘鍦ㄦ湰鐩綍锛坄host_software`锛変笅鎵ц銆?*

## 鐩綍

| 璺緞 | 鍐呭 |
|---|---|
| [docs/PROTOCOL.md](docs/PROTOCOL.md) | 璁惧鈥斾笂浣嶆満閫氫俊鍗忚 v1.0锛堝凡鍐荤粨锛?|
| [protocol/vectors.json](protocol/vectors.json) | 33 鏉″崗璁祴璇曞悜閲忥紝C / JS 鍏辩敤 |
| [lib/protocol.py](lib/protocol.py) | 鍏变韩瑙ｆ瀽鍣紙conformance / 妗ユ帴 / 鎺㈤拡鍏辩敤锛?|
| [relay/server.py](relay/server.py) | 妗ユ帴鏈嶅姟锛歍CP/涓插彛 鈫?WebSocket/HTTP |
| [web/](web/) | 鐪嬫澘椤甸潰锛堝師鐢?JS锛屾棤鏋勫缓锛?|
| [tools/conformance.py](tools/conformance.py) | 鍗忚涓€鑷存€ф鏌ャ€佹棩蹇楀洖鏀?|
| [tools/hardware_check.py](tools/hardware_check.py) | 纭欢鎺ュ叆 60 s 鑷锛氱瓑 I銆佹煡閰嶇疆銆乻eq / det / t_us / 甯х巼銆丳ASS/FAIL |
| [tools/ws_probe.py](tools/ws_probe.py) | 鏃犲ご WebSocket 鎺㈤拡 |
| [sim/sim_device.py](sim/sim_device.py) | 璁惧妯℃嫙鍣紝鏃犵‖浠舵椂鏇夸唬 Zynq |
| [docs/HARDWARE_BRINGUP.md](docs/HARDWARE_BRINGUP.md) | 10 鍒嗛挓纭欢鎺ュ叆娓呭崟涓庢晠闅滈€熸煡 |
| `records/` | 娴嬭瘯鏃ュ織锛屽彲鐩存帴鍥炴斁 |

## 蹇€熷紑濮?
### 1. 鑷妯℃嫙鍣?
```bash
python sim/sim_device.py --selftest
```

搴旇緭鍑?`PASS`锛屽苟缁欏嚭 60 绉掑唴鐨?T/E/lock/det/supp 缁熻銆?
### 2. 瀹炴椂妯℃嫙 + 閿洏娉ㄥ叆

```bash
python sim/sim_device.py
```

閿綅锛?
| 閿?| 浣滅敤 |
|---|---|
| `D` | 鏂嚎 3 绉掞紙t_us 缁х画锛宻eq 缁х画锛?|
| `B` | 閲嶅惎锛坱_us 褰掗浂銆乻eq 褰掗浂銆佽鏁版竻闆讹級 |
| `X` | 涓竴甯?T锛堣烦 seq锛?|
| `V` | 涓竴甯?E锛坙ock++ 浣嗕笉鍙戯級 |
| `P` | 30 绉掓紨绀猴細瀹夐潤 鈫?棰勮 鈫?鎶ヨ 鈫?鎭㈠ |
| `L` | 鎵嬪姩瑙﹀彂涓€娆￠攣瀛?|
| `S` | 浼犳劅鍣ㄥけ鏁?2 绉?|
| `Q` | 閫€鍑?|

### 3. 鐢熸垚鏃ュ織骞跺仛涓€鑷存€ф鏌?
```bash
python sim/sim_device.py --fast --duration 60 --seed 42 --quiet --sink file:records/sim60.log
python tools/conformance.py --source file:records/sim60.log
```

姝ｅ父鏃ュ織搴斾负 `PASS`锛?0 Hz銆佷涪甯?0銆乣det = lock + supp`銆丒 甯у畬鏁淬€?
### 4. 鍚姩妗ユ帴鏈嶅姟

缁堢 1锛堟ā鎷熷櫒锛屽甫鎺у埗绔彛锛岄〉闈?`L`/`P` 閿鐢級锛?
```bash
python sim/sim_device.py --sink tcp:0.0.0.0:9100 --control tcp:0.0.0.0:9101
```

缁堢 2锛堟ˉ鎺ワ紝`--sim-control` 璁╅〉闈㈠彲浠ユ敞鍏?`L`/`P`锛夛細

```bash
python relay/server.py --source tcp:127.0.0.1:9100 --port 8080 --sim-control tcp:127.0.0.1:9101
```

娴忚鍣ㄦ墦寮€ `http://127.0.0.1:8080/`銆?
鐪嬫澘蹇嵎閿細

| 閿?| 浣滅敤 |
|---|---|
| `F` | 鍏ㄥ睆 |
| `D` | 璋冭瘯闈㈡澘锛圵S / 鐘舵€?/ 璁℃暟 / 寤惰繜锛?|
| `L` | 鎵嬪姩閿佸瓨锛堥渶 `--sim-control`锛?|
| `P` | 30 绉掓紨绀猴紙闇€ `--sim-control`锛?|
| `R` | 鍥炴斁锛堝綋鍓嶅彧鎻愮ず锛涚敤 `--source replay:...` 閲嶅惎妗ユ帴锛?|

鏁版嵁婧愬窘绔狅細

| 寰界珷 | 鏉ユ簮 |
|---|---|
| `SIM` | `tcp:` 妯℃嫙鍣?|
| `BOARD` | `serial:` 涓插彛 |
| `REPLAY` | `replay:` / `file:` |

闇€瑕佹椂鍙敤 `--mode board` 寮哄埗鎸囧畾銆傜湅鏉挎寜 1920脳1080 涓夊垪 + 搴曟爮璁捐锛宍web/echarts.min.js` 鏄湰鍦版枃浠讹紝涓嶉渶瑕?Node 鎴?CDN銆?
鏃犲ご楠岃瘉锛?
```bash
python tools/ws_probe.py --url ws://127.0.0.1:8080/ws --seconds 5
```

搴旂湅鍒?`hello 1`銆乣telemetry 鈮?5/s`銆乣PASS`銆?
### 5. 鍙鐜扮殑鏁呴殰搴忓垪

```bash
python sim/sim_device.py --fast --duration 15 --seed 5 \
  --script "3:d,6:b,9:x,11:v,12:l" \
  --sink file:records/faults.log
```

杩欐潯浼氭晠鎰忓埗閫狅細鏂嚎銆侀噸鍚€佷涪 T銆佷涪 E銆傜敤涓€鑷存€ц剼鏈鏌ユ椂搴旂湅鍒?`閲嶅惎 1 娆銆乣seq 涓㈠け`銆乣E 甯у畬鏁存€?FAIL`鈥斺€旇繖鏄鏈熺粨鏋滐紝鐢ㄦ潵楠岃瘉鍓嶇鑳藉彂鐜板紓甯搞€?
### 6. 褰曞埗涓庡洖鏀?
```bash
# 鍚姩鏃跺綍鍒?python relay/server.py --source tcp:127.0.0.1:9100 --record records/session.jsonl

# 鍥炴斁褰曞埗鏂囦欢
python relay/server.py --source replay:records/session.jsonl --port 8080
```

涔熷彲浠ヨ繍琛屾椂閫氳繃 `POST /api/record/start`銆乣POST /api/record/stop` 鎺у埗銆?
### 7. 璋冩暣鐪嬫澘甯冨眬

- 鎷栧姩**鏍忛棿/鍖哄潡闂寸殑鍒嗛殧鏉?*锛堥紶鏍囩Щ鍒扮紳闅欏浼氬彉浜級璋冩暣澶у皬锛?- 宸?涓?鍙充笁鏍忓搴︺€佸彸渚т笁鍧楅珮搴︺€佸簳鏍忛珮搴﹂兘鍙皟锛岀浉閭绘ā鍧楄嚜鍔ㄨ浣嶏紝鐣岄潰濮嬬粓鏄竴鏁村潡锛?- 甯冨眬淇濆瓨鍦ㄦ祻瑙堝櫒 `localStorage`锛屽埛鏂颁笉涓紱
- 椤舵爮 `鈫?甯冨眬` 鎭㈠榛樿锛?- 瀹藉害灏忎簬 1100px 鏃惰嚜鍔ㄦ敼涓虹旱鍚戝爢鍙狅紝涓嶅啀鍚敤鍒嗛殧鏉°€?
### 8. 纭欢鎺ュ叆锛?0 鍒嗛挓锛?
鍏堝叧闂?XCOM / 涓插彛鍔╂墜鍜?`relay/server.py`锛堝畠浠兘鐙崰 COM锛夛紝鍐嶈繍琛岋細

```powershell
..\.venv\Scripts\python.exe tools/hardware_check.py --list-ports
..\.venv\Scripts\python.exe tools/hardware_check.py --port COM5 --baud 115200
```

宸ュ叿浼氱瓑 I 甯э紙3 绉掕秴鏃剁粰鎺掓煡娓呭崟锛夈€佹墦鍗?fs/win/bands銆佽窇 60 绉掓鏌?`seq` / `det = lock + supp` / `t_us` / 甯х巼锛屾渶鍚庤緭鍑?PASS/FAIL 鍜屽叿浣撳け璐ュ抚銆?娉ㄦ剰锛歅S 蹇呴』鍏堢儳 v1 鍗忚鍥轰欢锛圛/T/E/S锛夛紝鏃х増 `Temp:...` 瀛楃涓蹭笉浼氭湁 I 甯с€?
PASS 鍚庡惎鍔?BOARD 妗ユ帴锛?
```powershell
..\.venv\Scripts\python.exe relay\server.py --source serial:COM5@115200 --port 8080 --mode board
```

娴忚鍣ㄦ墦寮€ `http://127.0.0.1:8080/`锛屽窘绔犲簲浠?`SIM` 鍙樻垚 `BOARD`銆?瀹屾暣娓呭崟锛圠atency Timer銆乀X/RX銆佹尝鐗圭巼銆乣%f`銆佸崐琛?涔辩爜銆乻eq 鍥炵粫绛夛級瑙?[docs/HARDWARE_BRINGUP.md](docs/HARDWARE_BRINGUP.md)銆?
## 甯哥敤鍙傛暟

| 鍙傛暟 | 璇存槑 |
|---|---|
| `--seed 42` | 鍥哄畾闅忔満绉嶅瓙锛屾棩蹇楀彲澶嶇幇 |
| `--fast` | 涓嶆寜澧欓挓锛屽敖蹇敓鎴愶紙闇€閰嶅悎 `--duration`锛?|
| `--duration 60` | 妯℃嫙鏃堕暱锛堢锛?|
| `--sink stdout` | 杈撳嚭鍒扮粓绔紙榛樿锛?|
| `--sink file:PATH` | 鍐欐枃浠讹紝瑕嗙洊 |
| `--sink file+:PATH` | 鍐欐枃浠讹紝杩藉姞 |
| `--sink tcp:HOST:PORT` | 鐩戝惉 TCP锛屼緵涓户杩炴帴 |
| `--sink serial:COM5@115200` | 鍐欎覆鍙ｏ紙闇€瑕?pyserial锛?|
| `--control tcp:0.0.0.0:9101` | 杩滅▼鍗曞瓧绗︽帶鍒讹紝渚?Web 椤甸潰娉ㄥ叆鏁呴殰 |
| `--script "3:d,6:b"` | 瀹氭椂娉ㄥ叆锛屼究浜庡鐜?|

## 渚濊禆

妗ユ帴鏍稿績锛圱CP / HTTP / WebSocket / 鍥炴斁锛?*闆剁涓夋柟渚濊禆**锛屽彧鐢?Python 鏍囧噯搴撱€?
| 鐢ㄩ€?| 瀹夎 |
|---|---|
| 涓插彛鎺ョ湡瀹炶澶?| `pip install pyserial`锛堜笉闇€瑕?pyserial-asyncio锛?|
| 鑷姩鍖栨祴璇曪紙鍙€夛級 | `pip install pytest` |
| 鎶ュ憡鐢诲浘锛堝彲閫夛級 | `pip install numpy matplotlib` |

`aiohttp` / `websockets` **涓嶉渶瑕?*锛沗relay/ws.py` 宸茬粡鏄彲鐢ㄧ殑鏈€灏?WebSocket 鏈嶅姟绔€?鍓嶇涔熶笉闇€瑕?Node/npm锛宍web/` 鏄函闈欐€佹枃浠躲€?
- Python 3.10+锛堟祴璇曚簬 3.12锛?
## 鍗忚鐗堟湰

褰撳墠鍗忚 v1.0锛屽喕缁撲簬 2026-10-22銆傚瓧娈点€佸崟浣嶃€佺増鏈鍒欒
[docs/PROTOCOL.md](docs/PROTOCOL.md)锛涙敼鍗忚鍓嶅厛鏀?[protocol/vectors.json](protocol/vectors.json)锛屽啀鏀逛袱绔唬鐮併€?
