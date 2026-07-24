# Learnings � WSL Local Three-Node Closed-Loop

## 2026-07-20: Task 3 Step 1 � TDD RED Phase (FrameStreamParser)

### CMake/ament_cmake_gtest notes
- \ind_package(ament_cmake_gtest REQUIRED)\ must appear **after** other \ind_package\ calls but **before** \ment_add_gtest\.
- The \ment_add_gtest\ target registration must go **before** \ment_package()\.
- When the test file includes headers that transitively pull ROS message types (e.g., \RefereeProtocol.hpp\ ? \std_msgs/msg/string.hpp\), the test target needs explicit \ment_target_dependencies\ for those packages (\std_msgs\, \ision_interface\) � the base \ment_add_gtest\ doesn't inherit the main target's dependencies.
- \package.xml\ needs \<test_depend>ament_cmake_gtest</test_depend>\ alongside other test depends.

### CRC function signatures
- CRC8 functions use \unsigned char*\ (\unsigned int dwLength\).
- CRC16 functions use \uint8_t*\ (\uint32_t dwLength\).
- \Append_CRC8_Check_Sum(data, len)\: computes CRC8 on \len-1\ bytes, writes result at \data[len-1]\.
- \Append_CRC16_Check_Sum(data, len)\: computes CRC16 on \len-2\ bytes, writes result at \data[len-2]\ (low) and \data[len-1]\ (high).
- Existing \FRAME_SIZE(n)\ macro: \(n) + FRAME_HEADER_SIZE + CMD_ID_SIZE + FRAME_TAIL_SIZE\ = \(n) + 5 + 2 + 2\ = \(n) + 9\.

### Build process
- Need \--packages-up-to radar_referee\ not \--packages-select\ when dependent packages (sdr_receiver, vision_interface) haven't been built in the workspace.
- \colcon test --packages-select radar_referee --event-handlers console_direct+\ shows live test output.
- CTest result for RED: \1/1 Test #1: test_frame_stream_parser .........***Failed\

### RED phase results (4 failed, 3 passed)
- **Failed** (expected, stub returns empty/0):
  - \alid_single_frame\: push returned 0 frames, expected 1
  - \alid_adjacent_frames\: push returned 0 frames, expected 2
  - \	runcated_frame\: buffered_bytes() returned 0, expected 4
  - \leading_noise_then_valid\: push returned 0 frames, expected 1
- **Passed** (stub return values accidentally match):
  - \crc8_corrupted\: push returned empty ?
  - \crc16_corrupted\: push returned empty ?
  - \declared_length_exceeds_max\: push returned empty ?

## 2026-07-20: Task 3 Step 2 - TDD GREEN Phase (FrameStreamParser)

### Implementation
- Replaced stub FrameStreamParser.cpp with real bounded incremental parser.
- Algorithm: append incoming bytes, scan buffer for 0xA5, verify CRC8 on 5-byte header, parse data_length, check upper bound (<=128), wait for full frame, verify CRC16, extract, advance.
- push() appends to internal buffer, processes in a while loop, discards consumed bytes via buffer_.erase().
- buffered_bytes() returns buffer_.size().
- reset() calls buffer_.clear().

### Key type-casting notes
- Verify_CRC8_Check_Sum takes unsigned char* - need reinterpret_cast<unsigned char*>(&buffer_[pos]).
- Verify_CRC16_Check_Sum takes uint8_t* - &buffer_[pos] is directly compatible.
- Length params: CRC8 needs unsigned int, CRC16 needs uint32_t - safe static_cast since sizes are bounded by PROTOCOL_MAX_DATA_LENGTH + 9 < 256.

### GREEN phase results (7/7 passed)
- valid_single_frame: OK
- valid_adjacent_frames: OK
- crc8_corrupted: OK
- crc16_corrupted: OK
- truncated_frame: OK (buffered_bytes=4 after pushing 4 bytes)
- declared_length_exceeds_max: OK (data_length=178 > 128, rejected)
- leading_noise_then_valid: OK (noise skipped, valid frame extracted)

### Build/test commands that work
- colcon build --packages-select radar_referee --cmake-args -DBUILD_TESTING=ON
- colcon test --packages-select radar_referee --event-handlers console_direct+
- CTest result for GREEN: 1/1 Test #1: test_frame_stream_parser .........   Passed


## 2026-07-20: Task 3 Step 3 - Route syncToFrameStart through FrameStreamParser

