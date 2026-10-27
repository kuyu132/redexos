# 树莓派 SD 镜像、现场采集与自动验收

本轮已经生成可写入 SD 卡的 MBR/FAT32 镜像。它们包含板级 UEFI、校验过的设备树与串口 overlay、Redox `BOOTAA64.EFI`、live 系统，以及板内功能和稳定性脚本。最终的原始镜像已经从分区内逐文件回读，并作为整张 MBR 镜像在 QEMU 中重新启动到用户态。

| 板卡 | 原始镜像 | SHA256 | 压缩镜像 SHA256 |
| --- | --- | --- | --- |
| Pi 4B | `artifacts/sd-image/20261025T063912Z-pi4b-23098b34/redox-pi4b-sd.img` | `b3e1657ab09750d670947c38d340ab3aa06790644b60258172e4f217a060793a` | `5c08bf7cea9cd2d2bc8a1cd329d1e10922f90d6cb7f1ac1db39ce50aaf55194b` |
| Pi 5，SD 日志 v3 | `artifacts/sd-image/20261025T151501Z-pi5-09rcnulj/redox-pi5-sd.img` | `fa7fa113d99d5837497ea281860a3e739a110273886a2f7fc53244d335435ebd` | `5e4115eb2b84e38ecd0d0ba138db69de941bff2f59587c0072cc77744b726aff` |
| Pi 5，调用检查点 v4 | `artifacts/sd-image/20261025T235437Z-pi5-2nopy1j9/redox-pi5-sd.img` | `8c3f37666d6b5da046c48838adb56f472def18333ad9aa6c950ec187d6b8261a` | `7afdbfb64f03f85a9269343b948ebf62ff5599abd0f59114bf984104fad044ae` |

两张镜像都是 768 MiB，写入后卡上剩余容量暂不使用。Pi 4B 使用 PFTF UEFI v1.52。Pi 5 使用 WoR UEFI v0.3；该项目已经归档，且上游只报告在早期 BCM2712C1 板上测试过，所以 Pi 5 D0 修订必须当作新的硬件组合独立记录。

Pi 5 实机已确认 UEFI 加载 Redox 引导程序、传入设备树、完成 512 MiB 文件读取和首个文件头校验；v2 最后可见第 259 次元数据读取返回，v3 最后可见第 2 次读取 pending，尚未确认进入内核。2026-10-26 已从实卡只读备份并提取 v3 的 `BOOT0001.LOG`，最后落盘为 FS-CALL；同一 FAT 副本在 QEMU 中通过用户态测试，尚未复现实机停顿。详见 [SD 日志与提取](boot-logs-v3.md)。UEFI 之后的内核日志采集及板上自动验收仍使用下述串口链路。

## 串口连接

串口必须是 3.3 V、115200 8N1，USB 串口适配器的 TX 接板卡 RX、RX 接板卡 TX，并共地；不得把 5 V TTL 或 RS-232 电平接入 GPIO。

- Pi 4B：J8 pin 6 为 GND，pin 8/GPIO14 为板卡 TX，pin 10/GPIO15 为板卡 RX。
- Pi 5：使用板上标记为 `UART` 的三针专用调试接口，即 UART10。

