# WSL Local Three-Node Closed Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible Windows-hosted, Ubuntu-22.04-WSL2 three-node test field that runs the real radar referee protocol slice and the real SDR receiver behind ROS 2, simulates pregame and in-match referee behavior, and produces machine-verifiable L0-L3 evidence without requiring YOLO, an industrial camera, Pluto hardware, or the official referee system.

**Architecture:** Windows owns WSL lifecycle, immutable recordings, snapshots, and reports. Inside the existing WSL2 distribution, `RefereeScenarioDriver` exchanges RoboMaster `0xA5` frames with a protocol-only executable linked to the production `RefereeControl` sources through a Linux PTY; the radar slice publishes `RadarContext`, the receiver consumes it and publishes `JamCode`, and the radar slice returns V1.3.1 type-2 key verification frames. A fast `RadarContractSimulator` and the PTY path consume the same scenario files and must produce the same canonical context sequence. Production radar changes are limited to inbound CRC/resynchronization, type-2 key validation, and configurable serial paths; receiver/radar coupling remains exclusively the existing ROS message contract.

**Tech Stack:** Git worktrees, PowerShell 7/Windows PowerShell, WSL2 Ubuntu 22.04, Bash, ROS 2 Humble, C++17, `ament_cmake`, GoogleTest, Python 3.10, `pytest`, `rclpy`, PyYAML, Linux PTYs, JSON/JSONL, SHA-256.

---

## Frozen inputs and safety constraints

- Design authority: `docs/superpowers/specs/2026-07-18-wsl-local-three-node-closed-loop-design.md`.
- Protocol authority: `E:\RoboMaster 2026 机甲大师高校系列赛通信协议 V1.3.1（20260519）.pdf` and `docs/superpowers/specs/2026-07-17-referee-protocol-mapping.md`.
- Recording authority: `docs/superpowers/specs/2026-07-17-recording-evidence-manifest.md` and `sdr_receiver_py_wrapper/fixtures/manifest.json`. Recordings remain outside Git and are addressed by SHA-256.
- Immutable snapshot branch: `codex/pre-wsl-integration-snapshot-20260718`.
- Implementation branch: `codex/wsl-protocol-integration`.
- Implementation worktree: `E:\sdr\.worktrees\wsl-protocol-integration`; WSL path: `/mnt/e/sdr/.worktrees/wsl-protocol-integration`.
- Never modify `main`, force-push, rewrite the snapshot branch, delete an existing branch/worktree, run the three nodes as root, or automatically alter Windows networking/firewall/proxy settings.
- System changes require this order: read-only doctor, WSL export plus SHA-256, generated bootstrap plan, explicit operator approval, then narrowly scoped apply. A failed run preserves evidence and stops only processes whose recorded PID and start identity belong to that run.
- L0-L3 are the software completion gate. L4 hardware/Pluto/self-transmitter testing is optional and reported separately.

## Per-task execution gate

Apply this sequence to every numbered task. Do not dispatch or start the next task until the current task is committed.

1. Create one fresh implementation subagent for exactly one task.
2. Keep its changes uncommitted and create a fresh requirements-compliance reviewer.
3. Return every compliance finding to the implementation subagent; rerun focused tests until compliance passes.
4. Create a fresh code-quality reviewer only after compliance passes.
5. Return every quality finding to the implementation subagent; rerun focused tests until quality passes.
6. From a clean shell, run the task's final verification command and `git diff --check`.
7. Commit only that task. Record commit SHA, tests, review outcomes, remaining risk, and the next command in `docs/handoffs/wsl-loop-current.md`.

Any failed assertion, unverified external recording hash, unexpected WSL mutation, missing ROS dependency, or reviewer finding stops the gate. Do not weaken assertions or relabel candidate recordings as confirmed.

## File map

- `tools/wsl/closed-loop.ps1`: Windows entry point for `doctor`, `bootstrap-plan`, `bootstrap-apply`, `build`, `run-scenario`, `run-suite`, `collect`, and `restore-guide`.
- `tools/wsl/closed_loop.sh`: non-root WSL dispatcher for build and run operations.
- `tools/wsl/bootstrap_manifest.yaml`: allowlisted Ubuntu packages, users, files, and intended mutations.
- `tools/wsl/export_snapshot.ps1`: WSL export, free-space check, and SHA-256 capture.
- `integration/wsl_closed_loop/`: scenario schema, A5 codec, PTY driver, contract simulator, orchestration, assertions, and evidence generation.
- `integration/scenarios/`: canonical pregame, compressed match, real-time match, multi-match, and fault-injection scenarios.
- `integration/test/`: Python unit, simulator parity, lifecycle, and evidence tests.
- `src/radar_referee/include/robot_referee/FrameStreamParser.hpp` and `src/radar_referee/src/FrameStreamParser.cpp`: bounded A5 stream parser shared by production and tests.
- `src/radar_referee/include/robot_referee/JamKeyTransaction.hpp` and `src/radar_referee/src/JamKeyTransaction.cpp`: pure type-2 validation state machine.
- `src/radar_referee/src/protocol_slice_main.cpp`: protocol-only ROS executable linked to the production radar protocol sources.
- `src/radar_referee/test/`: CRC/parser, key transaction, and serial configuration GoogleTests.
- `sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/competition_controller.py`: match-state/freshness gate and transition reset.
- `sdr_receiver_py_wrapper/test/test_competition_controller.py`: receiver-side pregame and stale-result regression tests.
- `docs/wsl_closed_loop_operator_zh.md`: safe operator runbook and exact recovery paths.
- `docs/handoffs/wsl-loop-current.md`: compact continuation record for low-context agents.

