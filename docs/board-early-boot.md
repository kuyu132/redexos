# Pi 4B / Pi 5 板级早期启动基线

当前已经完成设备树输入锁定、串口/GIC/定时器离线合同检查，以及相关 ARM64 内核补丁的 QEMU 回归。截至 2026-10-26，Pi 4B 尚无实机结果；Pi 5 已进入 Redox EFI、收到 DeviceTree、完成 512 MiB 文件读取并校验首个 RedoxFS 文件头，但停在内核之前。v3 实卡日志已读回，v4 诊断镜像已写卡校验，尚无 v4 实机结果。证据见 [v4 诊断说明](boot-logs-v4.md)，后续按 [分层定位方法](bringup-methodology.md)推进；以下离线 DTB 合同和历史 QEMU 结果不代表实机通过。

## 重建设备树候选

```bash
python3 scripts/prepare-board-dtb.py pi4b
python3 scripts/prepare-board-dtb.py pi5
```

脚本从 Raspberry Pi firmware commit `88a0ae8876e800801ee5c27624138b3d02a21c63` 获取校验和锁定的文件。Pi 4B 应用同一版本的 `disable-bt.dtbo`，使 `serial0` 指向 GPIO14/15 上的 PL011；Pi 5 保留 `serial10`，使用 BCM2712 上的专用调试 UART10，不依赖 RP1 UART0。

| 合同 | Pi 4B | Pi 5 |
| --- | --- | --- |
| stdout | `serial0:115200n8` | `serial10:115200n8` |
| PL011 物理地址 | `0xfe201000` | `0x107d001000` |
| UART 中断 | GIC SPI 121，level-high | GIC SPI 121，level-high |
| GIC-400 物理地址 | `0xff841000` | `0x107fff9000` |
| 输出 DTB SHA256 | `10f5ba94776526de5ad410c6da66c800e76e24951f95a3448bb0b474cffb7741` | `03ce4742452ad52e3e8ec2259a0c705b496970e2b75e6e0b4e5446475ea1b926` |

本轮证据位于 `artifacts/board-baseline/20261025T063001Z/`。其中每块板都有 `redox.dtb`、`config.txt`、`verification.json` 和 `SHA256SUMS`；同级的 `pi4b-sources.json` 与 `pi5-sources.json` 记录实际 recipe 配置、补丁内容和工程输入哈希。

## 内核接入

Pi 5 官方 DTB 的根 SoC 节点是 `/soc@107c000000`。原内核设备内存扫描只查找精确路径 `/soc`，会漏掉这个节点下的 GIC 等直接子设备。锁定的 `patches/kernel/0004-aarch64-native-ptrace.patch` 现在同时识别 `soc` 和带非空 unit address 的 `soc@...`，并让直接子设备统一走完整的父总线 `ranges` 地址转换。

该累计补丁 SHA256 为 `8ed8d63a05c7def5a4e54fbfbc5c2730a7b68d702ae77192ea79bf8978fa20c7`。`scripts/prepare.py` 会验证哈希并将它接入 Pi 4B 和 Pi 5 的 kernel recipe；base、relibc、RedoxFS 的 QEMU 已验证补丁也使用相同方式接入。2026-10-25 的 macOS ARM64 构建产物位于 `artifacts/macos-arm64/20261025T062718Z-dzVdKK/`，其中 kernel SHA256 为 `c9a1590368767d1d82ce18d39ecd76c8f4bc8870c13c8031a7357b732d7a898d`。

新内核在 QEMU `virt,gic-version=2,acpi=off` 上重新通过 EFI → 内核 → init → Ion → 普通 Bash 用户态回归，证据位于 `artifacts/qemu-arm64/20261025T062928Z-w4fabj6d/`。22 项宿主机自动测试同时覆盖 DTB 二进制解析、`soc@` ranges 转换、补丁防篡改和验证器回归。

内核完整宿主机单元测试目标仍被上游已有的 `context/switch.rs` 测试签名不一致阻塞；本次改动已由 release 构建、独立 DTB 解析测试和 QEMU 冷启动回归覆盖。

## 实机下一步

启动固件必须通过 UEFI 配置表交付最终 DTB。EEPROM 和固件可能修改内存、reserved-memory、aliases、chosen、ranges 或状态属性，所以不能只依赖仓库中的参考 DTB。首次上板需要保存最终交接 DTB、固件/EEPROM/UEFI 版本与串口全日志，然后与本页合同逐项比较。

当前写卡镜像已经锁定 [PFTF RPi4 UEFI](https://github.com/pftf/RPi4) v1.52 和 [rpi5-uefi](https://github.com/worproject/rpi5-uefi) v0.3，精确归档 SHA256 位于 `board-firmware.lock.json`。Pi 5 项目已归档，并明确限制在早期 BCM2712C1 测试范围，所以它仍是实机实验依赖；任何 Pi 5 修订都要记录并独立验收。写卡、串口测试与恢复流程见 [实机测试手册](hardware-testing.md)。
