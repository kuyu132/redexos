# Pi 5 启动诊断 v4

v4 针对实卡 v3 日志与照片不一致的停点，增加屏幕上的日志调用检查点。**这是诊断版，尚未证明修复 Pi 5 启动停顿。** 原因分析与真实 SD 日志见 [v3 读回记录](boot-logs-v3.md)。

实卡日志 `BOOT0001.LOG` 的最后一条是 FS-CALL，屏幕多显示了 FS-READ #2 pending。因此 v4 在第 2、259 次文件系统读取的 pending/returned 记录中，依次输出：

1. `console returned; acquiring log lock`
2. `formatting record`
3. `formatted len=... buf=... sp=...`
4. `calling firmware Write`
5. `firmware Write returned`
6. `calling firmware Flush`
7. `firmware Flush returned`

这些 `[SD-IO]` 行直接输出到屏幕，避免在诊断 FAT 调用时递归写 FAT；普通引导检查点继续写入每次启动独立的日志。短写会重复 Write/returned 组合，错误不会显示伪造的成功阶段。最后一行用于缩小排查范围，仍需考虑诊断输出本身或异步异常，不能只凭它认定具体根因。

下一次实机启动应显示 `[BOOT-DIAG v4]`、`[SD-LOG] BEGIN v4 ... BOOT0002.LOG`。镜像保留了本次实卡的 `RPI_EFI.fd`（含 UEFI 设置）和 `BOOT0001.LOG`；系统镜像也逐字节保持一致。若仍停住，拍下最后的 `[SD-IO]` 与 `[FS-READ]` 行，再断电插卡取日志。

镜像：`artifacts/sd-image/20261025T235437Z-pi5-2nopy1j9/redox-pi5-sd.img`，768 MiB，SHA256 `8c3f37666d6b5da046c48838adb56f472def18333ad9aa6c950ec187d6b8261a`。

## 证据与限制

- 原始实卡 FAT 备份、照片、日志与重放：`artifacts/hardware/pi5-v3-read2-20261025T153456Z-uk_q1b87`。
- 最终构建：`artifacts/macos-arm64/20261025T235333Z-JmgInp`；宿主 33 项测试通过，包含 14 项直接使用生产 Rust 代码的嵌入测试。新增测试验证每次 Write/Flush 前后的事件顺序、短写以及失败时不出现 Flushed。
- 启动回归：`artifacts/boot-diagnostics/20261025T235505Z-40e79mrx`，检查 EL1/A72 与 EL2/A76 启动到用户态、四组完整 SD-IO 顺序及五种 EFI 错误路径。
- 日志回归：`artifacts/boot-log-tests/20261025T235505Z-ppw682cp`，验证实际旧日志保留、新日志遭遇强制终止后读回、重启继续记录、只读介质和真实 EFI 错误落盘。测试发生在镜像副本上。
- 累计补丁：`patches/bootloader/0004-log-io-stages.patch`，两个源码锁均已更新。v3 补丁与失败镜像仍保留。

该镜像从发布版 MBR 前缀与只读采集的实卡启动分区重建，保留实际 FAT 内容。原始 `.cdr` 仅覆盖启动分区，不能称为整张 64 GB SD 或 EEPROM 的完整备份。QEMU 使用自己的 EDK2 固件，通过测试不等于 Pi 5 实机通过。最终写卡状态查阅 `docs/verification.json`。
