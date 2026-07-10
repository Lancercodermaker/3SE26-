# Windows SDR Python Wrapper 部署说明

本文档用于在 Windows 开发机上运行 `sdr_receiver_py_wrapper` 的离线检查、旁路调试工具和可选 Pluto 真机接收测试。

推荐 Windows 用途：

- 离线验证 wrapper/import/monkey patch 是否正常。
- 运行 `weak_info_probe`、`rf_power_scan` 等不依赖 ROS2 topic 的工具。
- 重新生成 `dist/sdr_receiver_py_wrapper-0.1.0.tar.gz`，再部署到 Ubuntu ROS2 机器。

不推荐 Windows 用途：

- 直接运行比赛 ROS2 node 主线。除非本机已经完整安装并配置 Windows 版 ROS2、相关 message 包和运行环境，否则比赛主线仍建议在 Ubuntu 22.04 + ROS2 Humble 上运行。

## 1. 环境要求

基础环境：

```text
Windows 10/11
Python 3.10 或 3.11
PowerShell
Git 可选
```

真机 SDR 测试额外需要：

```text
Analog Devices libiio Windows runtime
pyadi-iio
PlutoSDR 通过 USB 网络或以太网可访问
默认 URI: ip:192.168.2.1
```

Pluto/AD936x 接收端硬件增益最高是 `73 dB`。本 wrapper 的 sweep/probe 工具会把高于 73 的候选裁剪到 73，不要把 80 当作有效 RX gain。

## 2. 创建 Windows venv

在 PowerShell 中进入 wrapper 包目录：

```powershell
cd E:\sdr\iq_recevier\sdr_receiver_py_wrapper
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip setuptools wheel
pip install -r requirements.txt
```

如果本机没有 `py -3.10` launcher，也可以直接使用：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

如果 PowerShell 阻止激活脚本，可在当前用户范围放宽执行策略：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## 3. 安装当前包

开发模式安装：

```powershell
cd E:\sdr\iq_recevier\sdr_receiver_py_wrapper
pip install -e .
```

或者从已生成的源码包安装：

```powershell
pip install .\dist\sdr_receiver_py_wrapper-0.1.0.tar.gz
```

如果只是从源码目录运行 `python -m ...`，也可以不安装包，但建议安装一次，避免路径问题。

## 4. 离线 smoke test

不连接 SDR、不安装 libiio 时，也可以运行：

```powershell
python -m sdr_receiver_py_wrapper.offline_smoke_test --allow-adi-stub
```

期望看到类似输出：

```text
import smoke ok: ... main_not_executed=True
patch smoke ok: captured fake 0x0A06 key ABC123
weak_soft smoke ok: soft AC payload entered pool and produced SOF
```

这个检查确认：

- 能 import 包内 v67 原脚本，且不会自动执行 `main()`。
- monkey patch 能捕获 fake jam key。
- `weak_soft` soft AC fallback 能把 payload 投入原脚本 bit pool，并触发 SOF 处理路径。

## 5. 常用离线命令

查看工具参数：

```powershell
python -m sdr_receiver_py_wrapper.adaptive_profile_sweep --help
python -m sdr_receiver_py_wrapper.weak_info_probe --help
python -m sdr_receiver_py_wrapper.rf_power_scan --help
python -m sdr_receiver_py_wrapper.rf_iq_diff_capture --help
```

编译检查：

```powershell
Get-ChildItem -Path .\sdr_receiver_py_wrapper -Filter *.py |
  ForEach-Object { python -m py_compile $_.FullName }
```

重新打包：

```powershell
python setup.py sdist --formats=gztar
Get-FileHash .\dist\sdr_receiver_py_wrapper-0.1.0.tar.gz -Algorithm SHA256
```

## 6. Pluto/libiio 真机检查

先确认 Pluto 网络可达：

```powershell
ping 192.168.2.1
```

如果安装了 libiio 工具，确认 IIO context：

```powershell
iio_info -u ip:192.168.2.1
```

如果 `iio_info` 不存在，说明 libiio command line tools 没进 `PATH`，但 Python 仍可能通过 pyadi-iio 工作。可以用下面的最小 Python 检查：

```powershell
@'
import adi
sdr = adi.Pluto("ip:192.168.2.1")
print("sample_rate", sdr.sample_rate)
print("rx_lo", sdr.rx_lo)
'@ | python -
```

也可以改用一行：

```powershell
python -c "import adi; sdr=adi.Pluto('ip:192.168.2.1'); print('sample_rate', sdr.sample_rate); print('rx_lo', sdr.rx_lo)"
```

