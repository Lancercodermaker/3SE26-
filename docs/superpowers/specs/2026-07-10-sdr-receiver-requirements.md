# SDR Receiver Refactor Requirements

Date: 2026-07-10

## 1. Purpose

Refactor the 3SE 2026 radar SDR receiver into a diagnosable and competition-ready system. The work must preserve the current ROS 2 integration, use the open-source CombatRadarSdr2026 decoder as a reference implementation, and address both confirmed RF failures and context-driven target switching failures.

## 2. Confirmed Evidence

### 2.1 Field IQ

- The field capture contains no recoverable protocol entry: `AC_RAW`, `AC`, `SOF`, `CRC8`, `CRC16`, and `CRC16_FAIL` remain zero across scanned candidates.
- Many samples reach the AD9363 digital rails near +/-2048. The field receiver package inferred ADC scale from candidates beginning at 32768, so clipped input was reported as low signal.
- The 900-second recording window contains 576.8 seconds of complex64 samples, approximately 64% acquisition duty.
- Running the open-source decoder against the field IQ does not recover a valid frame. Replacing only the decoder cannot repair this recording.

### 2.2 Radar Main Logs

- Match 1 reports level 1, then level 3 from 11:30:52 to 11:30:56, then level 1 again.
- Match 2 reports level 1, then level 3 from 11:42:03 to 11:42:07, then level 1 again.
- Neither match contains a received `JamCode`, phase-2 key submission, or successful key update.
- Invalid transient self IDs such as 0, 160, and 176 appear in the logs. The valid red radar station ID is 9.
- The receiver accepts context from `/judge/radar_context`, `/match_info`, and `/judge/radar_info` without source arbitration.

### 2.3 Reference IQ

- `RX_BLUE_ganrao_1`, `RX_BLUE_ganrao_2`, and `RX_BLUE_ganrao_3` are little-endian complex64 recordings at 2.0 Msps.
- `RX_BLUE_ganrao_1` is a confirmed L1 positive sample and recovers `0x0A06` payload `fcYqTC`.
- L2 and L3 labels must be verified by decoded command, payload, CRC mode, and required frequency offset before becoming authoritative fixtures.
- The field BO3 capture is a negative RF/context regression sample, not a positive key-decoding fixture.

## 3. Scope

### 3.1 In Scope

- A host-side common receiver foundation that exclusively controls Pluto SDR hardware.
- Two decoder plugins: adapted upstream decoder and improved v67 decoder.
- ROS 2 input/output integration without introducing the upstream TCP bridge.
- RF safety, context arbitration, deterministic target switching, structured diagnostics, and replayable recording metadata.
- A small radar-main integration change that publishes a new topic containing data already available in the radar main process.
- Offline, ROS closed-loop, hardware bench, and endurance acceptance tests.

### 3.2 Out of Scope

- Running decoding on the Zynq7010.
- Replacing Zynq7010/AD9363 with Zynq7020/AD9361.
- Sending decoder output through TCP or rebuilding an A5 referee frame for local IPC.
- Letting decoder plugins control SDR settings or publish ROS/TCP data.
- Requiring radar main to expose new low-level referee sequence or transport timestamps that it does not already retain.

## 4. Functional Requirements

### FR-1 Hardware Ownership

Only the common foundation may connect to libiio, set LO/sample rate/RF bandwidth/gain, or perform reconnects. Decoder plugins consume IQ and immutable metadata only.

### FR-2 Canonical Decoder Interface

Every decoder plugin shall consume an `IqChunk` plus an accepted target context and return zero or more `DecodedCommand` values. A decoded command shall contain command ID, payload bytes, CRC evidence, decoder ID, profile, sample range, and receive timestamp.

### FR-3 Output Ownership

Only the common ROS adapter may publish `/sdr/jam_code`, raw-frame diagnostics, or receiver status. A valid `0x0A06` payload shall remain six ASCII alphanumeric bytes. The radar main project remains responsible for sending the key to the referee system.

### FR-4 Context Authority

Exactly one configured ROS topic may drive competition state. Other compatible topics are diagnostic inputs and may not overwrite the accepted context.

### FR-5 Context Validation