### Changes made
1. SendReceive.hpp: Added `#include "robot_referee/FrameStreamParser.hpp"`
2. CMakeLists.txt: Added `src/FrameStreamParser.cpp` to radar_node executable sources
3. SendReceive.cpp (framePreProcess): Restored CRC validation
   - Replaced unconditional `return true` with real CRC8 + CRC16 checks
   - CRC8 verified first on 5-byte header (using FRAME_HEADER_SIZE = 5)
   - CRC16 verified on full frame (using FRAME_SIZE(data_length) = data_length + 9)
   - This also fixes two bugs in the commented-out code: CRC8 used wrong length (CRC8_OFFSET=4 vs FRAME_HEADER_SIZE=5), CRC16 was missing FRAME_TAIL_SIZE
4. SendReceive.cpp (syncToFrameStart): Replaced manual 0xA5 scanning loop with FrameStreamParser
   - Serial I/O read logic unchanged
   - After read: `FrameStreamParser parser; return parser.push(bytesBatch);`

### Why framePreProcess still exists
Although syncToFrameStart no longer calls framePreProcess, the function is still exposed in the header and may be called from other code paths (RefereeControl::getCommand calls syncToFrameStart, but framePreProcess remains a public API). Keeping it fixed prevents accidental use of the broken path.

### CRC bugs fixed in framePreProcess
- CRC8 was called with length=CRC8_OFFSET=4, meaning bytes 0-2 vs byte 3. Correct is FRAME_HEADER_SIZE=5, covering bytes 0-3 vs byte 4.
- CRC16 was called with length=data_length + FRAME_HEADER_SIZE + CMD_ID_SIZE = data_length+7. Correct is FRAME_SIZE(data_length) = data_length+9, covering the full frame including FRAME_TAIL_SIZE.

### Result
- build: clean
- test_frame_stream_parser: 7/7 PASSED
- baseline pytest: 795 passed, 11 skipped

## 2026-07-20: Task 4 - V1.3.1 type-2 key verification (JamKeyTransaction)

### Files created
- `src/radar_referee/include/robot_referee/JamKeyTransaction.hpp` — class declaration
- `src/radar_referee/src/JamKeyTransaction.cpp` — implementation
- `src/radar_referee/test/test_jam_key_transaction.cpp` — 8 GTest cases

### Files modified
- `src/radar_referee/include/robot_referee/RefereeControl.hpp` — added `JamKeyTransaction _jamTx`, removed `_jamMutex`/`_password_updated`/`_jam_time`
- `src/radar_referee/src/RefereeControl.cpp` — rewrote `sendKey()` and `wirelessKeyCallback()`, added `_jamTx.on_radar_info()` in 0x020E handler, removed dead init lines
- `src/radar_referee/CMakeLists.txt` — added `src/JamKeyTransaction.cpp` to radar_node, added `ament_add_gtest(test_jam_key_transaction ...)`

### Design notes
- JamKeyTransaction is a pure state machine with injected clock (`std::chrono::steady_clock::time_point`) — no ROS dependency
- `begin()` returns `std::optional<JamKeyOutput>` — nullopt on rejection (invalid key, cooldown)
- `armed_output()` exposes pending data for `sendKey()` to consume
- Key validation: exactly 6 chars, all alphanumeric (std::isalnum)
- Cooldown: same (level, key) pair cannot repeat within 10 seconds; different level bypasses cooldown
- Acceptance: only confirmed by subsequent 0x020E level increase (`new_level > pending_level`)
- `radar_cmd` is an internal monotonic counter independent of `radar_cmd_t.radar_cmd` (still used by `sendVul()`)
- `reset()` clears transaction state but preserves `radar_cmd_` and cooldown fields (session-wide)

### What was deleted
- `password_cmd = 2` with empty key (zero-key phase 1) — **DELETED**
- `password_cmd = 3` with key data (undefined in V1.3.1) — **DELETED**
- `_jamMutex`, `_password_updated`, `_jam_time` members — **DELETED**
- All two-phase/`_jamMutex`-based logic in `sendKey()` — **DELETED**

### Test results
- RED phase: 4 FAIL (valid_type2_begin, ten_second_cooldown, acceptance_only_on_level_rise, different_level_allowed), 4 PASS (rejection stubs coincidentally match)
- GREEN phase: 8/8 PASSED
- Production integration: 7/7 FrameStreamParser + 8/8 JamKeyTransaction = 15/15 PASSED
- colcon test-result: 17 tests, 0 errors, 0 failures, 0 skipped
- Baseline pytest: 803 passed, 3 skipped, 15 failed (pre-existing failures in test_command_validator.py + test_receiver_pipeline.py — unrelated to this task)