### Task 1: Publish the immutable pre-integration snapshot and create the isolated implementation worktree

**Files:**
- Create: `docs/handoffs/wsl-loop-current.md`

- [ ] **Step 1: Verify the source worktree before creating any branch**

Run from the documentation worktree after this plan commit exists:

```powershell
$status = git -C E:\sdr\.worktrees\open-source-replacement-docs status --short
if ($status) { throw "source worktree is dirty" }
$SNAPSHOT_SHA = (git -C E:\sdr\.worktrees\open-source-replacement-docs rev-parse HEAD).Trim()
$SNAPSHOT_SHA
git -C E:\sdr\.worktrees\open-source-replacement-docs branch --show-current
git -C E:\sdr\.worktrees\open-source-replacement-docs remote get-url origin
```

Expected: status is empty; the branch is `codex/open-source-replacement-docs`; origin is `https://github.com/Lancercodermaker/3SE26-.git`. Save the printed HEAD as `$SNAPSHOT_SHA`.

- [ ] **Step 2: Run the existing software baseline**

```powershell
cd E:\sdr\.worktrees\open-source-replacement-docs
python -m pytest sdr_receiver_py_wrapper\test -q
```

Expected: exit code `0` and no failed tests. Record skipped tests exactly; do not convert missing ROS/Pluto into a pass if the suite reports a failure.

- [ ] **Step 3: Create and push the immutable snapshot branch**

```powershell
git branch codex/pre-wsl-integration-snapshot-20260718 $SNAPSHOT_SHA
git push -u origin codex/pre-wsl-integration-snapshot-20260718
$REMOTE_SHA = git ls-remote origin refs/heads/codex/pre-wsl-integration-snapshot-20260718 | ForEach-Object { ($_ -split "`t")[0] }
if ($REMOTE_SHA -ne $SNAPSHOT_SHA) { throw "remote snapshot SHA mismatch" }
```

Expected: push succeeds and local/remote SHA values are identical. Never add later commits to this branch.

- [ ] **Step 4: Create the implementation worktree from the verified SHA**

```powershell
git worktree add E:\sdr\.worktrees\wsl-protocol-integration -b codex/wsl-protocol-integration $SNAPSHOT_SHA
git -C E:\sdr\.worktrees\wsl-protocol-integration rev-parse HEAD
git -C E:\sdr\.worktrees\wsl-protocol-integration branch --show-current
git -C E:\sdr\.worktrees\wsl-protocol-integration status --short
```

Expected: HEAD equals `$SNAPSHOT_SHA`, branch is `codex/wsl-protocol-integration`, and status is empty.

- [ ] **Step 5: Write and verify the initial handoff**

Create `docs/handoffs/wsl-loop-current.md` containing the snapshot SHA, remote SHA, branch, worktree, baseline command/result, frozen design path, and next command `tools/wsl/closed-loop.ps1 doctor`. Verify:

```powershell
rg -n "snapshot|codex/wsl-protocol-integration|pytest|doctor" docs\handoffs\wsl-loop-current.md
git diff --check
```

Expected: all four facts are present and `git diff --check` is silent.

- [ ] **Step 6: Commit Task 1**

```powershell
git add docs/handoffs/wsl-loop-current.md
git commit -m "chore: establish WSL integration rollback point"
```

Expected: one commit on `codex/wsl-protocol-integration` and a clean worktree.

### Task 2: Add safe WSL doctor, export, bootstrap planning, and non-root enforcement

**Files:**
- Create: `tools/wsl/closed-loop.ps1`
- Create: `tools/wsl/export_snapshot.ps1`
- Create: `tools/wsl/closed_loop.sh`
- Create: `tools/wsl/bootstrap_manifest.yaml`
- Create: `integration/test/test_wsl_entrypoints.py`
- Modify: `docs/handoffs/wsl-loop-current.md`

- [ ] **Step 1: Write failing entry-point safety tests**

Create five tests named `test_doctor_is_read_only_and_machine_readable`, `test_bootstrap_plan_contains_only_manifested_mutations`, `test_bootstrap_apply_requires_exact_plan_hash_and_approval`, `test_runtime_commands_reject_uid_zero`, and `test_restore_guide_prints_commands_but_never_executes_them`. Each test invokes PowerShell with pytest's `tmp_path` as its output directory and reads the recording stub's JSON command log before making its assertions.

The fixture must replace `wsl.exe` with a recording stub; assert no `--unregister`, filesystem deletion, firewall, proxy, or network-setting command is issued.

Run:

```powershell
python -m pytest integration/test/test_wsl_entrypoints.py -q
```

Expected: FAIL because the entry points do not exist.

- [ ] **Step 2: Implement a read-only doctor and deterministic bootstrap plan**

`doctor` must write `doctor.json` with distribution name/version, WSL version/kernel, default UID, ROS distro, Python/Git versions, disk free space, proxy warning, repository mount, and dependency presence. `bootstrap-plan` must compare the doctor result with `bootstrap_manifest.yaml`, emit an ordered JSON mutation list, and make no mutation. Use an explicit verb dispatch:

```powershell
param(
  [ValidateSet('doctor','bootstrap-plan','bootstrap-apply','build','run-scenario','run-suite','collect','restore-guide')]
  [string]$Command,
  [string]$OutputDirectory = '.artifacts\wsl-closed-loop',
  [string]$ApprovedPlanSha256,
  [switch]$ConfirmSystemChanges,
  [ValidateSet('L0','L1','L2','L3')][string]$Level,
  [ValidateSet('contract','pty')][string]$Mode,
  [string]$Scenario,
  [string]$Recording,
  [string]$RecordingManifest,
  [string]$RunDirectory
)
```

The manifest may allow only creation of user `sdrdev`, `/etc/wsl.conf` default-user keys, the ROS build dependencies actually missing from doctor, and a workspace under `/home/sdrdev/3SE26-`. It must not allow removal or in-place editing of unrelated files.

- [ ] **Step 3: Implement export and approval-bound apply**

`export_snapshot.ps1` must check destination free space, call `wsl.exe --shutdown`, export `Ubuntu-22.04` to a timestamped `.tar`, compute SHA-256, and write a sibling JSON manifest. `bootstrap-apply` must require both `-ApprovedPlanSha256 <hash>` and `-ConfirmSystemChanges`; it rejects a stale/mismatched plan.

Runtime Bash must begin with:

```bash
if [ "$(id -u)" -eq 0 ]; then
  echo "runtime commands must run as non-root user sdrdev" >&2
  exit 64