树莓派官方文档确认 Pi 4 的主串口引脚和 Pi 5 的专用 UART10，并说明 UART 均为 3.3 V：[UART 配置文档](https://www.raspberrypi.com/documentation/computers/configuration.html#configure-uarts)。

## 写卡与完整回读校验

先用系统工具确认 SD 卡的**整盘设备名**。以下命令会再次显示设备信息，默认要求目标是可移动或 USB 设备，并要求输入完整确认字符串：

```bash
# macOS 示例；请替换 /dev/diskN
bash scripts/flash-sd.sh \
  artifacts/sd-image/20261025T063912Z-pi4b-23098b34/redox-pi4b-sd.img \
  /dev/diskN

# Linux 示例；请替换 /dev/sdX
bash scripts/flash-sd.sh \
  artifacts/sd-image/20261025T235437Z-pi5-2nopy1j9/redox-pi5-sd.img \
  /dev/sdX
```

脚本拒绝分区设备和当前根磁盘，卸载目标分区后写入，再读取完整 768 MiB 并与源 SHA256 比较。只有输出 `FLASH_VERIFY_PASS` 才能进入上板测试。`--yes` 只用于已经由外部自动化准确选定设备的场景；固定磁盘还必须额外给出 `--allow-fixed`。

如果 macOS 拒绝脚本的原始磁盘访问，可用 Raspberry Pi Imager 的“使用自定义镜像”选择上面的 `.img`，选定 SD 卡，并完成 Imager 自带的写入校验。本项目曾通过这条路径写卡；不能仅凭 macOS 没有挂载分区就判断写入失败。

## 首次启动

连接 HDMI 和 USB 键盘；有串口时先启动采集，再给板卡上电。第一次进入 UEFI 时按 `Esc`，把 `System Table Mode` 设置为 `Device Tree`，保存并重启；Redox 当前依赖 UEFI `DEVICE_TREE_GUID`，ACPI 模式不能替代。镜像内的 `redexos/FIRST-BOOT.txt` 也保存了这一约定。Pi 5 当前 v4 镜像保留了本卡固件设置和旧日志，应显示 `[BOOT-DIAG v4]` 和 `[SD-LOG] BEGIN v4 ... BOOT0002.LOG`；如停在分辨率选择画面，按 Enter 继续。再次失败时拍下最后的 `[SD-IO]` 与 `[FS-READ]` 行，然后断电插卡，先用 `collect-boot-logs.py` 归档日志再重刷，详见 [v4 诊断说明](boot-logs-v4.md)。

快速功能测试：

```bash
python3 scripts/verify-board.py pi4b /dev/cu.usbserial-实际设备 \
  artifacts/hardware/pi4b-quick \
  --image-manifest artifacts/sd-image/20261025T063912Z-pi4b-23098b34/manifest.json \
  --boots 1 --stability-rounds 10
```

工具会在每轮开始前提示冷启动，持续把原始串口按块 `fsync` 到宿主机，自动登录 `root/password`，验证本轮确实经过内核和 init，再执行板卡身份、临时文件读写、管道、子进程退出/等待、定时器和重复心跳测试。失败时 `result.json` 会记录最后里程碑、异常和最后 4000 字符，原始日志不会因来宾崩溃而丢失。

初始验收要求 5 次冷启动和至少 4 小时累计循环：

```bash
python3 scripts/verify-board.py pi4b /dev/cu.usbserial-实际设备 \
  artifacts/hardware/pi4b-acceptance \
  --image-manifest artifacts/sd-image/20261025T063912Z-pi4b-23098b34/manifest.json \
  --boots 5 --stability-rounds 2880 --command-timeout 4000
```

如果有可编程电源或继电器，可以提供操作员自行编写的 `--power-cycle-command '实际命令'`，否则每轮由操作员断电重启。证据目录包含 `serial.raw.log`、清理后的 `serial-clean.log`、逐事件 `events.jsonl`、每轮测试输出、镜像 manifest 副本和原子更新的 `result.json`。

## 保存和恢复现场

UEFI 设置完成并确认能再次启动后，关机、取出 SD 卡，保存这张卡前 768 MiB 的精确副本：

```bash
bash scripts/snapshot-sd.sh /dev/diskN \
  artifacts/sd-image/20261025T063912Z-pi4b-23098b34/manifest.json \
  artifacts/recovery/pi4b-calibrated.img
```

输出包含原始镜像、zstd 压缩副本、SHA256 和设备信息。需要恢复时把 `pi4b-calibrated.img` 再交给 `flash-sd.sh`，恢复所采集的 SD 区域。另行拍照记录 UEFI 设置；卡外 EEPROM/NVRAM 不属于 SD 快照，不能保证随快照恢复。保留失败时的卡，不要先重刷；先执行 `snapshot-sd.sh`，再在副本上比较 FAT 文件和哈希。

当前 Redox 在两块板上的运行时 SD 驱动尚未验证，live 根文件系统位于内存中。因此本轮可以可靠恢复启动介质和宿主机串口现场，但崩溃时尚不能从 SD 卡取得来宾内存态或持久用户文件。板载 SD、网络、USB、显示、SMP 和掉电恢复必须在对应驱动完成后加入下一层自动化套件，不能由本轮早期启动测试判定为通过。
