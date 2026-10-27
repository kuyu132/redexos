# SD 启动日志 v3

v3 将 AArch64 UEFI 引导阶段的控制台检查点和 `log` 记录同步写到**启动 FAT 分区**的 `redexos/logs/BOOT0001.LOG`。下一次启动创建 `BOOT0002.LOG`，已有日志不覆盖、不自动清理。屏幕出现 `[SD-LOG] BEGIN v3 ...` 表示已打开本轮文件；后续若出现 `DISABLED`，则以该提示为准。

每条记录处理短写并调用 UEFI `Flush`。记录使用固定 2048 字节缓冲，超长记录标注 `[TRUNCATED]`；单次启动最多 4 MiB，最多使用 9999 个文件名。空间不足、只读介质或写入/刷新失败时会提示并关闭日志，继续尝试启动。日志满后，先在电脑上归档，再自行删除卡上的旧日志。

这不是内核崩溃转储：日志在最终内存映射和 `ExitBootServices` 之前关闭。`END UEFI` 只说明到达交接前的检查点，不能证明内核或用户态启动成功。UEFI 自身、外部 `uefi_std` panic handler 的直接输出、离开 UEFI 后的内核日志尚未接入此文件。已有 `[BOOT-ERROR]` 和通过 `log` 输出的错误会保存。固件若卡死在写入/刷新调用中，也无法保证当前记录完成；屏幕先打印，所以可能比落盘日志多一条。FAT 和 SD 内部缓存对真实断电的可靠性仍需实卡验证。

## 本次照片能确认什么

v2 照片已归档到 `artifacts/hardware/pi5-v2-read259-20261025T151150Z-iwxxudy0`：

- 使用 Device Tree、EL2，512 MiB 文件读取完成；首个文件头 `valid=true`，generation 8960。
- 最后可见 `[FS-READ] #259 block=0x2f70 returned=0x1000 processing`。这表示磁盘接口已经返回，尚不能证明 RedoxFS 打开完成，也不是明确的 CPU 异常码。
- 结合锁定的 RedoxFS 0.8.0 源码和 QEMU 对照，第 259 次是分配器元数据链第一个块。其后涉及校验、前向队列入队和读取上一块，不能仅凭照片认定某一步出错。
- 对**本地原始镜像**离线检查：256 个环形文件头校验通过，选中 generation 9154；元数据链绝对块 `0x2f70 → 0x2d22 → 0x2d8c` 均通过 SeaHash 校验。第一块与最后选中的文件头关联，不能用最早的 generation 8960 代替最新文件头。检查程序、块副本及结果在同一证据目录。

上述离线检查并非实卡回读，更不能证明实机 RAM 内容正确。实机停顿尚未复现于 QEMU，根因仍未确定。v3 保留 v2 的整个 `redox-live.iso`，只更新 EFI、首次启动说明和镜像元数据，避免在这轮同时更换内核或用户态。

## 取回日志

最新 v3 实机照片归档于 `artifacts/hardware/pi5-v3-read2-20261025T153456Z-uk_q1b87`。屏幕显示已打开 `BOOT0001.LOG`，512 MiB 加载完成、首个文件头校验通过，最后停在 `[FS-READ] #2 block=0x200 len=0x1000 pending`。此处先输出控制台，再写入/刷新 FAT 日志，最后才执行 RAM 镜像读取，所以仅凭照片无法区分日志调用停顿与内存读取停顿。它与 v2 的第 259 次读取返回后的停点不同，不能合并为同一已确认根因。

2026-10-26，用户确认 macOS 授权后，`hdiutil create -srcdevice /dev/disk6s1 -format UDTO .../boot-partition` 成功只读备份 804257792 字节启动分区，原始 `.cdr` 已保存并设为只读。备份 SHA256 为 `0d57d2ca70e0f266c1dceee61da88a6216aaedfa9f288e276150f764af3e8880`。源卡未改写。

提取出的 `collected/BOOT0001.LOG` 长 33613 字节，SHA256 为 `2a5d9010d99eb513ba0d9e0dc341dd30039305cb4a57c131ddc6f219310f646c`，最后落盘记录为 `[FS-CALL] Opening header ring and allocator; watch FS-READ`。照片中的 `FS-READ #2 pending` 尚未落盘；没有错误码或 UEFI 交接完成标记。控制台输出与落盘之间包含日志锁、格式化、写入和刷新，故障还不能精确归因于其中一个调用。

