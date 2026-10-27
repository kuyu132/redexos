# QEMU ARM64 来宾原生 GDB

本工程的原生调试范围是 QEMU `virt`、Cortex-A72、GICv2、单核的持久桌面。GNU GDB 运行在 Redox 来宾里，通过来宾内核的 ptrace 能力调试自己启动的、同身份的单线程子进程。正常使用无需开放 QEMU 的宿主调试端口。

## 来宾终端使用

```bash
python3 scripts/run-qemu-arm64.py --desktop --persistent
```

选择 1024×768，以 `user`、空密码登录。在桌面终端执行：

```text
cd ~/dev-demo
make clean
make run
gdb ./hello
(gdb) break dev_double
(gdb) run
(gdb) print value
(gdb) info registers x0 pc sp
(gdb) next
(gdb) bt
(gdb) continue
(gdb) quit
```

已有项目源码保持 `22 → 44`，编译使用来宾的 `dev-rustc` 和 LLD。调试信息、源码、Makefile、GDB 及私有链接 SDK 都保存在可写 AHCI `root.img`。批处理脚本在 `/usr/lib/devkit/native-debug.gdb`：

```bash
cd ~/dev-demo
gdb -q -nx --batch -x /usr/lib/devkit/native-debug.gdb ./hello
```

## 修改内容

- 内核新增完整 ARM64 通用寄存器接口，包含 X0–X30、SP、PC 和 NZCV；校验 PC/SP 范围、PC 对齐，并保留特权级和中断屏蔽位。
- BRK 与软件单步异常生成调试暂停。`MDSCR_EL1.SS` 在返回用户态时依据目标栈帧配置，避免单步状态泄漏到调试器和其他进程。
- 调试能力由进程管理器授权给同 UID/GID 的直接子进程，限制为单线程；能力传递后禁止提升身份。内存读写借用子进程地址空间，临时修改写保护后恢复。
- ptrace 的公开返回值改为 64 位 `long`，修复 PEEKDATA 截断，并补全 C++ 头文件链接声明。未知请求返回错误，不再触发 ARM64 未实现 panic。
- 调试暂停与进程退出都通过进程管理器的 `waitpid` 报告。修复丢弃未请求的继续/停止状态后没有继续查询或登记等待者的问题，避免后续断点和退出永久挂起。
- 修复 libc 无缓冲输入的零长度读取，逐字节读取不再提前返回 EOF；GDB 脚本使用这一输入路径。GNU libiconv 提供完整的字符集转换。
- AArch64 信号返回改由内核从用户信号帧原子恢复 X0–X30、SP、PC 和 NZCV。旧跳板借用 `x18` 跳回被中断位置，但 Rust Redox 目标会把 `x18` 分配给普通值，异步信号可能破坏动态加载器的 ELF/重定位状态；新路径不要求应用或预编译 Rust 标准库采用特殊寄存器选项。
- 修复同步调用直接切换到服务线程后，服务线程睡眠时丢失定时器登记的问题；`tcdrain()` 所触发的 PTY 服务睡眠不再永久挂起。
- exec 交接后把 saved UID/GID 更新为有效身份，避免普通用户的新程序继续继承 saved root 身份；调试授权仍检查全部身份。GDB 启动失败时回收子进程并清除 inferior，避免恢复执行的清理路径解引用空目标。
- GDB 新增 AArch64 Redox 原生子进程后端，并在 exec 交接处暂停。使用 GNU 的 `--disable-threading` 同步符号处理配置；Python、Guile、TUI 和 debuginfod 未启用。
- 自动化串口客户端在新连接完全静默时重发唤醒回车，覆盖 PL011 丢失首字节的情况；收到 getty 或 shell 的任何输出后不再注入额外字符。桌面输入先分别验证终端焦点、Bash 和工作目录，所有标记在每轮开始前删除。

GDB 未启用 Python，Rust 的 Python pretty-printer 自动加载提示不影响本例通过 DWARF 读取整数参数、源码行和调用栈。

这些是当前 QEMU 移植补丁，不代表上游 Redox 或树莓派硬件已具备同等支持。浮点/SIMD 寄存器、硬件监视点、多线程目标、任意 PID attach、SMP 尚未验收；不支持的请求明确返回错误。

## 构建与安装

源码提交、补丁和 GMP/MPFR/libiconv/交叉 SDK 校验值记录在 `qemu/sources.lock.json`。GNU GDB 源码来自锁定的 Redox binutils-gdb 分支；不是包装宿主调试器的替代命令。

