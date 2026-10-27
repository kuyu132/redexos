# QEMU ARM64 桌面输入与 libc 修复

2026-10-22，Apple Silicon macOS / QEMU virt / Cortex-A72 / TCG / 单核 / 4 GiB / ramfb 1024×768。复用先前的启动、Ion → Bash 信号修复和桌面镜像配置。应用来自官方 desktop build 562；内核、EFI、relibc 和 usbhidd 从锁定源码在本机编译。

## 问题与修复

### 编辑器启动崩溃和 TLS 布局

旧本地 libc 打开 COSMIC 编辑器触发 `RefCell already borrowed`，调用链进入 `pthread_setspecific`。不是通过关闭线程或吞掉 panic 绕过。ARM64 TLS 向上增长，而加载器混用了向下增长的偏移计算；同时 TLSDESC/TPOFF 缺少线程指针后的 16 字节 ABI 头。

补丁统一模块布局、复制位置、DTV 地址和总分配长度，补上重定位的 16 字节偏移。将 ABI 头放到 TLS 映射开头，使 TP 页对齐，避免将 TP 放在页边界前 16 字节后破坏高对齐 TLS。保留已验证的信号寄存器修复。

回归程序 `qemu/tls-isolation.c` 和独立 DSO `qemu/tls-module.c` 启动 8 个 pthread，分别检查已初始化/零初始化 TLS、pthread keys、TLSDESC、64 字节对齐及 5000 字节 TLS。对齐检查使用编译器屏障读取真实地址，防止声明上的 alignment 被常量折叠。旧布局在这一检查下失败；最终版本通过。

### 快速输入缺字、键释放延迟

USB PCAP 中观察到积压后 HID rollover 报告；仅修复定时器或仅删去 usbhidd 的等待循环仍会丢失长文本。

- ARM64 通用定时器原来固定 100 Hz，与调度器每 tick 2.25 ms 的记账不一致。现在按 `NANOS_PER_TICK` 计算重装值，恢复预期的抢占周期。
- usbhidd 对中断端点不再额外忙等/yield；控制传输轮询保留节流。
- HID idle 设为 0，只报告状态变化，减少重复报告。
- 当前 xhcid 会串行等待端点请求，因此 QEMU 键盘与指针分配到两个独立 xHCI 控制器。此配置与 HID 改动配套使用；没有声称已解决任意多设备共享控制器的问题。

桌面配置还保留了先前对 `30_console_switch` 的修改，避免启动时切换回文字控制台。

## 实际使用验证

通过 QMP 注入 USB 键盘和绝对坐标指针事件，操作真实 Orbital/COSMIC 窗口；没有直接向编辑器缓冲区或待验证文件写入内容。串口仅用于读取保存结果并独立比较。

组合修复的诊断镜像证据：`artifacts/qemu-arm64/20261022T114027Z-0193dimv/`。

1. 启动编辑器，输入 74 字节、包含大小写/数字/符号的两行脚本。按键事件间隔 25 ms（普通键每次按下/释放合计至少 50 ms，带 Shift 更慢）。
2. 保存为 `/home/user/editor-smoke.sh`，关闭编辑器，串口读回与预期逐字节相等。
3. 点击图形终端，普通 Ion 命令 `bash editor-smoke.sh` 输出预期文本及 `EDITOR_EXEC_OK`。
4. 在图形终端执行 `bash /usr/lib/qemu-smoke.sh`，文件/管道/子进程退出码/定时器/信号/TLS 全部 PASS，返回 Ion 提示符。
5. 从图形终端重新打开该文件，追加 `echo REOPEN_EDIT_SAVE_OK`，Ctrl+S 保存；99 字节文件与预期完全相等。

截图：`13-terminal-exec.png`、`14-terminal-smoke.png`、`15-reopened.png`、`16-edit-saved.png`；精确读回见 `editor-readback.txt` 和 `editor-reopened-readback.txt`。文件选择器首次加载较慢，需等窗口呈现；Save as 和已命名文件 Ctrl+S 都完成过实测。

最终 TP 对齐修正后，使用正式构建脚本重新生成镜像，并再次冷启动复测；最终证据：`artifacts/qemu-arm64/20261022T115400Z-w6qj5wb4/`，其中 `desktop-checks.json` 汇总结果，`07-terminal-smoke.png` 显示完整用户态测试通过，`10-terminal-final.png` 显示修改后脚本输出。两次编辑读回和最终终端输出均做精确比较。源码/镜像索引在 `verification.json` 的 `desktop` 项。

## 构建与复测

```bash
bash scripts/build-macos-components.sh qemu
bash scripts/build-macos-relibc.sh
python3 scripts/build-macos-usbhidd.py
python3 scripts/prepare-qemu-arm64.py --profile desktop
python3 scripts/run-qemu-arm64.py --desktop
```

源码与完整补丁由 `qemu/sources.lock.json` 指定，`0002` 补丁已包含旧 `0001` 的信号修复，不要叠加应用两份。`scripts/qemu_desktop_control.py` 提供可重放的 QMP 按键、鼠标及截图工具；设置 `QMP_SOCKET` 与绝对路径 `QMP_EVIDENCE`，运行器可用 `--qmp`、`--serial-socket`、`--usb-capture` 保存诊断证据。

TLS 失败对照：`work/debug-desktop/tls-before.log`、`tls-alignment-before.log`。输入失败对照：`artifacts/qemu-arm64/20261022T111913Z-f4nsgryt/`（旧输入路径）、`20261022T113858Z-3ro8cbeq/`（仅定时器修复）。这些是失败诊断记录，不属于最终通过结果。

## 验证边界

此结果覆盖 QEMU 单核软件渲染桌面的键鼠输入、终端与编辑器基本使用。未验证 Pi 4B/Pi 5 实机、SMP、HVF、GPU 加速、网络或持久存储。live 根文件系统与 QEMU snapshot 的写入仅在本次运行有效。短文本与本次输入速率通过不代表任意负载和输入速率都无丢键。桌面仍有上游 locale/font fallback、PTY ioctl 等日志提示，但本次测试操作没有因它们失败。