fi
```

- [ ] **Step 4: Run the automated safety tests**

```powershell
python -m pytest integration/test/test_wsl_entrypoints.py -q
git diff --check
```

Expected: all tests PASS and diff check is silent.

- [ ] **Step 5: Run doctor and export before any real WSL mutation**

```powershell
tools\wsl\closed-loop.ps1 doctor -OutputDirectory E:\sdr-artifacts\wsl-doctor
tools\wsl\export_snapshot.ps1 -Distribution Ubuntu-22.04 -DestinationDirectory E:\wsl-snapshots
tools\wsl\closed-loop.ps1 bootstrap-plan -OutputDirectory E:\sdr-artifacts\wsl-bootstrap
```

Expected: `doctor.json`, a non-empty `.tar` with matching SHA-256 manifest, and a bootstrap plan. Stop for explicit operator approval. After approval, run the exact hash-bound apply, rerun doctor, and verify default runtime UID is nonzero and username is `sdrdev`.

```powershell
$PLAN = 'E:\sdr-artifacts\wsl-bootstrap\bootstrap-plan.json'
$PLAN_SHA = (Get-FileHash -LiteralPath $PLAN -Algorithm SHA256).Hash
tools\wsl\closed-loop.ps1 bootstrap-apply -OutputDirectory E:\sdr-artifacts\wsl-bootstrap -ApprovedPlanSha256 $PLAN_SHA -ConfirmSystemChanges
wsl.exe --terminate Ubuntu-22.04
tools\wsl\closed-loop.ps1 doctor -OutputDirectory E:\sdr-artifacts\wsl-doctor-after
```

Expected after the operator-approved apply: the command log contains only plan-listed mutations, WSL restarts successfully, and `doctor.json` reports `sdrdev` with a nonzero UID as the default runtime identity.

- [ ] **Step 6: Commit Task 2**

```powershell
git add tools/wsl integration/test/test_wsl_entrypoints.py docs/handoffs/wsl-loop-current.md
git commit -m "feat: add guarded WSL bootstrap workflow"
```

Expected: tests pass, snapshot identity is recorded in the handoff, and the worktree is clean.

### Task 3: Harden the production A5 stream parser and CRC resynchronization

**Files:**
- Create: `src/radar_referee/include/robot_referee/FrameStreamParser.hpp`
- Create: `src/radar_referee/src/FrameStreamParser.cpp`
- Create: `src/radar_referee/test/test_frame_stream_parser.cpp`
- Modify: `src/radar_referee/include/robot_referee/SendReceive.hpp`
- Modify: `src/radar_referee/src/SendReceive.cpp`
- Modify: `src/radar_referee/CMakeLists.txt`
- Modify: `src/radar_referee/package.xml`

- [ ] **Step 1: Add failing parser tests before production changes**

Cover valid single/adjacent frames, CRC8 corruption, CRC16 corruption, truncated frames, declared length above the chosen protocol maximum, leading noise, and a bad frame followed by a valid frame. Register `test_frame_stream_parser.cpp` with `ament_add_gtest` in CMake before the first run so the missing parser produces a compile failure. The boundary assertions must include:

```cpp
EXPECT_TRUE(parser.push(valid_frame).size() == 1);
EXPECT_TRUE(parser.push(crc8_bad).empty());
EXPECT_TRUE(parser.push(crc16_bad).empty());
EXPECT_EQ(parser.push(noise_then_valid).at(0), valid_frame);
EXPECT_EQ(parser.buffered_bytes(), 0U);
```

Run in WSL:

```bash
cd /mnt/e/sdr/.worktrees/wsl-protocol-integration
source /opt/ros/humble/setup.bash
colcon test --packages-select radar_referee --event-handlers console_direct+
```

Expected: FAIL because the parser/test target does not exist.

- [ ] **Step 2: Implement a bounded incremental parser**

The parser must seek `0xA5`, wait for the full five-byte header, verify CRC8 across all five header bytes, decode little-endian `data_length`, reject values above a named maximum, wait for exactly `data_length + 9` bytes, and verify CRC16 across that full frame. On failure discard only enough bytes to search for the next `0xA5`; never trust `cmd_id` before both CRCs pass.

- [ ] **Step 3: Route production receive preprocessing through the parser**

Remove the unconditional-success/commented-CRC path in `SendReceive.cpp`. Keep serial I/O behavior unchanged and pass only validated complete frames to existing `RefereeControl` parsing. Add `ament_cmake_gtest` as a test-only dependency.

- [ ] **Step 4: Verify parser and radar package**

```bash
colcon build --packages-select sdr_receiver radar_referee --cmake-args -DBUILD_TESTING=ON
colcon test --packages-select radar_referee --event-handlers console_direct+
colcon test-result --verbose
```

Expected: build succeeds; every radar test passes; corrupted/truncated/oversized frames are rejected; valid frames after noise are recovered.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/radar_referee
git commit -m "fix: validate and resync referee A5 frames"
```