实卡 EFI、系统镜像、config.txt 和控制台 overlay 与发布版本逐字节哈希一致。`RPI_EFI.fd` 的 3856 个变化字节全部位于 NV 变量区 `0x1d0000..0x1de000`，其中包含已保存的 SystemTableMode、启动项等；代码区保持一致。变量区位置核对自[该固件版本的布局源码](https://github.com/worproject/edk2-platforms/blob/8e1779b538bcc1e6dc68d7df625394f933651d7a/Platform/RaspberryPi/RPi5/RPi5.fdf)。这不等于逐项验证固件配置正确。

将实卡 FAT 副本接入 QEMU Cortex-A76/EL2 后，Redox 成功启动到用户态，文件读写、管道、子进程、定时器、信号和 TLS 测试通过；原 `BOOT0001.LOG` 字节不变，新 `BOOT0002.LOG` 记录到 UEFI 交接前。证据为同目录 `qemu-replay/result.json`。QEMU 使用自己的 EDK2 固件，这次对照未复现 Pi 5 实机停顿，不能宣告实机故障修复。

本轮镜像：`artifacts/sd-image/20261025T151501Z-pi5-09rcnulj/redox-pi5-sd.img`（768 MiB），SHA256 `fa7fa113d99d5837497ea281860a3e739a110273886a2f7fc53244d335435ebd`。进入 UEFI 后保持 `System Table Mode = Device Tree`。

板卡断电后插卡，**先收集，后重刷**。提取工具只读源文件，生成独立证据目录：原始日志、每份日志的 SHA256、最后检查点、错误行、交接标记和镜像元数据。没有文件会明确返回 `no_logs`（退出码 2），读取权限或 FAT 错误会报失败，均不会伪装成采集成功。

```bash
# 已挂载的启动分区；替换真实挂载路径，输出目录必须不在 SD 上
python3 scripts/collect-boot-logs.py --volume /Volumes/REDOXPI5

# 已保存的完整 SD 镜像，自动读取第一 MBR FAT 分区的偏移
python3 scripts/collect-boot-logs.py --image /path/to/failing-sd.img

# Ubuntu：将 /dev/sdX 替换为通过 lsblk 确认的 SD 整盘，先卸载分区
# 需要 Python 3 与 mtools。此命令只读设备，输出写入宿主目录。
sudo python3 scripts/collect-boot-logs.py --image /dev/sdX \
  --output "$PWD/artifacts/hardware/pi5-boot-capture"

# 如果提供分区设备，明确指定 offset 0
sudo python3 scripts/collect-boot-logs.py --image /dev/sdX1 --offset 0 \
  --output "$PWD/artifacts/hardware/pi5-partition-capture"
```

macOS 将这张卡识别为 MBR `0xEF`，没有自动挂载。直接设备读取和管理员 `mount_msdos -o rdonly` 曾被拒绝，但通过系统授权的 `hdiutil` 只读备份现已实卡验证成功，无需改变分区类型或格式化。先用 `diskutil list` 核对设备编号；下例的 `/dev/disk6s1` 是本次确认过的启动分区，重新插卡后必须重新核对。`hdiutil` 可能弹出系统授权窗口，由用户在本机确认。

```bash
# 只读源卡；备份和采集输出均写到电脑，使用新的输出位置
mkdir -p /path/to/new-host-evidence
hdiutil create -srcdevice /dev/disk6s1 -format UDTO \
  /path/to/new-host-evidence/boot-partition
python3 scripts/collect-boot-logs.py \
  --image /path/to/new-host-evidence/boot-partition.cdr --offset 0 \
  --output /path/to/new-host-evidence/collected
```

## 自动回归

```bash
python3 scripts/verify-boot-log.py --image-manifest /path/to/manifest.json \
  --baseline-manifest artifacts/sd-image/20261025T144229Z-pi5-ktg1uajg/manifest.json
python3 scripts/verify-boot-diagnostics.py --image-manifest /path/to/manifest.json
python3 -m unittest discover -s tests -v
```

日志回归使用测试副本、可写磁盘和 `cache=directsync`，不使用 QEMU snapshot。它验证：v2 在同一位置没有日志（反例）；v3 到第 259 次读取附近被 `SIGKILL` 后可读回检查点；重启后旧日志字节不变、新日志到达交接、用户态 smoke 通过；只读磁盘提示后仍启动且磁盘哈希不变；真实 EFI 错误路径的 `[BOOT-ERROR]` 可从 FAT 读回。它没有模拟 SD 控制器内部的掉电行为。

两份源码锁使用累计补丁 `patches/bootloader/0003-persistent-boot-logs.patch`。最终镜像、构建、QEMU 回归和写卡结果以 `docs/verification.json` 为索引；Pi 5 实机是否通过，必须由下一次上板单独记录。

最终构建：`artifacts/macos-arm64/20261025T151415Z-6iMOrQ`；日志回归：`artifacts/boot-log-tests/20261025T151649Z-oafknk4z`；启动回归：`artifacts/boot-diagnostics/20261025T151649Z-gpmlw8x2`。旧版反例在 `artifacts/boot-log-tests/20261025T150837Z-uyl76o1n/legacy-readback/report.json`。33 项宿主测试通过，其中嵌入 12 项 Rust 测试直接使用生产读取、跟踪和日志写入代码。

已使用 Raspberry Pi Imager 将该镜像写入原 64 GB SD（SM00000，序列号 `0x6dc506d8`），完成校验并自动弹出。写卡证据：`artifacts/hardware/pi5-v3-flash-20261025T152532Z-y880v27w`。随后已从失败启动后的实卡取回日志，验证了本轮检查点保存和读取能力；Pi 5 内核/用户态启动仍未通过。
