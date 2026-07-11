# 开源解调插件与软硬件验收实现计划

> **面向执行代理：** 必须使用 `subagent-driven-development`（推荐）或 `executing-plans`，严格按任务逐项执行，并使用复选框跟踪进度。

**目标：** 将 CombatRadarSdr2026 的解调路径适配为纯计算插件，使用三等级参考录波完成公平对比，并通过分级硬件台架验证现有 Zynq7010+AD9363 链路。

**架构：** 固定上游提交 `13b13a68b7111a15163aedc97f1cb17722f45ad2`，保留来源说明，只适配 profile、PHY、parser 和 CRC，不引入 `server_comm.py`。两个插件使用同一 `IqChunk` 和 `DecodeContext`，硬件验证从无外置增益级开始逐层加入前端。

**技术栈：** Python 3.10、NumPy、pytest、ROS 2、pyadi-iio、SHA-256、JSONL。

---

## 文件结构

- 创建 `third_party/CombatRadarSdr2026/UPSTREAM.md`：来源、提交和整合边界。
- 创建 `upstream_decoder.py`：实现公共 `DecoderPlugin`。
- 创建 `fixture_manifest.py` 和 `fixtures/manifest.json`：样本哈希与期望结果。
- 创建 `decoder_benchmark.py`：同源 IQ 双插件统计。
- 创建 `scripts/run_rf_bench.sh`：分级硬件验收入口。

### 任务 1：固定上游来源和允许整合的文件

**文件：**
- 创建：`third_party/CombatRadarSdr2026/UPSTREAM.md`
- 创建：`third_party/CombatRadarSdr2026/__init__.py`
- 引入：`phy.py`、`protocol.py`、`radio_profiles.py`、`parser/gnuradio_frame_parser.py`

- [ ] **步骤 1：记录来源和固定提交**

`UPSTREAM.md` 必须写明仓库 URL、提交哈希、导入文件清单、未导入 `server_comm.py` 和本地修改清单。执行前检查上游授权条件；若仓库仍无明确许可证，只保留适配接口和测试，不将上游源码发布到目标仓库，直到获得作者许可。

- [ ] **步骤 2：按许可结果选择可审计的引入方式**

获得许可时使用：

```bash
git subtree add --prefix third_party/CombatRadarSdr2026 https://github.com/qianchuan-wys/CombatRadarSdr2026.git 13b13a68b7111a15163aedc97f1cb17722f45ad2 --squash
```

未获得许可时，在 `requirements` 和部署脚本中从固定提交安装，并仅提交本项目适配层。

- [ ] **步骤 3：验证没有 TCP 模块进入运行依赖**

运行：`rg -n "RadarServerComm|server_comm|socket\.send" sdr_receiver_py_wrapper third_party`

预期：生产适配代码中无匹配；来源说明可以出现模块名称。

- [ ] **步骤 4：提交**

```bash
git add third_party sdr_receiver_py_wrapper/requirements.txt
git commit -m "build: pin upstream SDR decoder source"
```

### 任务 2：实现纯计算开源解调插件

**文件：**
- 创建：`sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/upstream_decoder.py`
- 创建：`sdr_receiver_py_wrapper/test/test_upstream_decoder.py`

- [ ] **步骤 1：编写构造和限制测试**

```python
def test_upstream_decoder_has_no_transport_or_device_members():
    decoder = UpstreamDecoder()
    assert decoder.decoder_id == "combat_radar_sdr_13b13a6"
    assert not hasattr(decoder, "sdr")
    assert not hasattr(decoder, "server_comm")

def test_blue_l1_profile_is_selected_from_context():
    decoder = UpstreamDecoder()
    decoder.reset(ResetReason.TARGET_CHANGE, decode_context("BLUE", "L1"))
    assert decoder.active_profile.center_freq == 434_920_000
```

- [ ] **步骤 2：确认测试失败后实现 profile 映射**

映射必须显式覆盖 `RED/BLUE x L1/L2/L3`，不得使用默认等级兜底。`decode()` 只能读取 chunk 中的 IQ，不能设置 LO 或增益。

- [ ] **步骤 3：把上游 `ParsedFrame` 转换为 `DecodedCommand`**

```python
return DecodedCommand(
    cmd_id=frame.cmd_id,
    payload=bytes(frame.data),
    decoder_id=self.decoder_id,
    profile=self.profile_name,
    crc8_ok=True,
    crc16_ok=True,
    crc_mode="kermit-x3014",
    first_sample_index=chunk.first_sample_index,
    last_sample_index=chunk.first_sample_index + len(chunk.samples) - 1,
    receive_wall_time=chunk.rx_wall_time,
    target=context.target,
    team=context.rx_team,
    context_version=context.version,
    evidence={"upstream_seq": frame.seq},
)
```

- [ ] **步骤 4：运行测试并提交**

运行：`python -m pytest test/test_upstream_decoder.py -v`，预期全部通过。

```bash
git add sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/upstream_decoder.py sdr_receiver_py_wrapper/test/test_upstream_decoder.py
git commit -m "feat: adapt upstream decoder as pure plugin"
```

### 任务 3：建立三等级样本清单和确定性回放

