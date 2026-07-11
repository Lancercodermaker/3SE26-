# Radar Source Baseline

This import establishes a version-controlled source baseline for radar context task 3.

- `radar_referee` comes from the current local snapshot that already contains the SDR/JamCode integration.
- The complete `vision_interface` message package and the MIT license come from the complete radar project copy.
- The import is intentionally limited to `radar_referee` and its workspace dependency, `vision_interface`.
- Excluded content includes models, TensorRT engines, camera SDKs, media samples, caches, build artifacts, the complete vision/radar algorithm tree, and the duplicate `sdr_receiver_py_wrapper`.
- This commit does not add a `/judge/radar_context` publisher; that functional change remains part of the later radar context task 3.

No ROS/colcon build result is claimed for this mechanical baseline import.

## Known baseline property

The imported packages preserve the source snapshots' original bytes. A one-time
`git diff --cached --check` reports 98 legacy trailing/indentation whitespace
diagnostics across seven existing source files. This commit leaves them unchanged
to preserve SHA-256 and audit parity. Later functional commits must apply normal
diff checks to any lines they add or modify.
