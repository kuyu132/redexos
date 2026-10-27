# Ubuntu 构建与证据采集

当前用户选择自行在 Ubuntu 执行脚本；没有进行远程连接。下面命令均在复制后的工程根目录执行。

## 1. 安装和准备

```bash
bash scripts/ubuntu-deps.sh
python3 scripts/prepare.py prepare pi4b
python3 scripts/prepare.py prepare pi5
python3 scripts/prepare-board-dtb.py pi4b
python3 scripts/prepare-board-dtb.py pi5
```

依赖脚本适用于 Ubuntu，安装 Podman、rootless 所需工具、FUSE、QEMU AArch64、设备树工具。`podman info` 失败时先处理其实际报错，例如 subordinate UID/GID 或用户 namespace 配置，不要以 sudo 运行构建来绕过问题。`/dev/fuse` 缺失时，需由机器管理员启用 FUSE。

源码下载失败后，脚本不会自动删除半成品目录。若 `work/<板名>/redox` 尚无有效 HEAD，确认没有需要保留的文件后，将该目录改名为备份再重试。

设备树命令下载 `sources.lock.json` 中锁定的 Raspberry Pi 固件 DTB，并核对 SHA256。Pi 4B 还会应用锁定的 `disable-bt.dtbo`。输出目录内的 `verification.json` 记录最终串口节点、物理地址、IRQ、GIC 和 DTB 哈希；`offline_contract_passed` 不能替代实机启动。完整约定见 [板级早期启动基线](board-early-boot.md)。

## 2. 构建实验性载荷

```bash
bash scripts/build-ubuntu.sh pi4b --dry-run
bash scripts/build-ubuntu.sh pi4b
bash scripts/build-ubuntu.sh pi5
```

两款都调用同一套上游 AArch64 内核及 bootloader 构建流程，配置分别设置 `BOARD=pi4b` / `BOARD=pi5`，并使用独立 `CONFIG_NAME`。准备脚本还会把锁定的 kernel、base、relibc 和 RedoxFS 补丁复制到相应 core recipe。板名和补丁接入仍不会自动提供 Pi 专用 SD、网络、USB 或显示驱动，不能用生成配置作为硬件适配完成的证据。

初期继承上游 minimal 软件包集合，使用 live 模式降低对运行时 SD 驱动的依赖。它仍包含默认服务及网络配置，并非已精简完成的串口专用系统。单核基线也尚未强制实现：上游 kernel 默认启用 `multi_core`，后续需要显式调整 Cargo features / 启动策略后重新验证，不能只依靠板名实现单核。

失败时查看对应目录的 `build.log` 和 `exit-code.txt`，不要忽略退出码。已成功构建的缓存可在下一次运行复用。脚本不会运行 `make clean` 或清空 Podman。

每次正式构建会先在运行目录生成 `board-contract/`，并在开始耗时系统构建前验证设备树合同。系统构建成功后，脚本继续生成 `sd-image/`：其中包含板级 UEFI、FAT32 启动分区、Redox EFI/live 载荷、串口 overlay、压缩镜像和完整 manifest。最终 UEFI 仍必须在 Device Tree 模式下把 DTB 放入 `DEVICE_TREE_GUID` 配置表。

## 3. 在上板前检查

```bash
# 替换 RUN 为实际运行目录
RUN=artifacts/pi4b/实际运行目录
(cd "$RUN/payload" && sha256sum -c SHA256SUMS)
file "$RUN/payload/EFI/BOOT/BOOTAA64.EFI"
```

确认是 AArch64 EFI 应用。之后单独完成固件实验：在 UEFI 中装载 `EFI/BOOT/BOOTAA64.EFI`，让 bootloader 从该 EFI 文件所在卷的根目录读取 `redox-live.iso`。这里描述的是已审阅的 bootloader 文件查找约定；具体固件是否正确提供文件系统、DTB、串口和内存映射仍需验证。

Pi 4B 镜像采用 PFTF UEFI，Pi 5 采用独立的 WoR UEFI，不能共用固件二进制。工程提供的 `board-config/<板名>/config.txt` 是串口/DTB 参考合同；实际镜像以各自 UEFI 发布包的 `config.txt` 为基础，再追加 Redox 串口 overlay。

当前镜像生成路径已固定为 Pi 4B PFTF UEFI v1.52 和 Pi 5 WoR UEFI v0.3，版本与归档 SHA256 位于 `board-firmware.lock.json`。Pi 5 固件上游已归档，只能作为带明确版本的实机实验依赖。写卡使用 `scripts/flash-sd.sh`，它会保护根磁盘、要求确认并执行完整写后回读；详细操作见 [实机测试手册](hardware-testing.md)。

如果先用 QEMU `virt` 检查 ARM64 公共路径，其结果只能记为 QEMU 通过。上游对未知 BOARD 的 QEMU 路径会落到 `virt`，不能将设置 `BOARD=pi5` 后启动 QEMU 解释为 Pi 5 仿真成功。

## 4. 采集串口

先确认板上的实际接口：4B 需要正确配置 GPIO14/15 对应的 PL011；Pi 5 使用专用调试接口。波特率按本轮约定为 115200、8N1，固件与工具应一致。使用匹配电平的串口适配器、共地，不接 RS-232 电平。

```bash
python3 scripts/collect-serial.py pi4b /dev/serial/by-id/实际设备 artifacts/serial-pi4b-001
python3 scripts/collect-serial.py pi5 /dev/serial/by-id/实际设备 artifacts/serial-pi5-001
```

输出目录必须不存在；开始采集后再冷启动，Ctrl-C 停止。工具保存原始字节到 `serial.log`，采集时间和设备到 `capture.json`。它不发送串口命令、不自动认定启动成功。若无串口访问权限，按 Ubuntu 的设备组权限配置处理，然后重新登录。

手动补充板卡型号/内存/修订、供电、串口接线、EEPROM/固件/UEFI 版本、镜像 SHA256、交接 DTB SHA256，参照验收计划登记。不要将“没有看到 panic”视为进入用户态。

## 5. 下一轮需要提供的结果

首先提供 4B 构建运行目录中的退出码和日志末段。若成功，再提供载荷校验值和固件实验记录。Pi 5 采用相同证据格式。不要上传 SSH 密钥或其他凭据；脚本不需要这些内容。