Expected: one focused commit and clean status.

### Task 4: Correct V1.3.1 type-2 key verification and authoritative acceptance

**Files:**
- Create: `src/radar_referee/include/robot_referee/JamKeyTransaction.hpp`
- Create: `src/radar_referee/src/JamKeyTransaction.cpp`
- Create: `src/radar_referee/test/test_jam_key_transaction.cpp`
- Modify: `src/radar_referee/src/RefereeControl.cpp`
- Modify: `src/radar_referee/include/robot_referee/RefereeControl.hpp`
- Modify: `src/radar_referee/CMakeLists.txt`

- [ ] **Step 1: Write failing pure state-machine tests**

Use an injected monotonic clock and register the new test target with `ament_add_gtest` before the first run. Assert exactly one emitted payload has outer command `0x0301`, data command `0x0121`, receiver `0x8080`, monotonic `radar_cmd`, `user_data[1] == 2`, and six alphanumeric ASCII key bytes. Reject empty, short, long, or non-alphanumeric keys; reject type `3`; suppress the same level/key for ten seconds; do not mark success on serial write; mark success only after a subsequent `0x020E` level rise.

```cpp
EXPECT_EQ(tx.begin(level1, "fcYqTC", t0)->password_type, 2);
EXPECT_FALSE(tx.begin(level1, "fcYqTC", t0 + 9s).has_value());
EXPECT_FALSE(tx.on_serial_write());
EXPECT_TRUE(tx.on_radar_info(level2, t0 + 2s));
```

Expected initial test result: FAIL because `JamKeyTransaction` does not exist.

- [ ] **Step 2: Implement the pure transaction and replace the two-phase production logic**

Delete the zero-key phase and undefined `password_cmd=3` phase. `RefereeControl` must validate `JamCode.valid`, exact six-byte alphanumeric key, current level, and mutable state before requesting one type-2 transaction. Preserve the production sender/receiver IDs defined by V1.3.1.

- [ ] **Step 3: Feed validated `0x020E` changes back to the transaction**

Only a level increase following the pending request closes it as accepted. Timeout/rejection must remain observable in logs/state without retrying inside ten seconds. A match/level transition clears obsolete pending keys.

- [ ] **Step 4: Run focused and package tests**

```bash
colcon build --packages-select sdr_receiver radar_referee --cmake-args -DBUILD_TESTING=ON
colcon test --packages-select radar_referee --event-handlers console_direct+
colcon test-result --verbose
```

Expected: all tests PASS and no test or production source contains `password_cmd = 3` or an empty-key verification phase.

- [ ] **Step 5: Commit Task 4**

```bash
git add src/radar_referee
git commit -m "fix: implement referee type-2 key validation"
```

Expected: one focused commit and clean status.

### Task 5: Add configurable serial input and a production-linked protocol slice executable

**Files:**
- Create: `src/radar_referee/src/protocol_slice_main.cpp`
- Create: `src/radar_referee/test/test_serial_configuration.cpp`
- Modify: `src/radar_referee/include/robot_referee/RefereeControl.hpp`
- Modify: `src/radar_referee/src/RefereeControl.cpp`
- Modify: `src/radar_referee/include/robot_referee/SendReceive.hpp`
- Modify: `src/radar_referee/src/SendReceive.cpp`
- Modify: `src/radar_referee/CMakeLists.txt`
- Modify: `src/radar_referee/launch/radar.launch.py`

- [ ] **Step 1: Write failing serial-resolution tests**

