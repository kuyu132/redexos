# QEMU ARM64 持久存储

当前持久配置使用 4 GiB 可写 RedoxFS 系统磁盘，`/home/user` 中终端和编辑器保存的文件保留在宿主机 `root.img` 中。图形终端创建文件、COSMIC 编辑器 Save as 保存文件后，完整退出 QEMU，再由新的 QEMU 进程启动，分别进行精确读回和编辑器重新打开验证。证据索引见 `docs/verification.json` 的 `persistence`。

后续原生开发验证又修复了文件关闭后的映射缓存重用问题，更新了持久桌面的 RedoxFS 服务及 initfs，并保存完整磁盘副本。实际编译产物的双重启读回、修复及更新命令见 [开发工具链说明](qemu-development.md)。

## 日常启动

在工程根目录执行：

```bash
python3 scripts/run-qemu-arm64.py --desktop --persistent
```

持久桌面现已默认启用 E1000 用户态 NAT，DHCP/DNS 随启动恢复；联网下载和重启验证见 [网络说明](qemu-network.md)。

选择 **1024×768**，按 Enter。默认已关闭 live 模式，不要按 L 开启它。图形登录用户 `user`，密码为空。终端默认进入 `/home/user`。保存并关闭应用后，可退出 QEMU，再执行相同命令恢复文件。

当前数据目录：`work/qemu-arm64/persistent-desktop/`：

- `root.img`：4 GiB 可写 GPT/RedoxFS 系统磁盘，包含系统和用户文件，启动时重复使用。
- `boot.img`：只读启动镜像，包含普通 EFI、内核和加入 AHCI 驱动的 initfs。
- `image.json`：来源、版本、哈希、控制器与缓存配置。
- `resize.log`：GPT 分区和 RedoxFS 扩容记录，约 3.6 GiB 初始可用空间。

`work/qemu-arm64/persistent-desktop-image.json` 指向当前配置。准备脚本拒绝覆盖已有目录；运行器加独占锁，阻止两个实例同时使用同一块持久磁盘。QEMU 本身也启用磁盘写锁。每次运行在 `artifacts/qemu-arm64/` 保存命令、串口、镜像来源以及磁盘运行前后 SHA256。

原来的 `--desktop` 入口仍是内存 live/snapshot 配置，持久入口需要显式加 `--persistent`。

## 从源码准备新的持久桌面

```bash
bash scripts/build-macos-components.sh qemu
bash scripts/build-macos-relibc.sh
python3 scripts/build-macos-usbhidd.py
python3 scripts/build-macos-usbhidd.py --driver ahcid
python3 scripts/prepare-qemu-arm64.py --profile desktop
# 仅首次创建；已有数据目录时会拒绝覆盖
python3 scripts/prepare-qemu-persistent.py
python3 scripts/run-qemu-arm64.py --desktop --persistent
```

如需另建一套，指定尚不存在的 `--directory PATH`；这会建立新的磁盘和更新当前配置指针，原目录保留。持久磁盘备份应在 QEMU 停止后进行。

`prepare-qemu-persistent.py` 从经过哈希检查的桌面副本开始，将本机编译的 AHCI 驱动加入 initfs，并验证读回；把 GPT 内的 EFI 换成普通 EFI；复制出可写根磁盘，更新主/备 GPT 的分区边界和 CRC，再使用锁定 RedoxFS 源码的 resize 工具扩容文件系统。所有修改只作用于新创建的副本。

## AHCI 和中断修复

当前 ARM64 virtio-blk PCI 驱动依赖尚未实现的 MSI-X。持久配置改用现有 AHCI 驱动，并修复启动时实际暴露的问题：

1. GIC 初始化原来将全部中断标为已使用，用户态 AHCI 申请 SPI 返回 `File exists`。现在仅预留 SGI/PPI；内核注册处理器时继续保留它使用的 SPI，其他 SPI 供用户态驱动申请。
2. 固件没有初始化 AHCI 时，设备签名保持无效。驱动现在先配置临时 FIS 接收区，等初始设备到主机 FIS 后识别 SATA/ATAPI，再停止接收并切换到正式 DMA 区。QEMU 中设备签名的更新时机可从其 [AHCI 实现](https://raw.githubusercontent.com/qemu/qemu/v10.2.2/hw/ide/ahci.c) 核对。
3. 停止 AHCI 端口时先清 ST、等 CR，再清 FRE、等 FR，避免等待仍然启用的接收引擎停止。

普通 EFI 从只读 virtio MMIO 启动镜像加载内核/initfs；内核启动后通过 PCI AHCI 挂载可写根目录。根磁盘未启用 snapshot，使用 `cache=writethrough`，QEMU 完成写请求前同步到宿主机。只读启动镜像与系统磁盘的文件系统 UUID 一致，启动器按 UUID 交接。

完整补丁：`patches/kernel/0003-aarch64-signals-timer-and-device-irqs.patch` 与 `patches/base/0002-usbhidd-and-ahci-cold-start.patch`，均继承前一阶段的信号、调度和 USB 输入修复。当前版本由 `qemu/sources.lock.json` 固定。

## 验收与回归

实际 GUI 验收使用真实 USB 键鼠事件：终端创建 `terminal-persist.txt`（42 字节），编辑器保存 `editor-persist.txt`（87 字节）。先精确比较运行中读回；完全退出 QEMU 后，宿主机直接读取根磁盘再比较；新进程启动后再读回并打开编辑器。首次写入证据：`artifacts/qemu-arm64/20261022T131636Z-gkuhppue/`，含 `04-terminal-written.png`、`07-editor-written.png` 和退出后的离线读回。

重启证据：`artifacts/qemu-arm64/20261022T132134Z-8wg_33t9/`，含 `04-terminal-readback.png`、`05-editor-reopened.png`、精确读回文本和 `persistence-checks.json`。

还提供可自动重复的持久化回归：

```bash
# 先关闭使用同一块磁盘的 QEMU
python3 scripts/verify-qemu-persistence.py
```

它使用两个独立 QEMU 进程，第一轮在 root 家目录写入随机标记，完全退出后第二轮精确读回，再清理本次测试标记。已有用户文件不参与写入。该脚本的结果保存在 `artifacts/qemu-persistence/`；GUI 编辑器使用验证另由截图和精确文件比较覆盖。本次自动回归通过记录：`artifacts/qemu-persistence/20261022T132605Z-2ixg4pkd/`，含两轮读回、完整用户态测试和 `result.json`。

`tests/test_persistent_disk.py` 检查扩容后的主/备 GPT CRC、原有分区数据保留、坏 GPT 拒绝写入，以及 initfs 添加驱动后 bootstrap/原始载荷保持不变。

## 范围

已覆盖单核 TCG、软件显示下的正常文件保存与完整 QEMU 退出/启动恢复；未验证宿主机断电、任意中途强杀时的事务恢复，也未验证 Pi 4B/Pi 5 硬件。持久模式的内核和 initfs 来自只读 `boot.img`；如果以后升级这些启动组件，需要另外更新启动镜像，单独修改根磁盘上的副本不会改变引导载荷。
