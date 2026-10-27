# Pi 5 屏幕启动诊断 v1

本文保留 v1 的构建和观察记录。当前应使用 [v2 镜像与说明](boot-diagnostics-v2.md)。

本版用于在没有 UART 适配器时，通过 HDMI 确认引导程序具体停在哪一步。实机上一版照片显示 `DeviceTree(...)` 后停留在 `Looking for RedoxFS:`；该提示覆盖文件打开、内存分配和镜像读取，单凭照片无法判断根因。另一次 ACPI 模式照片显示已找到 509 MiB RedoxFS，但这不代表内核启动成功。

## v1 首次实机反馈

2026-10-25 新版实机照片确认 `[BOOT-DIAG v1]`，运行在 EL2，打印 `[SD-DONE] 512/512 MiB (100%) bytes=536870912`，随后停在 `[FS-OPEN] source=redox-live.iso block=0x200`。用户确认超过一分钟没有继续输出。照片和记录保存在 `artifacts/hardware/pi5-v1-fs-open-20261025T141713Z-qdo028ui`。

因此已确认固件文件读取调用返回了完整长度；尚未确认 RedoxFS 打开成功，也没有内核启动证据。文件系统在此通过内存缓冲区读取元数据。完整读取不等于已验证内存中的内容正确，根因仍待确定。

