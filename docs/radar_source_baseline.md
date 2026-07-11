# Radar Source Baseline

This import establishes a version-controlled source baseline for radar context task 3.

- `radar_referee` comes from the current local snapshot that already contains the SDR/JamCode integration.
- The complete `vision_interface` message package and the MIT license come from the complete radar project copy.
- The import is intentionally limited to `radar_referee` and its workspace dependency, `vision_interface`.
- Excluded content includes models, TensorRT engines, camera SDKs, media samples, caches, build artifacts, the complete vision/radar algorithm tree, and the duplicate `sdr_receiver_py_wrapper`.
- This commit does not add a `/judge/radar_context` publisher; that functional change remains part of the later radar context task 3.

No ROS/colcon build result is claimed for this mechanical baseline import.

## Audit and compatibility normalization

The byte-exact initial import is commit
`271529be647b7a574f49d23aac421a200c471229`; this commit is the stable audit
identifier for the original source snapshots.

Quality review found that the legacy `vision_interface` schema did not match the
current SDR-integrated consumer. No matching schema snapshot was available
locally, so the follow-up compatibility fix makes the smallest additive message
field extensions supported by the repository's existing interface contract. It
also completes the package manifests, MIT license metadata, and C++17 default.
The original bytes remain replayable at the initial import commit; the follow-up
fix is an intentional, versioned normalization.

## Known baseline property

The initial import preserves the source snapshots' original bytes. Its one-time
full diff check reports 98 legacy trailing/indentation whitespace diagnostics
across seven existing source files. Those original bytes remain unchanged at the
stable audit commit above. Later functional commits must apply normal diff checks
to any lines they add or modify.