- Accept radar station IDs 9 and 109 only for team selection.
- Reject IDs such as 0, 160, and 176 without changing the locked team.
- Lock team selection before competition decoding starts.
- Reject invalid levels outside 1 through 3.
- Record every received, accepted, rejected, and conflicting context event.

### FR-6 Level Transition Policy

- Pre-match level changes shall be logged but shall not retune the receiver.
- At competition start, initialize from the latest stable authoritative level.
- In-match changes require configurable consecutive observations and duration.
- A stable return to a lower official level must replace a previously accepted higher level.
- A target transition shall record old/new targets, triggering evidence, LO, bandwidth, gain, and decoder reset reason.

### FR-7 Radar Main Evidence Topic

Radar main may publish a new topic using data it already owns. The minimum atomic message contains:

- `self_id`
- `self_color`
- `radar_info_raw`
- derived `jam_level`
- derived `key_mutable`
- game progress and match time already held by radar main

The receiver records local ROS receipt wall time and monotonic time. Referee-frame sequence and low-level serial receive time are optional future fields, not current requirements.

### FR-8 RF Safety

- Use an explicit AD9363 digital full-scale convention; the current expected code scale is 2048.
- Detect I/Q rail occupancy, peak, RMS, DC offset, and clipping ratio.
- Clipping shall force gain reduction or an RF fault state; it must never be classified as low signal.
- Automatic gain changes shall be bounded, rate-limited, and auditable.
- Competition startup shall begin from a conservative gain.

### FR-9 Recording

Record IQ separately from a sidecar event stream. Every IQ chunk shall be correlatable with sample index, local monotonic time, LO, sample rate, RF bandwidth, gain, target, context version, decoder configuration, clipping metrics, and drop/overflow counters.

### FR-10 Acquisition Continuity

Acquisition, decoding, and disk writing shall use bounded queues so synchronous disk flush cannot block SDR reception. Report expected samples, received samples, dropped chunks, queue overflows, libiio errors, and effective acquisition duty.

### FR-11 Decoder Comparison

Both plugins shall be runnable on identical stored IQ and identical metadata. Shadow mode may run both, but only the configured primary decoder may feed the ROS output validator.

## 5. Non-Functional Requirements

- No decoder plugin may import ROS, pyadi-iio, or socket output code.
- Competition configuration must be explicit and versioned.
- Logs must be structured JSONL in addition to concise operator output.
- A failure must be classifiable as context, RF, acquisition, synchronization, frame validation, or ROS delivery.
- The system shall run on the existing radar host and Zynq7010/AD9363 hardware.
- Decoder CPU and memory use must not reduce acquisition duty below the acceptance threshold.

## 6. Hardware Requirements

- Establish a measured RF gain budget for antenna, external LNA, active SAW, cable loss, and SDR gain.
- Provide a bench-tested attenuation/LNA-bypass procedure for close-range transmitters.
- Prefer a short verified USB 3 data cable; record libiio reconnects and timeouts during endurance tests.
- Keep the active SAW only after confirming its passband, gain, compression behavior, and supply noise in the assembled chain.

## 7. Acceptance Criteria

1. Confirm the expected key and CRC evidence for each approved L1/L2/L3 positive fixture.
2. Decode approved fixtures repeatedly with both plugins under one metadata contract.
3. Produce no key from the field negative capture and classify its clipping/context faults.
4. Ignore transient invalid IDs and pre-match level-3 excursions without retuning.
5. Complete `IQ -> decoder -> DecodedCommand -> /sdr/jam_code -> radar main` closed-loop tests.
6. Demonstrate that radar main enters phase 2 after a known-good JamCode test input.
7. Run the assembled RF chain without sustained clipping at close range.
8. Demonstrate at least 99% acquisition duty in a representative endurance test, with zero unexplained drops.

## 8. Delivery Strategy

- `main`: preserved pre-refactor baseline and approved documents.
- `codex/open-source-replacement`: minimal common foundation plus adapted upstream decoder.
- `codex/hybrid-receiver`: full common foundation, both plugins, context arbitration, RF safety, and diagnostics.
- Merge back only after acceptance evidence identifies the production decoder and validates the common foundation.