该次照片中的硬件描述是 `Acpi(37bd0000, 24)`，下一次测试应先在 UEFI 中选择 Device Tree，并以启动行确实出现 `DeviceTree(...)` 为准。设置入口是 `Device Manager → Raspberry Pi Configuration → ACPI / Device Tree → System Table Mode`，见 [固件说明](https://github.com/worproject/rpi5-uefi#linux)。按界面提示保存并重启。不能将 ACPI 模式直接认定为本次文件系统停顿的原因。

为检查 EL2 条件，使用同一镜像在 QEMU `virt,gic-version=2,acpi=off,virtualization=on`、Cortex-A76 下复测，日志确认进入 EL2，RedoxFS 打开及用户态 smoke 均通过。证据为 `artifacts/boot-diagnostics/20261025T141643Z-el2-a76-zc9wtmb1`。这没有复现实机停顿；QEMU virt 的固件、内存及外设环境仍与 Pi 5 不同。

## 镜像与上板操作

- 镜像：`artifacts/sd-image/20261025T135813Z-pi5-cc2f5425/redox-pi5-sd.img`（768 MiB）。
- SHA256：`dea09ce337442a00953983e8cc1c40f0423310f2f3964c5877556c9098827fd2`。
- 独立引导程序：`artifacts/macos-arm64/20261025T135635Z-08UNHq/bootloader-live.efi`。
- 该 EFI SHA256：`04ad94b102296836e97243e878575ad383144f1b514e9458c41713d372abe400`。

使用 Raspberry Pi Imager 写入 `.img` 并完成写后校验。连接 HDMI、USB 键盘；保持 UEFI 的 `System Table Mode = Device Tree`。加载后应出现 `[BOOT-DIAG v1]`，随后显示读取进度。遇到分辨率选择提示时按 Enter。

如果进度停住，拍摄最后几行，记录等待时间、最后的 `offset` 和是否曾继续变化。若看不到 `[BOOT-DIAG v1]`，先检查是否启动了新镜像。错误行后的 panic 文字可以一起拍下。

## 屏幕标记

| 最后标记 | 当前操作或可确认的状态 |
| --- | --- |
| `[SD-01]` / `[SD-02]` | 打开 UEFI 启动文件系统 / 根目录 |
| `[SD-03]` | 打开 `redox-live.iso` |
| `[SD-04]` | 取得文件长度；本镜像应为 536870912 字节 |
| `[SD-ALLOC]` | 分配并清零 512 MiB 镜像缓冲区 |
| `[SD-READ]` | 显示已读字节对应的 MiB/百分比；`offset` 是接下来要读取的位置 |
| `[SD-DONE]` | 已完成文件读取，打印实际字节数 |
| `[FS-OPEN]` | 正在校验/打开 RedoxFS |
| `[BOOT-01]` | RedoxFS 已打开 |
| `[BOOT-02]` | 准备选择显示模式，按 Enter 继续 |
| `[BOOT-03]` | 复制 live 根文件系统到内存 |
| `[BOOT-04]` / `[BOOT-05]` | 加载内核 / initfs |
| `[BOOT-06]` / `[BOOT-07]` | 设置帧缓冲 / 建立页表 |
| `[BOOT-08]` | 即将离开 UEFI 并交接内核；本行不代表交接成功 |
| `[BOOT-ERROR]` | UEFI 状态或文件错误，保留其后的阶段、偏移和状态码 |
| `[SD-MISSING]` | 缺少 live 文件，继续原有的裸 RedoxFS 分区扫描路径 |

读取由原来的 8 KiB 循环改为预分配缓冲区、最多 1 MiB 一次；每次调用固件读取**之前**刷新进度。支持短读，拒绝文件提前结束或非法返回长度。这个改变减少调用次数，但尚未测量 Pi 5 上的性能收益，也未证明解决了实机卡住问题。

如果固件读取一直不返回，屏幕会保留待完成偏移；本版没有中止固件调用的超时能力。诊断使用 UEFI 控制台；离开 UEFI 后，内核的显示和异常输出属于下一阶段，不能承诺所有内核故障都能留在 HDMI 上。照片也不替代完整串口日志或内存转储。

## 验证与重建

证据目录：`artifacts/boot-diagnostics/20261025T140119Z-nr2fpj5_`。

- 最终整张 SD 镜像通过 QEMU EFI → 内核 → init → Ion → 普通 Bash 启动/返回及用户态 smoke；连续 512 个读取偏移无遗漏，完成字节数正确。
- 使用镜像内实际 EFI 启动三个异常 FAT 测试盘：缺失 live 文件、仅 128 字节的文件、4096 字节的无效文件头，均输出对应错误，未进入 `[BOOT-01]`。
- 使用 QEMU ramfb 和限速读盘，在实际 GOP 控制台截取进度，确认文字可见。截图为模拟器证据，不是 Pi 5 实机结果。
- 29 个宿主测试通过，其中读取模块测试实际编译并运行 5 个 Rust 测试，覆盖短读、提前 EOF、固件错误、非法长度、分配失败和进度顺序。

![QEMU GOP 中可见的读取进度](../artifacts/boot-diagnostics/20261025T140119Z-nr2fpj5_/gop-read-progress.png)

```bash
python3 -m unittest discover -s tests -v
PYTHONPATH=scripts python3 artifacts/boot-diagnostics/20261025T140119Z-nr2fpj5_/verify-boot-diagnostics.py \
  --image-manifest artifacts/sd-image/20261025T135813Z-pi5-cc2f5425/manifest.json
```

脚本将日志、命令、结果和 GOP 截图保存到独立证据目录。`gop.visual_review` 默认是 `pending`，需要打开截图确认文字；自动生成截图本身不等于人工可读性检查。

源码补丁为 `patches/bootloader/0001-live-read-diagnostics.patch`，已纳入基础与 QEMU 两份源码锁；两款板的 Ubuntu recipe 和 macOS 构建都会应用。Pi 4B 的已有成品镜像没有重打包；下一次重建才包含本版诊断。源码新增文件的补丁重入测试也已补齐。

```bash
bash scripts/build-macos-components.sh qemu
# 用刚生成目录中的 bootloader-live.efi 替换下行 EFI 路径
python3 scripts/prepare-sd-image.py pi5 \
  --live work/qemu-arm64/image-v3db6jvh/redox-live.iso \
  --efi artifacts/macos-arm64/20261025T135635Z-08UNHq/bootloader-live.efi
```

这版继续使用现有 live 根文件系统；Pi 5 运行时 SD 持久存储、网络及完整内核启动仍需实机验证。日志和现场恢复流程见 [实机测试手册](hardware-testing.md)。
