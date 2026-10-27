# 上游核对记录（更新于 2026-10-25）

这是一轮源码、设备树和构建路径审阅。随后已在 macOS arm64 完成内核和普通/live EFI 引导程序的交叉编译，并用 QEMU 验证用户态回归；未完成 Ubuntu 完整系统编译或硬件验证。源码锁中的核心 recipe 现在会装入校验和锁定的本地补丁。

| 项目 | 本轮证据 | 对移植的影响 |
| --- | --- | --- |
| 已有硬件记录 | Redox HARDWARE.md 记载 Pi 3B+ UART 启动；未列出 4B、5 成功记录 | 两款维持 not-tested |
| 镜像配置 | Redox `config/aarch64/raspi3bp/minimal.toml`、`mk/config.mk` | 使用相同 include 机制创建两板配置；隔离 checkout 与配置名 |
| AArch64 EFI | `recipes/core/bootloader/recipe.toml` 与 bootloader `mk/aarch64-unknown-uefi.mk` | 可构建普通/live EFI 程序，不代表固件兼容性 |
| 内存盘 | bootloader `src/os/uefi/device.rs`、`src/main.rs` | live loader 查找同卷根目录的 redox-live.iso，并传递 DISK_LIVE_ADDR/SIZE；仍需验证用户态挂载 |
| 设备树交接 | bootloader `src/os/uefi/dtb.rs` | 查找 UEFI DEVICE_TREE_GUID，复制 DTB；固件必须提供正确的表 |
| UART | kernel `src/dtb/serial.rs`、`src/devices/uart_pl011.rs` | 已有 PL011；当前 init 保留固件设置的 baud divisor，因此时钟与波特率交接必须实测 |
| MMIO | kernel `src/dtb/mod.rs`、`patches/kernel/0004-aarch64-native-ptrace.patch` | 复用嵌套 ranges 转换；补丁让设备内存扫描同时识别 `/soc` 与 `/soc@<unit-address>`，覆盖 Pi 5 根 SoC 总线 |
| 中断 | kernel `src/arch/aarch64/device/irqchip/mod.rs` | 识别 GIC-400/GICv2；未知控制器返回 Null，不等于可处理其子设备中断 |
| CPU | kernel `Cargo.toml` 和 `src/arch/aarch64/device/cpu/boot.rs` | 已有多核/PSCI 相关路径；本项目尚未关闭默认 multi_core 或验证板上启动方法 |
| QEMU | Redox `mk/qemu.mk` | Pi 3B+ 有专门分支，其余 AArch64 默认走 virt；不能当双板模拟器 |

上述内核/bootloader 路径相对于各自仓库。完整 commit 和补丁校验值见 `sources.lock.json`。板级 JSON 不参与内核编译；`scripts/prepare.py` 会把锁定补丁接入两块板的核心 recipe。

锁定的 Raspberry Pi 固件 DTB 已通过离线合同检查。Pi 4B 在应用官方 `disable-bt.dtbo` 后，`serial0` 解析到 PL011 物理地址 `0xfe201000`；Pi 5 的 `serial10` 解析到 BCM2712 侧 `0x107d001000`。两者 UART SPI 为 121，根中断控制器均为 GIC-400。详细哈希、重建命令和限制见 [板级早期启动基线](board-early-boot.md)。这项结果验证输入 DTB 和内核解析路径，不代表固件已经成功把同一 DTB 交给 Redox。

硬件方面的设计依据：4B 与 Pi 5 分别使用 BCM2711 / BCM2712；两者均可复用 GICv2 和 PL011 的通用逻辑。Pi 5 的 RP1 经 PCIe 连接，USB/网络必须核对其 MMIO、DMA 和中断路径；专用 UART10 与主芯片 SD 控制器可以先于 RP1 外设推进。这里没有把设备树总线地址抄成 CPU 物理地址。

参考资料：

- [Redox 硬件记录（固定版本）](https://github.com/redox-os/redox/blob/b3f371c1bac6bb4c88ef4eb4b30139d2e007580f/HARDWARE.md)
- [Redox 构建配置（固定版本）](https://github.com/redox-os/redox/blob/b3f371c1bac6bb4c88ef4eb4b30139d2e007580f/mk/config.mk)
- [内核设备树实现（固定版本）](https://github.com/redox-os/kernel/blob/b68957d4a3e1590c4f5b8d5608d94b3e45bd3b45/src/dtb/mod.rs)
- [EFI live 文件加载（固定版本）](https://github.com/redox-os/bootloader/blob/cc922ec0bb3f9dd31cce589bb513ab2f15ac9746/src/os/uefi/device.rs)
- [U-Boot 树莓派配置说明](https://docs.u-boot.org/en/stable/board/broadcom/raspberrypi.html)
- [树莓派 UART 文档](https://www.raspberrypi.com/documentation/computers/configuration.html#configuring-uarts)
- [RP1 官方介绍](https://www.raspberrypi.com/documentation/computers/io-controllers.html)
- [BCM2711 设备树参考](https://github.com/raspberrypi/linux/blob/7bc8e3ebb16d7287ad155d8d33c01c04dda1ee3d/arch/arm/boot/dts/broadcom/bcm2711.dtsi)
- [BCM2712 设备树参考](https://github.com/raspberrypi/linux/blob/7bc8e3ebb16d7287ad155d8d33c01c04dda1ee3d/arch/arm64/boot/dts/broadcom/bcm2712.dtsi)
- [锁定的 Raspberry Pi 固件设备树目录](https://github.com/raspberrypi/firmware/tree/88a0ae8876e800801ee5c27624138b3d02a21c63/boot)

仍需锁定实际固件交付的 DTB，而非直接假定 Linux 源树生成的 DTB 就是 UEFI 交给 Redox 的版本。需要保留固件修订、overlay、/chosen/stdout-path、aliases、memory、reserved-memory、interrupt-parent、ranges、dma-ranges 和 PSCI 等信息。