```bash
bash scripts/build-macos-components.sh qemu
bash scripts/build-macos-relibc.sh
python3 scripts/build-macos-native-debug.py --libc artifacts/macos-relibc/具体构建目录
```

构建器使用 macOS Clang/LLD 和锁定的 ARM64 目标头文件、libstdc++、GMP、MPFR、libiconv；不执行 Linux 交叉工具链中的宿主二进制。GDB、bootstrap、回归程序、源码补丁及校验表写入新的 `artifacts/macos-native-debug/` 目录。

完全关闭 QEMU 后安装匹配的三组构建产物：

```bash
python3 scripts/prepare-qemu-native-debug.py \
  --debug artifacts/macos-native-debug/具体构建目录 \
  --kernel artifacts/macos-arm64/具体构建目录 \
  --libc artifacts/macos-relibc/具体构建目录
```

安装器检查补丁和哈希，取得磁盘排他锁，在 `work/qemu-arm64/native-debug-backups/` 备份完整磁盘和配置。它重新封装 bootstrap 并逐项比较 initfs 中其余文件和目录，安装后逐文件读回，不创建或覆盖用户项目源码。

## 回归与证据

```bash
# 来宾中
wait-continued
ptrace-smoke
stdio-unbuffered
tty-drain
exec-saved-ids

# QEMU 关闭后，宿主中
python3 scripts/verify-qemu-native-debug.py
```

`wait-continued` 用管道阻止子进程提前退出：先停止和继续子进程，再以 `WNOHANG` 忽略继续状态，最后收集退出码 7。旧版进程管理器在这一步确定性挂起；独立磁盘副本中的负对照保存在 `work/native-debug/negative-wait/result.json`。

`ptrace-smoke` 检查子进程权限、64 位内存读写、父进程写隔离、BRK、通用寄存器修改、真实硬件单步和退出状态。它不使用 QEMU GDB stub，也没有用于掩盖等待问题的固定延时。

`exec-saved-ids` 先构造有效身份为普通用户、保存身份为 root 的状态，再 exec 自身，检查保存身份已降为普通用户。旧进程管理器会保留保存的 root 身份并返回测试失败。

`tty-drain` 连续三次调用终端排空，覆盖同步服务调用中的睡眠唤醒；旧内核会永久等待。

信号烟雾测试把 `x1`、`x2` 和 `x18` 保持为不同哨兵，异步接收信号后检查三者全部恢复。自动验证每次冷启动还执行 65 次不同命令长度的 Bash 加载；修复前的动态加载器压力复现通常在第 48 次前后因活跃 `x18` 被信号返回覆盖而异常退出。

自动验证启动两个独立 QEMU 进程，分别运行持久程序、来宾原生重新编译、GDB 源码断点/变量/单步/调用栈/退出，以及 ptrace、stdio、终端和等待回归。每轮还通过 QMP 键鼠操作，以普通 `user` 在真实桌面终端重新编译并交互调试；先故意使用不存在的工作目录验证启动失败能报错并恢复，再启动正常程序。GDB 在断点处把 `value=22` 导出为 8 字节文件，单步后再导出 `$pc`，验证器比较两者的 SHA256；因此旧标记或只执行 GDB `shell` 命令不能构成通过条件。断点暂停期间另运行来宾命令，确认只暂停目标子进程。证据包含桌面截图、批处理 GDB 文本、输入握手记录和变量/PC 文件哈希。关闭每个 QEMU 后离线比较源码和 ELF 字节，并检查调试节；首轮还运行信号、TLS、文件系统和普通用户态烟雾测试。

2026-10-24 的最终证据目录为 `artifacts/qemu-native-debug/20261024T040117Z-v6fqu8rt`，两次实际启动分别记录在 `artifacts/qemu-arm64/20261024T040119Z-fe_nxvyc` 和 `artifacts/qemu-arm64/20261024T040736Z-0zj13anc`。两轮均执行 65 次 Bash 加载器压力；参数文件哈希为 `9bb57922dc1a9aa3c4a6dcb92467700e2d5c80ff5906c5a3e3487c0321c50e6a`，单步 PC 为 `0x50000368`，对应文件哈希为 `696f1c94e7d2ca99411ec53e505a5f9f17f1141f8c313f762450a6d550d03868`。源码和最终 ELF 在两轮及关机离线读回中分别保持 `9acae58489a3fc2eddfd69bf30b6a9580fd376145c7ce180a6598455ef3e0a6b` 与 `4174bd24919b305a07c8c6571dd8b2197e9e4f9a8052a89db3e5c38418a39d3d`。结构化结果索引在 `docs/verification.json`。
