# 双平台移植与验收

代码分层遵循 ARM64 公共层 → 通用驱动 → 板级集成。当前工程已把 QEMU 验证的公共 ARM64 补丁接入两块板的 recipe，并增加 Pi 5 `/soc@...` 设备内存登记修复；后续 Pi 专用驱动改动也应进入对应上游仓库并形成可复现补丁或固定 commit。

## 最终目标与推进顺序

最终交付是可以用于日常开发和工作的图形桌面系统。串口登录、显示启动画面或单次打开窗口都只是中间结果，不能作为项目完成的依据。

截至 2026-10-26，QEMU ARM64 已验证桌面输入、终端和编辑器、AHCI 持久化、DHCP/DNS/HTTPS，以及原生 Rust 小程序编译和 GDB 调试，并有重启读回记录。它们属于固定 QEMU 配置的功能证据，不代表两款树莓派或全部日常应用已通过。

当前已有 Pi 5 实机停在 Redox EFI 的文件系统打开附近，尚未确认进入内核。下一阶段优先按 [分层定位方法](bringup-methodology.md)拆分固件 I/O、日志和 RAM 文件系统实验；Pi 4B 维护构建与离线检查，取得实机后独立验收。公共代码持续执行 QEMU 回归，两款板均需独立通过最终验收。基础显示先行，GPU 加速是否成为阻塞项由实际桌面及应用性能决定。

## 阶段与通过条件

| 阶段 | 4B 工作 | Pi 5 工作 | 验收证据 |
| --- | --- | --- | --- |
| M0 基线 | 完成 Ubuntu 完整编译、固件/DTB 固定 | 独立构建、选择并固定启动固件 | 构建命令、锁文件、退出码、产物校验值 |
| M1a | 固件→EFI→内核→UART→异常/MMU/GIC/定时器→用户态 | 同时推进固件→EFI→早期 UART10 | 全程串口日志，真实 DTB、内存布局与版本 |
| M1b | 多次冷启动，用户态交互、调度稳定性 | 完成中断/定时器/用户态闭环 | 两款分别至少 5 次冷启动；最小用户进程成功运行并退出 |
| M2 | BCM2711 SD 控制器与块设备 | BCM2712 SDHCI 与块设备 | 挂载、读写校验、超时恢复、重启后数据一致性 |
| M3 | GENET | PCIe/RP1/MMIO/中断/DMA→GEM | 原始帧、地址配置、连接与持续传输日志 |
| M4 | USB、显示与输入 | RP1 USB、显示与输入 | 按实际用途拆分；基础显示与 GPU 加速独立验收 |
| M5 | 桌面会话与应用集成 | 复用桌面配置并验证板级差异 | 图形登录、键鼠、窗口操作、终端和编辑器实测 |
| M6 | 日常开发与工作验收 | 相同工作流独立验收 | 持久项目、联网获取依赖、编辑/构建/运行/调试、长时间使用记录 |

M1a 是工作推进顺序，M1 的最终验收要求两款都进入用户态。QEMU 当前证据使用单 CPU；实机首个内核基线也应明确限制与记录 CPU 启动策略，不能因 QEMU 的单 CPU 配置推定板上已关闭多核。多核启动作为独立后续验证。

## 桌面与开发工作验收

以下是完整验收标准；部分 QEMU 功能已有记录，但整套标准和两款实机验收尚未完成。测试需要记录镜像、应用版本、机器配置、操作步骤和结果；QEMU 与两款实机分别登记。

| 能力 | 通过条件 |
| --- | --- |
| 图形启动 | 冷启动进入可交互桌面；连续 5 次成功，保存启动日志及桌面截图 |
| 显示和输入 | 记录并实测目标分辨率；键盘、鼠标、窗口移动/切换/重绘可用，无持续花屏或输入失效 |
| 文件持久化 | 在图形编辑器中创建、修改和保存项目；正常关机并冷启动后内容校验一致，实际使用持久磁盘而非内存盘 |
| 网络 | DNS、HTTPS、Git 获取代码及项目依赖下载实测成功；记录网络中断后恢复行为 |
| 开发闭环 | 在系统内完成一个代表性项目的编辑、构建、运行、测试和调试；记录工具链、命令、退出码及产物 |
| 日常工作 | 按用户实际应用清单逐项测试浏览网页、阅读文档及其他工作任务；中文显示和输入按实际需求验收 |
| 稳定性与体验 | 初始门槛为连续 4 小时代表性工作负载，无内核崩溃、桌面卡死或文件损坏；记录资源使用、操作响应和构建耗时，再按实际使用调整指标 |
| 安装与恢复 | 提供可重复的安装/启动步骤、数据备份与恢复办法，并实际验证恢复流程 |

开发语言、必需 IDE/编辑器、浏览器及办公应用的具体清单尚未确定。在需求明确前，先以图形终端、代码编辑器和一个小型项目验证基础链路；不得据此宣称全部日常工作需求已满足。无法运行的必需应用必须明确登记为缺口，远程工具如作为替代方案，需要单独说明并得到用户接受。