Register `test_serial_configuration.cpp` with `ament_add_gtest`, then test precedence `ROS parameter > environment absent > /dev/ttyUSB0`, rejection of empty/non-device values, and acceptance of a test PTY path. Require bounded startup failure instead of the existing infinite retry loop. The first build must fail before the resolver/timeout implementation is added.

- [ ] **Step 2: Make the serial path configurable without changing its production default**

Declare ROS parameters `serial_port` (default `/dev/ttyUSB0`) and `serial_open_timeout_ms`. Pass the resolved value into `setupSerialPort`; do not hard-code PTY paths in receiver or production code.

- [ ] **Step 3: Add the protocol-only executable linked to the same source files**

`radar_protocol_slice` may start only serial receive, ROS spin, `RadarContext` publication, `JamCode` subscription, and key transmission. It must not start location, vulnerability, robot-info, event-info, outpost, vision, YOLO, or camera threads. CMake must link both `radar_node` and `radar_protocol_slice` to one shared protocol library containing `RefereeControl.cpp`, `SendReceive.cpp`, `FrameStreamParser.cpp`, `JamKeyTransaction.cpp`, and `CRC.cpp`.

- [ ] **Step 4: Verify build and absence of forbidden dependencies in the slice**

```bash
colcon build --packages-select sdr_receiver radar_referee --cmake-args -DBUILD_TESTING=ON
source install/setup.bash
ros2 run radar_referee radar_protocol_slice --ros-args -p serial_port:=/dev/does-not-exist -p serial_open_timeout_ms:=100
```

Expected: build/tests pass; the run exits nonzero within one second with a clear serial-open error, not an infinite loop. `ldd`/source review shows no new YOLO or camera dependency.

- [ ] **Step 5: Commit Task 5**

```bash
git add src/radar_referee
git commit -m "feat: add configurable radar protocol slice"
```

Expected: production default remains `/dev/ttyUSB0`, tests pass, and status is clean.

### Task 6: Define canonical match scenarios and a V1.3.1 A5 codec

**Files:**
- Create: `integration/wsl_closed_loop/__init__.py`
- Create: `integration/wsl_closed_loop/scenario.py`
- Create: `integration/wsl_closed_loop/a5_codec.py`
- Create: `integration/scenarios/pregame_self_check.yaml`
- Create: `integration/scenarios/compressed_full_match.yaml`
- Create: `integration/scenarios/realtime_seven_minute_match.yaml`
- Create: `integration/scenarios/multi_match_stability.yaml`
- Create: `integration/scenarios/a5_faults.yaml`
- Create: `integration/test/test_scenario.py`
- Create: `integration/test/test_a5_codec.py`

- [ ] **Step 1: Write failing schema and byte-level codec tests**

Test deterministic load/dump, unique event IDs, monotonic logical time, pregame L3 duration range 3-8 seconds (default 5), required L3→L1 reset marker, official-match `game_progress == 4`, L1/K1→L2/K2→L3/K3→end order, and exact CRC8/CRC16 round trips for the protocol fields used by the radar slice.

- [ ] **Step 2: Implement an explicit scenario model**

Each event must carry `id`, `at_ms`, `phase`, `game_progress`, `radar_level`, `key_mutable`, `referee_online`, optional deterministic `key`, expected context, and expected/forbidden key-send count. `phase` values are exactly `offline`, `pregame_self_check`, `pregame_ready`, `in_match`, and `ended`.

- [ ] **Step 3: Implement only the needed official frame codec**

Encode/decode the game-state and `0x020E` inputs plus `0x0301/0x0121` key-verification output, reusing the protocol CRC algorithm. Fault builders must independently corrupt CRC8, CRC16, length, truncation, and prefix noise. The receiver package must never import this module.

- [ ] **Step 4: Create the four lifecycle scenarios and one fault scenario**

The compressed match uses logical time but preserves ordering/deadlines; the real-time match lasts seven minutes; multi-match runs at least three complete match resets; fault scenario places a valid frame after every injected fault. Deterministic K1/K2/K3 are marked `synthetic_protocol_key`, never official oracle values.

- [ ] **Step 5: Verify the schema and codec**

```bash
python3 -m pytest integration/test/test_scenario.py integration/test/test_a5_codec.py -q
```

Expected: all tests PASS; scenario serialization is byte-for-byte stable; invalid timelines fail with an event-specific message.

- [ ] **Step 6: Commit Task 6**

```bash
git add integration
git commit -m "test: define closed-loop referee scenarios"
```

Expected: one focused commit and clean status.

### Task 7: Implement the Linux PTY referee scenario driver

**Files:**
- Create: `integration/wsl_closed_loop/referee_scenario_driver.py`
- Create: `integration/wsl_closed_loop/pty_transport.py`
- Create: `integration/test/test_referee_scenario_driver.py`
- Modify: `sdr_receiver_py_wrapper/setup.py`

- [ ] **Step 1: Write failing PTY behavior tests**

Using `pty.openpty()`, assert the driver writes scheduled A5 inputs, records raw bytes/timestamps, parses the radar response, rejects type 3/empty/non-ASCII/wrong receiver/wrong data command, enforces the ten-second duplicate rule, and emits a level-raised `0x020E` only after a correct type-2 response.

- [ ] **Step 2: Implement owned PTY lifecycle**

