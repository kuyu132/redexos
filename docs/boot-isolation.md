# Pi 5 启动隔离测试

2026-10-26。独立 EFI 探针、宿主 RedoxFS 探针及自动验证器已实现。**这些是定位工具；尚无 Pi 5 隔离测试结果，根因仍未确定。** 用户已确认此前写入的 v4 尚未上板测试。本轮没有刷写 SD 卡，也没有发布新的修复镜像。

## 实现与边界

`upstream/bootloader/src/isolation/` 增加两个显式启用的 binary，默认引导构建不启用 `isolation-probes`：

- `boot-isolation.efi`：在 UEFI 内执行一个配置指定的实验，最后停留在 HDMI/固件控制台，不退出 Boot Services、不进入内核。
- `boot-isolation-host`：在宿主机上运行相同 RAM 适配器和生产 RedoxFS 打开算法。

EFI 直接复用当前 `boot_log.rs`、`boot_log_buffer.rs`、`live_read.rs`、`fs_trace.rs`，并使用原引导器 `Cargo.lock` 中的 RedoxFS 0.8.0。日志开/关使用同一份 EFI，唯一配置差异为 `fat_log`。探针没有修改 CPU 控制寄存器、异常向量或栈边界，没有加入任何推测性修复。

RAM 适配器只持有内存字节，不持有固件文件句柄，并拒绝所有文件系统写入。输入文件在加载完成后显式关闭；`fat_log=off` 时，RAM 打开阶段不进行 FAT 文件 I/O。UEFI 控制台仍由固件实现，因此它并非完全独立的调试通道。

FAT 测试使用独立文件 `redexos/isolation/<id>.BIN`，每条 2,048 字节，逐条通过生产写入器完成 Write/Flush，记录实际返回状态和写入数量。正常完成后显式检查 Close，再重新打开并校验全部字节。已有同名文件会拒绝写入，保留前次证据。

探针的栈布局和调用上下文与完整引导器不同。探针全部通过仍不能排除完整引导中的栈/布局/时序问题；宿主通过也不能排除 ARM64 特有问题。当前 GPT 判断沿用生产引导器的 2 MiB 文件系统偏移约定，不是通用 GPT 验证器。

## 固定输入与构建

当前实验使用从失败实卡读回的 `redox-live.iso`，536,870,912 字节，SHA256：

```text
74ac5914789d54fee84ca25b5d2e6b79e437bb7b85767985854eb6e3a9e35c88
```

构建入口：

```bash
python3 scripts/build-boot-isolation.py
```

最终测试构建目录：`artifacts/boot-isolation-build/20261026T023110Z-xeyec4pl/`。包含 EFI、宿主程序、匹配的 PDB/汇编、实际源码、依赖锁、补丁、构建命令与各文件 SHA256。累计补丁为 `patches/bootloader/0005-isolation-probes.patch`，已接入两个源码锁；旧 v4 镜像和失败分区备份保留。

## 配置与实机执行

程序从其所在 FAT 分区读取 `redexos/ISOLATE.CFG`，最多 256 字节。不存在、未知字段、重复字段或非法组合都报错停止，不会默认开始写入。配置样例：

```text
ISOLATE1
id=ram-ab
mode=ramfs
fat_log=off
preload=live
records=1
```

构建目录 `profiles/` 中已有六个配置。每次仅把一个配置复制为 `redexos/ISOLATE.CFG`：

| 顺序 / 配置 | 操作 | 通过证据 |
| --- | --- | --- |
| `ram-off.cfg` | 加载原 512 MiB 文件，关闭 FAT 日志，打开 RAM RedoxFS | `FS-READY` 和 `ISO-RESULT id=ram-ab PASS` |
| `ram-on.cfg` | 同一个 EFI、同一载荷，开启原有 FAT 日志路径 | 同样完成，并有新的 `BOOTnnnn.LOG`，含 `ISO-COMPLETE` |
| 再次 `ram-off.cfg` | A/B/A 的最后一轮 | 再次完成；保留失败与成功的全部结果 |
| `fat-small-write.cfg` | 不加载 live 文件、不打开 RedoxFS；写 16 条记录、Flush、关闭、读回 | `ISO-VERIFY bytes=32768 records=16 exact=true` 和 PASS |
| `fat-small-read.cfg` | 断电后重新启动，仅检查上一轮的文件 | 同一 32 KiB 文件校验通过；先写成功才能做此项 |
| `fat-loaded-write.cfg` | 加载并保留原 512 MiB RAM 数据，不打开 RedoxFS，写 300 条记录并校验 | `ISO-VERIFY bytes=614400 records=300 exact=true` 和 PASS |
| `fat-loaded-read.cfg` | 再次启动，加载相同载荷，再检查原文件 | 614,400 字节校验通过 |

实机准备：先保留 v4 的启动观察和 SD 现场；将测试 EFI 作为额外文件 `EFI/BOOT/ISOLATE.EFI` 放入测试介质，保留原 `BOOTAA64.EFI`、`RPI_EFI.fd` 和 live 文件。通过固件的文件启动入口选择 `ISOLATE.EFI`。若所用固件没有可用的文件启动入口，需另外准备可恢复的测试启动方式，不能把它记为探针失败。本轮尚未在卡上执行这些文件操作。