## 当前功能矩阵

截至 2026-10-26。所有项目必须按环境、镜像和固件组合分别登记。状态只用 `not-tested` / `failed` / `passed`，失败需要日志和复现步骤，通过需要证据目录；一次观察到的子阶段通过不等于整体或重复性通过。历史执行详情保存在 [verification.json](verification.json)，本表汇总当前已知边界。

| 项目 | Pi 4B | Pi 5 |
| --- | --- | --- |
| 固件 DTB 离线合同（非实机） | passed | passed |
| MBR/FAT32 写卡镜像生成、回读及 QEMU 启动 | passed | passed |
| Ubuntu 完整载荷构建 | not-tested | not-tested |
| 实机固件装载 Redox EFI | not-tested | passed（v3 日志/照片） |
| 固件读取 live 文件、首个 RedoxFS 头校验 | not-tested | passed（v3；非全部文件系统校验） |
| 实机 EFI 完成文件系统打开并交接内核 | not-tested | failed（v3 停顿，根因未定） |
| v4 诊断版实机启动 | not-tested | not-tested（已写卡校验，无启动结果） |
| 内核串口输出与异常输出 | not-tested | not-tested |
| MMU、异常、中断、定时器、调度 | not-tested | not-tested |
| 用户进程启动/系统调用/退出 | not-tested | not-tested |
| 内存盘用户态文件访问 | not-tested | not-tested |
| 冷启动重复性 | not-tested | not-tested |
| Redox 运行时 SD 读写 | not-tested | not-tested |
| 有线网络 | not-tested | not-tested |
| Redox 运行时 USB / 基础显示 / 多核 | not-tested | not-tested |

Pi 5 的 `passed` 子阶段来自 [实卡观察](../artifacts/hardware/pi5-v3-read2-20261025T153456Z-uk_q1b87/observation.json)和 [BOOT0001.LOG](../artifacts/hardware/pi5-v3-read2-20261025T153456Z-uk_q1b87/collected/BOOT0001.LOG)；v4 写卡状态来自 [写后校验](../artifacts/hardware/pi5-v4-flash-20261025T235725Z-1eujixga/result.json)。UEFI 的 HDMI 文字输出不算 Redox 显示驱动通过。

| QEMU 已有功能证据 | 状态与范围 | 记录 |
| --- | --- | --- |
| EFI→内核→用户态 | passed，含 A72/EL1 与 A76/EL2 诊断回归 | [v4 启动与错误路径结果](../artifacts/boot-diagnostics/20261025T235505Z-40e79mrx/result.json) |
| 桌面、输入、终端与编辑器 | passed，单 CPU、ramfb、指定 USB 配置 | [桌面记录](desktop-fix.md) |
| 保存文件并重启读回 | passed，AHCI 持久磁盘 | [持久存储记录](qemu-persistence.md) |
| DHCP、DNS、HTTPS 与重启恢复 | passed，E1000、IPv4 用户态 NAT | [网络记录](qemu-network.md) |
| 原生编译、运行、断点/单步/调用栈、重启后继续 | passed，小型无外部依赖 Rust 程序、单线程子进程调试 | [原生调试记录](qemu-native-debugging.md) |
| SMP、GPU 加速、全部日常应用与完整长时验收 | not-tested | 不由上述功能测试推定通过 |

各项通过绑定所链接记录中的具体构建与配置；并不表示每次新引导诊断构建都重跑过完整桌面套件。

固件从 SD 读内核不等于 Redox 的 SD 驱动正常。内存盘运行不等于持久存储完成。QEMU virt 通过不等于任何一块板通过。

离线 DTB 合同只证明锁定的固件 DTB 候选包含预期 PL011、GIC 和定时器资源，并且地址转换结果符合板级约定。Pi 5 已观察到 DeviceTree 交接，但还需要保存并逐项核对实机最终 DTB、内存图与固件状态；不能以离线通过代替硬件资源验证。

## 实机记录模板

每次测试创建独立目录，至少保存：

```text
board/model/revision/RAM:
power_supply:
serial_adapter/wiring/baud:
EEPROM_version:
firmware_version_and_hash:
U-Boot_or_UEFI_version_and_hash:
DTB_and_overlays_hash:
payload_SHA256SUMS:
kernel/bootloader/base/relibc_commits:
kernel_features_and_CPU_count:
serial.log:
cold_boot_trial: 1/5
user_program/command/output/exit_status:
result: not-tested | failed | passed
failure_step_and_reproduction:
```

进入用户态的证据应包含最小程序自身的输出、至少一次系统调用结果及退出/返回 shell 的记录。下一步可先在串口 shell 执行 `cat /etc/port-target` 和进程/文件操作，之后增加独立的系统调用 smoke 程序。不要只按某个内核日志字符串自动宣告用户态通过。
