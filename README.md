# 3SE26 SDR Receiver

This repository contains the pre-refactor baseline of the 3SE 2026 radar SDR receiver.

## Packages

- `sdr_receiver_py_wrapper`: the Python/ROS 2 competition receiver and its vendored decoder.
- `sdr_receiver`: the C++/ROS 2 receiver implementation and message definitions.
- `docs`: existing requirements, architecture, interface, and RF scan documents.

IQ recordings, scan logs, virtual environments, and generated build artifacts are intentionally excluded. This baseline preserves the current source before the replacement and hybrid receiver designs are implemented.

