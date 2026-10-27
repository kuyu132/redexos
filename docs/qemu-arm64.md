# 本机 QEMU ARM64 验证

2026-10-22，在 Apple Silicon macOS 上完成本地编译的 Redox ARM64 内核及 EFI 的启动与用户态测试，并修复普通 Ion → Bash 启动路径的两个信号上下文错误。用户态和 initfs 以官方 server live 镜像为基础，内核与 relibc.so 已应用源码补丁并本地编译，尚未在 Mac 上从源码构建完整发行版。

## 直接运行

本机已准备镜像，在工程根目录执行：

```bash
python3 scripts/run-qemu-arm64.py --verify
# 或交互登录
python3 scripts/run-qemu-arm64.py
```

串口账号 `root`，密码 `password`。登录后直接输入 `bash`，或使用 `bash /usr/lib/qemu-smoke.sh` 执行测试。交互模式按 Ctrl-C 关闭 QEMU。

自动测试使用普通子进程启动方式，还验证 Bash 退出后 Ion 可以继续接受命令。历史 `exec bash` 绕行方案已移除；根因、补丁及失败对照见 [Ion 启动修复记录](ion-bash-fix.md)。

## 从源码与官方用户态重建

```bash
# 依赖：Git、Python 3、Rustup；本机这些依赖已就绪
brew install qemu llvm zstd mtools
rustup toolchain install nightly-2026-05-24 --profile minimal --component rust-src
bash scripts/build-macos-components.sh qemu
bash scripts/build-macos-relibc.sh
python3 scripts/prepare-qemu-arm64.py
python3 scripts/run-qemu-arm64.py --verify --repeat 5
```

版本由 `qemu/sources.lock.json` 固定。QEMU 基线与先前树莓派工程基线分开保存：

- 内核：GitLab `72fcb44fa88c3a74ec9efaaba9082cb2e141caa1`，源码在 `upstream/qemu-kernel`，附 `patches/kernel/0003-aarch64-signals-timer-and-device-irqs.patch`。
- relibc：官方镜像内嵌版本 `39e84406c8a42acbc4f4be002e04b8bbd704a335`，源码在 `upstream/qemu-relibc`，附 `patches/relibc/0002-aarch64-signals-and-tls-layout.patch`。
- EFI：`cc922ec0bb3f9dd31cce589bb513ab2f15ac9746`，源码在 `upstream/bootloader`。
- 用户态：官方 `redox_server_aarch64_2026-10-21_562_livedisk.iso.zst`，压缩包 SHA256 为 `4365d710d1b53d3b4a68f315173706d5997d46abd2b256eb28a839559c30d1d8`。
- Rust：`nightly-2026-05-24`。

内核版本先按官方镜像构建日期筛选，再实测兼容性；未断言它就是官方镜像内核的精确源码版本。GitHub 内核镜像比 GitLab 主线旧，不能把两者的 HEAD 混用。更新到 GitLab `6cfaba5` 时曾遇到 DT 定时器中断解析 panic，因此未采用该版本作为通过基线。

构建脚本按锁文件校验并应用补丁，拒绝覆盖其他源码修改。relibc 通过 Clang、Rust LLD 和上游 `math_libm` feature 在 Mac 交叉编译，无需 Linux GCC；记录 dlmalloc 子模块版本、Cargo.lock、补丁及编译工具版本。

准备脚本使用本地 Rust 工具 `tools/redox-image` 在镜像副本中替换 kernel、kernel.all、kernel.sym 和 libc.so，加入 shell 测试及独立信号寄存器测试程序，并读回校验替换内容。使用 mtools 制作 FAT32 EFI 载荷，避免依赖 macOS FUSE。内核、EFI、relibc、压缩下载、最终镜像均记录 SHA256。

`work/qemu-arm64/image.json` 指向最近准备的镜像。运行脚本先核验镜像哈希，再使用 QEMU 10.2.2 的 `virt,gic-version=2,acpi=off`、TCG、Cortex-A72、单核、2 GiB RAM 和 EDK2 AArch64 UEFI。固件路径可由 `--firmware` 指定，实际版本和哈希记录在每次运行的 inputs.json。

EFI 经 virtio MMIO 块设备读取 live 镜像，Redox 根文件系统在内存中运行。QEMU 使用 snapshot，测试写入不持久保存。

