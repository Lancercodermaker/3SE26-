# 3SE26 SDR 解析波接收工程

本仓库保存 3SE 2026 雷达 SDR 解析波接收工程的重构前基线，以及后续重构所需的需求分析和架构设计文档。

## 工程目录

- `sdr_receiver_py_wrapper`：Python/ROS 2 比赛接收程序及其内置解调器。
- `sdr_receiver`：C++/ROS 2 接收程序、消息定义和相关配置。
- `docs`：需求分析、架构设计、接口约定、射频扫描及部署文档。

仓库有意排除了 IQ 录波、扫描日志、虚拟环境和构建产物。当前 `main` 分支用于保留重构前源码基线与已确认的设计文档，后续开源替换方案和融合改进方案将在独立分支中实现和验证。

## 重构文档

- [需求分析](docs/superpowers/specs/2026-07-10-sdr-receiver-requirements.md)
- [架构设计](docs/superpowers/specs/2026-07-10-sdr-receiver-architecture-design.md)

## 实现计划

- [雷达主工程上下文证据话题实现计划](docs/superpowers/plans/2026-07-11-radar-context-integration.md)
- [公共接收底座与融合方案实现计划](docs/superpowers/plans/2026-07-11-common-receiver-foundation.md)
- [开源解调插件与软硬件验收实现计划](docs/superpowers/plans/2026-07-11-upstream-decoder-and-validation.md)
