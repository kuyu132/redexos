# Redox：树莓派 4B / 5 移植工程

最终目标是在树莓派 4B / 5 上正常显示图形界面，并能用于日常开发和工作。验收必须覆盖桌面与键鼠交互、持久保存文件、联网，以及实际编辑、构建、运行和调试工作流；冷启动进入用户态只是阶段一门槛。

两款板共用 ARM64 内核基础与通用驱动，分别实现板级集成。原计划以 4B 建立启动基线；按当前可用硬件，先推进 Pi 5 实机早期启动，4B 维护构建和离线检查。QEMU 验证公共实现、图形桌面和应用链路，两款板的实机体验分别验收。完整的完成标准见 [桌面与开发工作验收](docs/porting-plan.md#桌面与开发工作验收)。

**当前交付：Pi 4B/Pi 5 可写卡镜像、Ubuntu 构建入口及本机 QEMU ARM64 持久桌面。Pi 5 新版加入 HDMI 可见的启动阶段、镜像读取进度和错误偏移，已通过整张镜像的 QEMU 用户态验证及异常镜像测试。实机照片已确认 Pi 5 进入 Redox 引导程序；内核和用户态仍待上板验证。** `boards/*.json` 是移植元数据，不会被内核读取，不能替代板级驱动。

上游源码位于 `upstream/kernel`（内核）、`upstream/bootloader`（引导程序）、`upstream/redox`（构建系统与软件包配方）。两块板的 recipe 现在会自动装入 QEMU 已验证的 ARM64 内核、base、relibc 和 RedoxFS 补丁；内核补丁还修正了 Pi 5 `/soc@...` 根总线的设备内存登记。Pi 专用 SD、网络、USB 和显示驱动仍未实现。源码目录不包含在轻量脚本包中，由准备/构建脚本按锁定版本下载。

早期启动设备树候选可以在 macOS 或 Ubuntu 上生成并校验：

```bash
python3 scripts/prepare-board-dtb.py pi4b
python3 scripts/prepare-board-dtb.py pi5
```

Pi 4B 的通用固件参考合同使用 `disable-bt`；实际 PFTF 写卡镜像使用其发布包自带的 `miniuart-bt` 与 `upstream-pi4`，两条路径都把 GPIO14/15 的 `serial0` 切换到 PL011。Pi 5 使用 BCM2712 侧的专用 UART10。地址、中断、GIC 和定时器合同及当前证据见 [板级早期启动基线](docs/board-early-boot.md)。

当前可写卡镜像、串口接线、UEFI 首次设置、完整写后回读、自动冷启动/稳定性测试及现场恢复步骤见 [实机测试手册](docs/hardware-testing.md)。镜像位于：

- `artifacts/sd-image/20261025T063912Z-pi4b-23098b34/redox-pi4b-sd.img`
- `artifacts/sd-image/20261025T235437Z-pi5-2nopy1j9/redox-pi5-sd.img`

Pi 5 实机尚未进入内核。2026-10-26 已通过 macOS 授权后的只读分区备份，取回 v3 实卡 `BOOT0001.LOG`：最后落盘为 FS-CALL，照片多出 FS-READ #2 pending。同一 FAT 副本在 QEMU 中通过用户态测试，未复现实机停顿。[v4 诊断版](docs/boot-logs-v4.md) 保留原日志、固件设置和系统载荷，在第 2、259 次读取增加屏幕上的日志格式化、Write、Flush 检查点；QEMU 和日志持久化回归已通过，尚未证明修复实机故障。取证方法见 [SD 启动日志与读回](docs/boot-logs-v3.md)。

后续采用 [双平台开发与故障定位方法](docs/bringup-methodology.md)：固定失败现场，用独立小实验区分 FAT 日志、固件 I/O 和 RAM 文件系统处理；按宿主、QEMU、实机分别验收。当前优先推进已有 Pi 5 的早期启动，Pi 4B 保留构建与离线检查。最新能力边界见 [当前功能矩阵](docs/porting-plan.md#当前功能矩阵)。

[启动隔离测试](docs/boot-isolation.md)已提供独立 EFI、宿主程序、日志 A/B/A 对照、FAT 写入与重启读回、RAM RedoxFS 打开及异常输入测试。宿主和 QEMU 已验证，Pi 5 实机尚待执行；本轮未制作或刷写新的修复镜像。

本机核心组件编译方法见 [macOS 编译说明](docs/macos.md)：`bash scripts/build-macos-components.sh`。

QEMU 持久桌面现已默认启用 E1000 用户态 NAT，启动后自动获取 DHCP 地址与 DNS。已验证 HTTPS 下载和关闭 QEMU 后重新启动自动恢复，见 [网络验证记录](docs/qemu-network.md)。

QEMU 持久桌面已安装原生 Rust、Cargo、rustfmt、Make 和 GNU GDB。ARM64 ptrace 已接通，可在来宾终端完成原生编译、运行、源码断点、参数读取、单步和调用栈检查；使用及重启回归方法见 [原生调试说明](docs/qemu-native-debugging.md) 和 [开发工具链说明](docs/qemu-development.md)。已修复 RedoxFS 关闭映射缓存、调试等待通知和同步终端调用的定时唤醒问题。

QEMU 持久桌面已准备，执行 `python3 scripts/run-qemu-arm64.py --desktop --persistent`。终端和编辑器文件保存在 `work/qemu-arm64/persistent-desktop/root.img`，已验证关闭 QEMU 后重新启动读回。重建、数据目录与自动回归见 [持久存储说明](docs/qemu-persistence.md)。

本机 QEMU 镜像已准备，可执行 `python3 scripts/run-qemu-arm64.py --verify` 自动验证，或不带 `--verify` 进入交互串口。重建步骤、Ion 启动修复和证据说明见 [QEMU ARM64](docs/qemu-arm64.md)。该配置使用独立的 `qemu/sources.lock.json` 和 `upstream/qemu-kernel`。

QEMU 图形桌面入口：`python3 scripts/run-qemu-arm64.py --desktop`。已修复 ARM64 TLS 布局、调度时钟与 QEMU USB 输入路径，并验证图形终端和编辑器的保存、重新打开、修改与读回；详见 [桌面验证记录](docs/desktop-fix.md)。桌面使用官方应用与本地编译的内核、EFI、libc 和 USB HID 驱动，该 live 入口的文件不跨重启保留；需要保存数据时使用上面的 `--persistent` 入口。

Ion 普通启动 Bash 的信号上下文错误已修复：内核和 relibc 均有锁定的源码补丁，默认验证直接使用普通命令启动并检查返回 Ion。重建时还需执行 `bash scripts/build-macos-relibc.sh`，详见 [修复记录](docs/ion-bash-fix.md)。

Ubuntu 机器建议使用 22.04 或更新版本、x86_64 / aarch64 架构。复制本目录到不含空格的路径，例如 `~/redexos`；可以排除 `upstream/`、`work/`、`artifacts/` 和 `.git/`。脚本会重新获取锁定的源码。预留较多磁盘空间，实际需求取决于上游依赖和缓存，当前未测量。

```bash
cd ~/redexos
bash scripts/ubuntu-deps.sh
bash scripts/build-ubuntu.sh pi4b --dry-run
bash scripts/build-ubuntu.sh pi4b
bash scripts/build-ubuntu.sh pi5
```

依赖安装使用 `sudo apt-get`，构建以普通用户运行。通过上游 Podman 构建流程生成载荷，需要网络、可工作的 rootless Podman 和 `/dev/fuse`。容器依照上游配置使用 host 网络、`SYS_ADMIN` capability 和 FUSE 设备。初次构建可能下载较多依赖，日志会完整保存。

构建成功后，每次运行各有独立的 `artifacts/<板名>/<UTC时间>-<随机后缀>/`：

- `payload/redox-live.iso`、`payload/EFI/BOOT/BOOTAA64.EFI` 和 `payload/SHA256SUMS`。
- `board-contract/redox.dtb`、`config.txt`、`verification.json` 和 `SHA256SUMS`；这是已校验的交接候选，不会自动被 UEFI 使用。
- `sd-image/redox-<板名>-sd.img`、压缩镜像、完整 manifest 和 `SHA256SUMS`。
- `build.log`、`command.txt`、`host.txt`、`exit-code.txt`。
- 源码锁、实际解析的源码版本和 recipe lock、容器信息、板卡元数据。

`payload/redox-live.iso` 仍只是构建中间件，不能直接刷卡；应使用同一运行目录下 `sd-image/*.img`。镜像包含 UEFI 和 FAT 启动分区，但首次启动仍要在 UEFI 中选择 Device Tree 模式，并通过串口保存实机证据。

完整步骤见 [Ubuntu 操作说明](docs/ubuntu.md)，技术边界见 [上游核对记录](docs/baseline-audit.md)，里程碑与实机记录见 [移植与验收计划](docs/porting-plan.md)。

源码固定范围是构建系统和六个核心仓库，详见 `sources.lock.json`；其余包源码、二进制工具链归档、容器基础镜像 digest 和板卡固件还没有全部锁定。因此目前是**可重复执行的准备流程**，尚不能承诺逐字节可重现的系统镜像。

```bash
# 只准备源码，不安装依赖、不构建
python3 scripts/prepare.py prepare pi4b
python3 scripts/prepare.py verify pi4b
# 脚本自身的隔离/版本锁/防覆盖检查
python3 -m unittest discover -s tests -v
```

`work/pi4b/redox` 和 `work/pi5/redox` 相互隔离，镜像配置名、容器 tag、容器 home 及 recipe 构建缓存也隔离。重复 prepare 不会重置已有 checkout；遇到源码版本变化或配置修改会报错，需要先保留并审查本地改动。