## 7. Windows 运行 RF 工具

Windows 上没有 ROS2 环境时，使用 `python -m` 直接运行工具。

RF 功率扫描：

```powershell
python -m sdr_receiver_py_wrapper.rf_power_scan --all-known --gain 60
python -m sdr_receiver_py_wrapper.rf_power_scan --red-info --gain 73
```

弱 INFO IQ 旁路探测：

```powershell
python -m sdr_receiver_py_wrapper.weak_info_probe -- `
  --team RED `
  --gains 70,73 `
  --rf-bws 220000,300000,420000,540000 `
  --offsets-hz 0,-80000,80000,-150000,150000,-250000,250000 `
  --info-filters normal,loose3 `
  --captures 3
```

如果怀疑有窄带杂散：

```powershell
python -m sdr_receiver_py_wrapper.weak_info_probe -- `
  --team RED `
  --gains 73 `
  --rf-bws 160000,220000,300000,420000,540000 `
  --offsets-hz 0,-80000,80000,-150000,150000,-250000,250000 `
  --info-filters normal,loose3,wide_loose3,tight_loose3 `
  --notch-modes off,on `
  --captures 4
```

输出目录默认在 Windows 临时目录下，例如：

```text
C:\Users\<User>\AppData\Local\Temp\weak_info_probe\...
```

## 8. Windows 运行 adaptive_profile_sweep

`adaptive_profile_sweep` 会启动原 v67 接收主循环，需要 Pluto 真机和可用的 pyadi-iio。无 ROS2 时仍可通过 `python -m` 运行：

```powershell
python -m sdr_receiver_py_wrapper.adaptive_profile_sweep -- `
  --team RED `
  --profiles INFO `
  --gains 70,73 `
  --rf-bws 220000,420000 `
  --offsets-hz 0,150000 `
  --info-filters loose10,wide_loose10_notch `
  --dwell-sec 8.0
```

如果 `loose10/wide_loose10_notch` 仍然 `AC_RAW=0`，再显式测试 soft fallback：

```powershell
python -m sdr_receiver_py_wrapper.adaptive_profile_sweep -- `
  --team RED `
  --profiles INFO `
  --gains 70,73 `
  --rf-bws 220000,420000 `
  --offsets-hz 0,150000 `
  --info-filters weak_soft,wide_weak_soft_notch `
  --dwell-sec 12.0
```

注意：

- `weak_soft` 不是默认主线，只在显式指定时启用。
- 它先让原 v67 硬 AC 路径运行；硬路径没有 `AC_RAW/CRC` 增量时，才用 soft AC peak 把 payload 投进原 `BIT_POOLS`。
- `SOF/CRC8/CRC16/validate_and_parse()` 仍由原 v67 协议代码把关。

## 9. 直接运行包内原脚本

排除 wrapper/patch 影响时，可以运行直通版本：

```powershell
python -m sdr_receiver_py_wrapper.direct_original_receiver
```

这个命令会定位包内 v67 脚本并执行原 `main()`，不启动 ROS2 node，也不应用 wrapper monkey patch。

如果要覆盖包内 v67 脚本路径：

```powershell
$env:SDR_RECEIVER_ORIGINAL_SCRIPT = "E:\path\to\receiving_messages_adaptive_filter_v67_l2cal_20260505_l2rescue_80k_g40.py"
python -m sdr_receiver_py_wrapper.direct_original_receiver
```

## 10. 常见问题

### ModuleNotFoundError: No module named 'adi'

说明当前 venv 没装 `pyadi-iio`：

```powershell
pip install -r requirements.txt
```

如果只是跑离线 smoke，可加：

```powershell
python -m sdr_receiver_py_wrapper.offline_smoke_test --allow-adi-stub
```

### Pluto 连接失败

检查：

```powershell
ping 192.168.2.1
iio_info -u ip:192.168.2.1
```

如果 ping 不通，先处理 Windows 网络适配器、USB Ethernet/RNDIS、IP 地址和防火墙。

### ros2 run 在 Windows 不可用

这是正常情况，除非你在 Windows 上完整安装了 ROS2。Windows 本地调试优先使用：

```powershell
python -m sdr_receiver_py_wrapper.<tool_name>
```

比赛主线部署仍建议在 Ubuntu 22.04 + ROS2 Humble 上进行。

### pytest 不可用

本地没有装 pytest 时会看到：

```text
No module named pytest
```

需要运行测试时：

```powershell
pip install pytest
python -m pytest -q
```

离线 wrapper 主路径可先用 `offline_smoke_test` 覆盖。
