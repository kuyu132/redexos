# Pi 5 文件系统启动诊断 v2

v1 的实机照片显示：ACPI 和 Device Tree 模式下都完成了 512 MiB 的 `redox-live.iso` 读取，最后可见行均为 `[FS-OPEN] source=redox-live.iso block=0x200`。Device Tree 设置已经正确；这说明只切换模式没有绕过该问题。仍不能据此认定文件系统损坏、CPU 异常或分配器故障中的任何一种原因。

v2 增加首个文件头的读取和校验探针、RedoxFS 打开期间的每次磁盘接口调用跟踪，以及 CPU 控制寄存器、栈指针、内存镜像地址。元数据读取在此使用内存镜像。固件控制寄存器只读取，不修改。探针仍调用原来的 RedoxFS 实现选择最新文件头并重建分配器，没有跳过校验。

同时将内存磁盘的块地址乘法、范围计算和切片访问改为检查后读取。越界会输出 `[BOOT-ERROR] FS-RANGE` 并返回 I/O 错误；这已在测试中验证，但尚未证明与实机停顿有关。

## 镜像与操作

- 原始镜像：`artifacts/sd-image/20261025T144229Z-pi5-ktg1uajg/redox-pi5-sd.img`，768 MiB。
- SHA256：`ee5b029b18a230cb0f1af07af00671e26be906b039050b18f353602629e42097`。
- 独立 EFI：`artifacts/macos-arm64/20261025T143939Z-xV3LSk/bootloader-live.efi`。
- EFI SHA256：`7d21ec8a0098d59f5ac78c8cafb9cdc9aae35c4849ad24ca2d54f8fab5ebd2e8`。

本镜像从 v1 的校验副本生成，只替换 EFI、首次启动说明和镜像元数据。内核、用户态、板级固件及整个 `redox-live.iso` 保持字节一致；live SHA256 仍为 `74ac5914789d54fee84ca25b5d2e6b79e437bb7b85767985854eb6e3a9e35c88`。生成后逐文件回读了全部已登记 FAT 文件。

通过 Raspberry Pi Imager 写入并完成校验。上板后保持 `System Table Mode = Device Tree`，以启动行 `Hardware descriptor: DeviceTree(...)` 为准。新版本标记应为 `[BOOT-DIAG v2]`。如果再次停住，拍摄最后几行及上方的 `[FS-CPU]`；记录是否有编号继续增加。遇到分辨率选择时按 Enter 继续。

| 标记 | 含义 |
| --- | --- |
| `[FS-CPU]` | 当前 EL、栈地址、SCTLR 和对齐检查位、CPTR_EL2 或 CPACR_EL1；供比较，不自动判错 |
| `[FS-RAM]` | 已读入内存的镜像起始地址和长度 |
| `[FS-PROBE] Initializing...` | 准备首个文件头缓冲区 |
| `[FS-PROBE] Reading...` | 准备读取指定块；后续由 FS-READ 标记具体调用 |
| `[FS-HASH] Checking...` | 即将验证首个文件头的签名、版本和校验和 |
| `[FS-HASH] valid=...` | 首个文件头结果；仅证明这个文件头，不证明整个文件系统完整 |
| `[FS-CALL]` | 进入原有 RedoxFS 打开函数，选择文件头环并重建分配器 |
| `[FS-READ] #N ... pending` | 第 N 次磁盘接口读取即将执行；此处 file 后端复制内存 |
| `[FS-READ] #N ... processing` | 第 N 次读取已经返回；之后可能在校验、解析元数据或处理分配器 |
| `[FS-READY]` | RedoxFS 打开函数已返回成功，随后应进入 BOOT-01 |
| `[BOOT-ERROR] FS-RANGE` | 镜像块地址越界或算术溢出，已拒绝该读取 |

这些标记会缩小下一次故障定位范围，不能直接解释 CPU 异常或区分每一个库内部操作。固件调用不返回时仍没有强制超时能力；离开 UEFI 后的内核故障也不保证显示在 HDMI。

## 已完成验证

证据：`artifacts/boot-diagnostics/20261025T144248Z-el_ri2uf`。

- 最终 SD 镜像在 QEMU Cortex-A72 / EL1 下启动到用户态，512 次文件读取及 261 次文件系统探针/元数据读取均有配对的前后标记。
- 同镜像在 QEMU Cortex-A76 / EL2 下启动到用户态，文件、管道、普通 Bash 子进程、定时器、信号及 TLS smoke 通过。
- 实际 EFI 的五个异常镜像用例通过：缺失文件、过短文件、无效文件头、校验和损坏、GPT 指向镜像外。
- 30 项宿主测试通过，其中 8 个嵌入的 Rust 测试直接覆盖生产读取和跟踪代码。
- QEMU GOP 截图已人工检查：可见文件头 `valid=true` 以及第 16 次元数据读取的 `pending` 标记。

![QEMU 文件系统打开诊断](../artifacts/boot-diagnostics/20261025T144248Z-el_ri2uf/gop-fs-progress.png)

v1 的 Device Tree 实机照片位于 `artifacts/hardware/pi5-v1-dtb-fs-open-20261025T144343Z-cua_7pk_`。后续 v2 实机照片已确认首个文件头校验通过，最后可见第 259 次元数据读取返回；记录位于 `artifacts/hardware/pi5-v2-read259-20261025T151150Z-iwxxudy0`。实机根因未确定，不能将 QEMU 通过记为 Pi 5 启动修复。后续 [v3](boot-logs-v3.md) 增加了 FAT 日志保存与电脑端提取。

## 重建与回归

```bash
bash scripts/build-macos-components.sh qemu
# 将 --efi 替换为刚生成的构建目录；保持原始 v1 文件系统用于比较
python3 scripts/update-sd-bootloader.py \
  --base-manifest artifacts/sd-image/20261025T135813Z-pi5-cc2f5425/manifest.json \
  --efi artifacts/macos-arm64/20261025T143939Z-xV3LSk/bootloader-live.efi
python3 scripts/verify-boot-diagnostics.py \
  --image-manifest artifacts/sd-image/20261025T144229Z-pi5-ktg1uajg/manifest.json
python3 -m unittest discover -s tests -v
```

累计源码补丁为 `patches/bootloader/0002-filesystem-open-diagnostics.patch`，已锁入基础和 QEMU 两份源码锁，并更新两款板的构建配方。旧镜像、源码补丁和证据保留，可回退对比。写卡、串口采集和现场恢复见 [实机测试手册](hardware-testing.md)。