Create a unique PTY per run; store master FD, slave path, PID, and process-start identity in `processes.json`. Close only descriptors created by the run. Never remove arbitrary `/dev/pts/*` paths.

- [ ] **Step 3: Implement scenario execution and referee acceptance**

The driver must distinguish pregame L3 diagnostic frames from in-match L3, send the scenario's game/level events, parse production radar responses, and advance K1/K2/K3 only on a valid type-2 payload. Persist `serial_rx.bin`, `serial_tx.bin`, and a JSONL decision trace.

- [ ] **Step 4: Verify the driver**

```bash
python3 -m pytest integration/test/test_referee_scenario_driver.py -q
```

Expected: all tests PASS, including malformed output rejection and bad-input-frame recovery.

- [ ] **Step 5: Commit Task 7**

```bash
git add integration sdr_receiver_py_wrapper/setup.py
git commit -m "feat: add PTY referee scenario driver"
```

Expected: one focused commit and clean status.

### Task 8: Enforce receiver pregame, online, mutability, freshness, and transition gates

**Files:**
- Modify: `sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/competition_controller.py`
- Modify: `sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/context_arbiter.py`
- Modify: `sdr_receiver_py_wrapper/sdr_receiver_py_wrapper/receiver_node.py`
- Modify: `sdr_receiver_py_wrapper/config/competition_receiver.yaml`
- Modify: `sdr_receiver_py_wrapper/test/test_competition_controller.py`
- Modify: `sdr_receiver_py_wrapper/test/test_context_arbiter.py`
- Modify: `sdr_receiver_py_wrapper/test/test_receiver_pipeline.py`

- [ ] **Step 1: Add failing receiver regression tests**

Assert zero JamCode during offline, pregame L3 self-check, pregame L1, `key_mutable == false`, stale context, profile/level mismatch, and after match end. Assert L3→L1 clears decoder state, candidates, delayed results, duplicate windows, and previous context generation. Assert a delayed pregame-L3 result cannot publish after in-match L1 begins.

```python
assert controller.handle_jam_key(pregame_l3_result, now=clock.now()) is None
controller.update_context(in_match_l1)
assert controller.handle_jam_key(delayed_pregame_result, now=clock.now()) is None
assert controller.handle_jam_key(fresh_l1_result, now=clock.now()) == expected
```

- [ ] **Step 2: Implement one explicit publish predicate**

Publish only when `referee_online and game_progress == 4 and key_mutable and context_is_fresh and result.profile == active_level`. Add the ROS parameter `context_timeout_sec` with default `2.0`; use an injected monotonic clock in the controller and compare it with the last accepted-context receive time. Return a structured rejection reason for logging/tests rather than duplicating the predicate across the pipeline.

- [ ] **Step 3: Add generation-based reset protection**

Every context transition that changes match phase, online state, or level increments the existing `context_version` and clears level-specific decoder/candidate/result state. `receiver_node.py` must preserve the originating `context_version` already carried through acquisition/decoding and reject a command when it differs from the active arbiter version.

- [ ] **Step 4: Run receiver tests**

```bash
python3 -m pytest \
  sdr_receiver_py_wrapper/test/test_competition_controller.py \
  sdr_receiver_py_wrapper/test/test_context_arbiter.py \
  sdr_receiver_py_wrapper/test/test_receiver_pipeline.py -q
```

Expected: all tests PASS and pregame self-check produces zero publishable JamCode.

- [ ] **Step 5: Commit Task 8**

```bash
git add sdr_receiver_py_wrapper
git commit -m "fix: gate receiver output by live match context"
```

Expected: receiver remains unaware of A5/PTY/`0x020E`/`0x0301`, and status is clean.

### Task 9: Add the fast RadarContractSimulator and context-sequence parity assertion

**Files:**
- Create: `integration/wsl_closed_loop/radar_contract_simulator.py`
- Create: `integration/wsl_closed_loop/context_trace.py`
- Create: `integration/test/test_radar_contract_simulator.py`
- Create: `integration/test/test_context_trace_parity.py`
- Modify: `sdr_receiver_py_wrapper/setup.py`

- [ ] **Step 1: Write failing canonical-trace tests**

Normalize only nondeterministic header timestamps; retain event ID, phase, level, online, mutable, game progress, and match time. Assert:

```python
assert canonical(expected_trace) == canonical(contract_simulator_trace)
assert canonical(expected_trace) == canonical(protocol_slice_trace)
```

The protocol-slice fixture may initially be a recorded deterministic trace produced from the same A5 codec; Task 11 replaces it with the live PTY process.

- [ ] **Step 2: Implement the simulator as a ROS contract peer**

Read the exact same YAML scenario used by `RefereeScenarioDriver` and publish only `RadarContext`; subscribe to `JamCode` to enforce forbidden/publish counts and timing. Do not import radar A5 or private-state modules.

- [ ] **Step 3: Implement canonical trace normalization and diff output**

On mismatch, report first divergent event, expected/actual fields, and preceding/following two events. Never hide missing or duplicate states.

- [ ] **Step 4: Run parity tests**

```bash
python3 -m pytest integration/test/test_radar_contract_simulator.py integration/test/test_context_trace_parity.py -q
```