## 验收与证据

自动测试依次确认内核启动、init 切换根目录、串口登录，然后执行 `qemu/userspace-smoke.sh`：

1. 写入临时文件，经外部 cat 进程读回并比较内容。
2. 管道传输并比较内容。
3. 创建 Bash 子进程，执行并验证退出码 37。
4. sleep 1 成功返回。
5. 五次 SIGUSR1 处理、独立 C 程序的异步信号 x1/x2 保留检查。
6. 8 个 pthread 的 TLS 隔离、初始化、跨 DSO TLSDESC、64 字节实际地址对齐和跨页 TLS 检查。
7. 输出发行版信息，清理文件，最后打印 `QEMU_ARM64_SMOKE_PASS`。
8. 返回 Ion 提示符，再执行 `echo ION_RETURN_OK`；`--repeat` 指定重复次数。

最小回归分别为 `python3 scripts/run-qemu-arm64.py --probe-ion` 和 `python3 scripts/run-qemu-arm64.py --probe-signals`。

验证器在异常、超时或缺失标记时返回失败；不会把登录提示单独算作通过。每次运行保存 `artifacts/qemu-arm64/<UTC时间>-<后缀>/`，包括原始/清理后串口日志、QEMU 命令、输入哈希及 result.json。完整成功记录索引见 `docs/verification.json`。

无网络、显示和音频设备的配置会产生相关服务错误；这些功能未纳入验收。通过范围是 QEMU virt 单核下的启动和上述用户态操作，不包括持久磁盘读写、网络、多核或 Pi 4B/Pi 5 实机。显示和 USB 输入另由桌面配置验收，见下文。

诊断时可执行 `python3 scripts/prepare-qemu-arm64.py --kernel official` 保留官方内核，以隔离内核补丁因素；它仍使用本地 EFI 和修复后的 relibc，且会更新当前镜像指针。该配置不应作为修复后的验收基线。恢复默认基线时重新运行不带该参数的准备命令。

## 图形桌面

本机镜像已准备：

```bash
python3 scripts/run-qemu-arm64.py --desktop
```

在启动菜单用上箭头选择 **1024×768**，按 Enter；图形登录用户 `user`，密码为空。底部黑色图标启动终端，蓝色文档图标启动 COSMIC 编辑器。桌面默认 4 GiB RAM、单核 TCG、ramfb，以及键盘和指针各一个 xHCI 控制器。

完整重建：

```bash
bash scripts/build-macos-components.sh qemu
bash scripts/build-macos-relibc.sh
python3 scripts/build-macos-usbhidd.py
python3 scripts/prepare-qemu-arm64.py --profile desktop
python3 scripts/run-qemu-arm64.py --desktop
```

USB HID 驱动从锁定的 `base` 源码本地静态编译。链接使用本地 relibc.a/crt0.o，以及锁定校验值的 ARM64 `libgcc_eh.a`；仅取目标平台归档，不执行 Linux 工具。驱动、补丁、Cargo.lock 和镜像内读回哈希均纳入证据。`desktop-image.json` 与串口 server 的 `image.json` 分开维护。

桌面修复和实际操作结果见 [桌面验证记录](desktop-fix.md)。TCG 下窗口和文件选择器启动有延迟，操作后应等界面完成。该 live 模式写入仅存在于本次运行，退出 QEMU 后不会保留。

## 持久桌面

使用 `python3 scripts/run-qemu-arm64.py --desktop --persistent` 启动 4 GiB 可写磁盘配置。终端与编辑器文件可跨 QEMU 进程退出/重新启动保留；普通 EFI、AHCI/initfs、中断修复、首次准备和自动重启读回回归见 [持久存储说明](qemu-persistence.md)。原来的 `--desktop` 仍保留 live 模式，用于隔离验证。

## 网络

持久桌面命令 `python3 scripts/run-qemu-arm64.py --desktop --persistent` 默认启用 E1000 用户态 NAT，已验证自动 DHCP、DNS、HTTPS 下载及新 QEMU 进程中的自动恢复。重复验证：`python3 scripts/verify-qemu-network.py`。配置、证据和范围见 [网络验证记录](qemu-network.md)。