每次修改配置后记录其字节/哈希、EFI 哈希、板卡与固件信息并完整断电启动。A/B/A 的严谨比较应从同一介质快照分别恢复，仅改变 `fat_log`，避免前一轮留下的 FAT 分配和日志数量成为额外变量。现场尚无自动供电控制，不能宣称无人值守实机测试已完成。

结果以本轮屏幕/串口为准，`ISO-HALT` 表示测试结束后主动停留，不是启动卡死。若停在 `ISO-HALT` 之前，记录最后的 `ISO-CALL`、`ISO-IO`、`FS-READ` 或 `SD-IO` 和等待时间，归档文件后再恢复介质。无法返回的固件调用没有内部强制中断；宿主自动测试有外部超时，实机需外部观察。

显式要求 FAT 日志时，日志无法创建或写入失败会使实验失败，不会以屏幕输出成功代替落盘成功。探针的 FAT 日志沿用 v4 logger 的 BEGIN 标记，但实验身份由 `BOOT-ISOLATION v1` 和 `ISO-CASE` 识别；没有 `END UEFI` 或内核日志是预期行为。

## 宿主与 QEMU 自动验证

```bash
python3 scripts/verify-boot-isolation.py \
  --build-manifest artifacts/boot-isolation-build/20261026T023110Z-xeyec4pl/manifest.json \
  --live-image artifacts/hardware/pi5-v3-read2-20261025T153456Z-uk_q1b87/files/redox-live.iso \
  --live-sha256 74ac5914789d54fee84ca25b5d2e6b79e437bb7b85767985854eb6e3a9e35c88
```

验证器创建临时 FAT 测试介质，使用 QEMU 自己的 EDK2，不使用 Pi 5 固件，不访问物理设备。每个运行保存完整命令、原始日志和结果；部分用例保存 HDMI 控制台截图。数据写入后强制结束 QEMU，再启动新的进程验证读回。该过程测试 QEMU 后端上的 Flush 行为，不能代替真实 SD 控制器断电试验。

最终结果目录：`artifacts/boot-isolation-tests/20261026T023120Z-348e00ut/`。

覆盖内容：

- 宿主与 QEMU A72/EL1、A76/EL2 打开同一 RAM 文件系统；比较读取次数和最终选中的头版本。
- A/B/A 日志对照；关闭日志时比较整个 FAT 测试介质的前后 SHA256，开启时提取真实新日志并检查完成记录。
- 小负载与保留 512 MiB 内存后的 FAT Write/Flush/Close/Read，离线逐字节校验和新进程重启读回。
- 已有文件拒绝覆盖、读回数据损坏、只读介质写入失败、显式日志不可用、禁止递归 FAT 日志的配置检查。
- 截断镜像在宿主和 QEMU 中返回 EIO；超时或进程异常退出不算成功的负对照。

首个物理文件头的 generation 是 8960；生产算法遍历头环后，宿主和 QEMU 实际选择 generation 9154，总共读取 261 次。两者不是同一检查点，不能据此判为头损坏。

正常引导入口另有 QEMU 用户态回归：`artifacts/boot-isolation-tests/20261026T022625Z-k1dojxxs/production-boot-regression/`。当前主程序执行逻辑没有改动，日志模块仅增加探针使用的状态查询与关闭入口；重新编译的 `.text` 与 v4 构建相同，整个 PE 文件因构建元数据不同而哈希不同，不能声称整个文件逐字节相同。

## 从返回的 SD 取证

先按 [v3 日志取回方法](boot-logs-v3.md)取得只读 FAT 分区副本。已有日志继续用 `collect-boot-logs.py` 提取；独立 FAT 数据用：

```bash
python3 scripts/collect-boot-isolation.py \
  --image /绝对路径/boot-partition.cdr --offset 0 \
  --id fat-small --records 16 --output artifacts/hardware/本轮新目录
```

大负载改为 `--id fat-loaded --records 300`；整个当前 SD 镜像的 FAT 偏移为 `1048576`，单独 FAT 分区备份偏移为 `0`。已挂载分区可用 `--volume /Volumes/实际卷名`。输出必须是新的、位于源介质外的目录。

提取器保存原字节、实际/期望 SHA256 和比较结果，不修改源文件。离线字节匹配只证明读回内容正确，不能单独证明实机重启或进入内核。`fat_log=off` 的 RAM 测试没有新 FAT 日志，需要保留屏幕或串口结果。

## 如何据结果决定修复

| 实机结果 | 下一步 |
| --- | --- |
| RAM 关日志通过，开日志失败，关日志再次通过 | 聚焦日志调用、资源和时序；结合独立 FAT 测试进一步区分，尚不能直接认定固件 bug |
| 独立 FAT 测试也失败 | 以最小 EFI 的调用、返回码、偏移和固件版本形成复现，核查固件/控制器路径 |
| 独立 FAT 通过，RAM 关日志失败 | 检查 RAM 访问、RedoxFS 处理、栈/ABI/内存属性；取得故障 PC 或异常现场 |
| 探针全部通过，完整引导仍失败 | 比较完整引导的栈、内存布局、句柄生命周期和初始化顺序；不能宣告已修复 |

取得实机结果后才选定最小修复，并在原失败组合及受影响的回归上验证，随后制作修复镜像。