Expected: all tests PASS for pregame, compressed match, and multi-match scenarios.

- [ ] **Step 5: Commit Task 9**

```bash
git add integration sdr_receiver_py_wrapper/setup.py
git commit -m "test: add radar contract simulator parity"
```

Expected: one focused commit and clean status.

### Task 10: Build the process orchestrator and immutable evidence collector

**Files:**
- Create: `integration/wsl_closed_loop/orchestrator.py`
- Create: `integration/wsl_closed_loop/evidence.py`
- Create: `integration/wsl_closed_loop/assertions.py`
- Create: `integration/test/test_orchestrator_lifecycle.py`
- Create: `integration/test/test_evidence_bundle.py`
- Modify: `tools/wsl/closed_loop.sh`
- Modify: `tools/wsl/closed-loop.ps1`

- [ ] **Step 1: Write failing lifecycle and evidence tests**

Test unique `ROS_DOMAIN_ID`, unique PTY/evidence directory, readiness barriers, per-process timeouts, exit-code propagation, SIGINT→bounded wait→SIGTERM for owned PIDs only, no unrelated-process kill, failure preservation, cached large-file hash keyed by path/size/mtime, and atomic evidence finalization.

- [ ] **Step 2: Implement explicit process specifications**

Each child record contains command, cwd, environment allowlist, PID, process-start identity, start/end time, readiness condition, exit code, stdout/stderr paths, and ownership token. A run stops on the first required-process failure and does not silently retry.

- [ ] **Step 3: Implement the required evidence contract**

Every finalized run directory contains exactly these required artifacts (additional raw logs are allowed):

```text
run.json
doctor.json
scenario.yaml
processes.json
ros_topics.json
serial_rx.bin
serial_tx.bin
radar_context.jsonl
jam_code.jsonl
receiver.log
radar.log
referee_simulator.log
assertions.json
artifact_sha256.json
```

Bind repository commit, dirty-state refusal, scenario hash, recording hash when present, WSL/ROS versions, process identities, exit codes, and timeline. `assertions.json` is the sole pass/fail authority.

- [ ] **Step 4: Wire build/run/collect entry points**

Support `run-scenario -Scenario <path> -Mode contract|pty -Recording <optional path>`, `run-suite -Level L0|L1|L2|L3`, and `collect -RunDirectory <path>`. Single-scenario reruns must not rebuild unchanged packages or rehash unchanged large recordings.

- [ ] **Step 5: Verify orchestration and evidence**

```bash
python3 -m pytest integration/test/test_orchestrator_lifecycle.py integration/test/test_evidence_bundle.py -q
```

Expected: all tests PASS, including forced child crash, timeout, interrupted run, and hash mismatch.

- [ ] **Step 6: Commit Task 10**

```bash
git add integration tools/wsl
git commit -m "feat: orchestrate closed-loop evidence runs"
```

Expected: one focused commit and clean status.

### Task 11: Prove L0-L2 with fast contract and live PTY production-slice runs

**Files:**
- Create: `integration/test/test_live_protocol_slice.py`
- Create: `integration/test/test_closed_loop_scenarios.py`
- Modify: `tools/wsl/closed_loop.sh`
- Modify: `docs/handoffs/wsl-loop-current.md`

- [ ] **Step 1: Add failing live-process assertions**

Launch `RefereeScenarioDriver`, `radar_protocol_slice`, and the receiver as real processes in one ROS domain. Assert the exact context sequence, zero JamCode/sendKey in pregame, one valid type-2 response per in-match level, level rise only after referee acceptance, zero post-end output, fault recovery, and clean process exits.

- [ ] **Step 2: Wire L0 and L1 suites**

L0 runs all Python unit tests plus `radar_referee` GoogleTests. L1 runs pregame, compressed full match, context timeout/offline, end-of-match, and multi-match through `RadarContractSimulator` with machine assertions.

- [ ] **Step 3: Wire L2 suite to the production-linked slice**

L2 runs the same scenarios through PTY/A5 and `radar_protocol_slice`, then compares its canonical `RadarContext` trace with L1. Add CRC8, CRC16, length, truncation, and noise injections, each followed by a valid recovered frame.

- [ ] **Step 4: Run compressed gates first**

```powershell
tools\wsl\closed-loop.ps1 build -OutputDirectory E:\sdr-artifacts\wsl-loop
tools\wsl\closed-loop.ps1 run-suite -Level L0 -OutputDirectory E:\sdr-artifacts\wsl-loop
tools\wsl\closed-loop.ps1 run-suite -Level L1 -OutputDirectory E:\sdr-artifacts\wsl-loop
tools\wsl\closed-loop.ps1 run-suite -Level L2 -OutputDirectory E:\sdr-artifacts\wsl-loop
```

Expected: each command exits `0`; each run has `assertions.json` with top-level `"passed": true`; L1 and L2 canonical traces match.

- [ ] **Step 5: Run the seven-minute and multi-match stability variants**