**文件：**
- 创建：`sdr_receiver_py_wrapper/fixtures/manifest.json`
- 创建：`sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/fixture_manifest.py`
- 创建：`sdr_receiver_py_wrapper/test/test_fixture_manifest.py`

- [ ] **步骤 1：先登记已确认 L1 样本**

```json
{
  "RX_BLUE_ganrao_1": {
    "format": "complex64-le",
    "sample_rate_hz": 2000000,
    "team": "BLUE",
    "target": "L1",
    "expected_cmd_id": 2566,
    "expected_ascii": "fcYqTC",
    "verification": "confirmed"
  }
}
```

- [ ] **步骤 2：为 L2/L3 添加 `verification: candidate`，禁止填写伪造期望密钥**

运行独立扫描和两个插件后，只有 CRC 合法且重复结果一致时，才能把状态改为 `confirmed` 并写入 SHA-256 与期望密钥。

- [ ] **步骤 3：编写清单校验测试**

确认 `confirmed` 条目必须包含 SHA-256、期望命令和期望 ASCII；`candidate` 条目不得作为 CI 必须解码断言。

- [ ] **步骤 4：运行并提交**

运行：`python -m pytest test/test_fixture_manifest.py -v`，预期全部通过。

```bash
git add sdr_receiver_py_wrapper/fixtures sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/fixture_manifest.py sdr_receiver_py_wrapper/test/test_fixture_manifest.py
git commit -m "test: define verified SDR IQ fixture manifest"
```

### 任务 4：实现双插件公平基准

**文件：**
- 创建：`sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/decoder_benchmark.py`
- 创建：`sdr_receiver_py_wrapper/test/test_decoder_benchmark.py`
- 修改：`sdr_receiver_py_wrapper/setup.py`

- [ ] **步骤 1：编写同源分发测试**

两个假插件收到的 `chunk_id`、样本索引、IQ 哈希和 context version 必须完全相同；结果报告分别包含首次密钥时间、AC/SOF/CRC8/CRC16、CPU 时间和峰值队列深度。

- [ ] **步骤 2：实现 CLI**

```bash
decoder_benchmark --iq RX_BLUE_ganrao_1 --manifest fixtures/manifest.json --decoders upstream,improved_v67 --out result.json
```

成功条件：两个插件都输出已确认的 `fcYqTC`；任一插件结果不同则命令返回非零。

- [ ] **步骤 3：加入 console entry point 并运行测试**

运行：`python -m pytest test/test_decoder_benchmark.py -v`，预期全部通过。

- [ ] **步骤 4：提交**

```bash
git add sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/decoder_benchmark.py sdr_receiver_py_wrapper/test/test_decoder_benchmark.py sdr_receiver_py_wrapper/setup.py
git commit -m "test: compare decoders on identical IQ chunks"
```

### 任务 5：执行分级硬件台架验收

**文件：**
- 创建：`sdr_receiver_py_wrapper/scripts/run_rf_bench.sh`
- 创建：`docs/rf_hardware_acceptance_zh.md`

- [ ] **步骤 1：建立硬件组合矩阵**

依次测试：SDR 直连、SDR+SAW、SDR+LNA、SDR+LNA+SAW、完整链路+10 dB 衰减、完整链路+20 dB 衰减。每组固定记录线缆长度、供电、发射距离和极化方向。

- [ ] **步骤 2：为每组执行低增益起步扫描**

```bash
ros2 launch sdr_receiver_py_wrapper competition_receiver.launch.py initial_rx_gain:=0 record_iq:=true
```

每级增益只在 `RF_LINEAR` 时增加；出现 `RF_CLIPPED` 立即停止加增益。验收记录必须包含峰值、RMS、削顶比例、CRC16 数量和最终增益。

- [ ] **步骤 3：执行 USB 与写盘长稳测试**

使用经过验证的短 USB 3 线连续运行 30 分钟，再用比赛 3 米线重复。要求采集占空比不低于 99%，`queue_drops=0`、`libiio_timeouts=0`；不满足时判定该线缆或主机端口组合不可上场。

- [ ] **步骤 4：验证 ROS 闭环**

使用确认的 L1 回放或台架发射，要求 `/sdr/jam_code` 出现一次正确密钥，雷达主工程日志出现回调存储密钥并进入 phase 2。

- [ ] **步骤 5：提交验收脚本和文档**

```bash
git add sdr_receiver_py_wrapper/scripts/run_rf_bench.sh docs/rf_hardware_acceptance_zh.md
git commit -m "test: add staged RF hardware acceptance procedure"
```

### 任务 6：整体验收与分支决策

- [ ] **步骤 1：在 `codex/open-source-replacement` 运行离线、ROS 和硬件验收**
- [ ] **步骤 2：在 `codex/hybrid-receiver` 使用同一清单和硬件组合重复验收**
- [ ] **步骤 3：生成一份只包含可复现指标的对比报告，不以代码量或主观复杂度决定胜者**
- [ ] **步骤 4：确认公共底座满足 99% 采集占空比、上下文不误切、负样本不误报后，再选择生产主插件**
- [ ] **步骤 5：提交验收报告**

```bash
git add docs/decoder_acceptance_report_zh.md
git commit -m "docs: record decoder and hardware acceptance results"
```