```powershell
tools\wsl\closed-loop.ps1 run-scenario -Scenario integration/scenarios/realtime_seven_minute_match.yaml -Mode pty -OutputDirectory E:\sdr-artifacts\wsl-loop
tools\wsl\closed-loop.ps1 run-scenario -Scenario integration/scenarios/multi_match_stability.yaml -Mode pty -OutputDirectory E:\sdr-artifacts\wsl-loop
```

Expected: both exit `0`; no pregame/stale/duplicate/post-end key publication; all processes exit cleanly.

- [ ] **Step 6: Commit Task 11**

```bash
git add integration tools/wsl docs/handoffs/wsl-loop-current.md
git commit -m "test: prove WSL radar receiver closed loop"
```

Expected: L0-L2 evidence paths and commit SHA are in the handoff; status is clean.

### Task 12: Run L3 official recordings and publish the operator/recovery handoff

**Files:**
- Create: `docs/wsl_closed_loop_operator_zh.md`
- Create: `integration/test/test_recording_evidence_contract.py`
- Modify: `tools/wsl/closed_loop.sh`
- Modify: `tools/wsl/closed-loop.ps1`
- Modify: `docs/handoffs/wsl-loop-current.md`

- [ ] **Step 1: Write failing recording-evidence contract tests**

Assert manifest/hash verification occurs before replay; confirmed oracle is a strong key assertion; candidate samples are diagnostic only; context-negative samples allow physical decode but require zero JamCode; fault samples require safe failure plus a reason. Reject missing, renamed-with-wrong-hash, or ambiguously classified files.

- [ ] **Step 2: Implement the L3 manifest runner**

Read the existing manifests, convert paths with `PureWindowsPath` so `E:\录波\raw_data_1_本场己方为红方\raw_data.bin` becomes `/mnt/e/录波/raw_data_1_本场己方为红方/raw_data.bin`, cache hashes by identity, and run each recording through the receiver plus both contract and PTY radar modes where applicable. Keep synthetic protocol keys and official recording conclusions in separate evidence fields.

- [ ] **Step 3: Write the Chinese operator and restore guide**

Document prerequisites, doctor, snapshot verification, bootstrap approval, build, one-scenario rerun, L0-L3 suite, report reading, process ownership, disk locations, proxy warning, USB/Pluto optional L4, and exact manual WSL import/restore commands. The guide must state that restore is an operator action and automation never unregisters or overwrites the current distribution.

- [ ] **Step 4: Run L3 and the complete software gate**

```powershell
tools\wsl\closed-loop.ps1 run-suite -Level L3 -OutputDirectory E:\sdr-artifacts\wsl-loop -RecordingManifest docs\superpowers\specs\2026-07-17-recording-evidence-manifest.md
tools\wsl\closed-loop.ps1 run-suite -Level L0 -OutputDirectory E:\sdr-artifacts\wsl-loop-final
tools\wsl\closed-loop.ps1 run-suite -Level L1 -OutputDirectory E:\sdr-artifacts\wsl-loop-final
tools\wsl\closed-loop.ps1 run-suite -Level L2 -OutputDirectory E:\sdr-artifacts\wsl-loop-final
tools\wsl\closed-loop.ps1 run-suite -Level L3 -OutputDirectory E:\sdr-artifacts\wsl-loop-final -RecordingManifest docs\superpowers\specs\2026-07-17-recording-evidence-manifest.md
```

Expected: all four levels exit `0`; confirmed oracle matches its manifest truth; candidates are not promoted; context-negative samples publish zero JamCode; every final evidence bundle passes hash verification.

- [ ] **Step 5: Run final scope and cleanliness checks**

```powershell
git status --short
git diff codex/pre-wsl-integration-snapshot-20260718...HEAD -- sdr_receiver_py_wrapper | rg "0x020E|0x0301|0x0121|/dev/pts"; if ($LASTEXITCODE -eq 0) { throw "receiver/radar coupling leaked" }
git diff --check
```

Expected: only the intended uncommitted documentation/handoff changes are listed before the commit; the receiver diff contains none of the forbidden radar/PTY protocol tokens; diff check is silent.

- [ ] **Step 6: Commit Task 12 and record the continuation point**

```powershell
git add docs/wsl_closed_loop_operator_zh.md docs/handoffs/wsl-loop-current.md integration/test/test_recording_evidence_contract.py tools/wsl
git commit -m "docs: publish WSL closed-loop acceptance workflow"
git status --short
```

Expected: commit succeeds, status is empty, and the handoff contains the branch/HEAD, snapshot SHA, WSL snapshot hash, exact L0-L3 evidence directories, residual risks, optional L4 command, and future Ubuntu-VM migration boundary.

## Completion definition

The first-stage software effort is complete only when Tasks 1-12 are individually reviewed, tested, and committed; the remote immutable snapshot is SHA-verified; WSL can be restored from a verified export; the runtime uses `sdrdev`; L0-L3 evidence is machine-passing; pregame L3 causes zero JamCode/key send; live PTY and fast simulator context traces match; the real production-linked radar protocol slice emits only V1.3.1 type-2 key verification; and receiver source remains free of radar serial/protocol knowledge. YOLO, industrial cameras, RoboMasterEngine, official referee hardware, Pluto live RF, SAW/LNA, antennas, and the full seven-minute visual radar product remain explicitly outside this completion gate.
