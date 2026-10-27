# QEMU ARM64 原生开发验证

当前持久桌面安装了官方 ARM64 Redox 的 Rust、Cargo、rustfmt、LLVM 21 和 GNU Make。验证项目使用 Rust；在 COSMIC 编辑器输入源码，在 COSMIC 终端以 `user` 原生编译、运行。当前已支持 **来宾内 GNU GDB/ptrace 原生调试**，详见 [原生调试说明](qemu-native-debugging.md)。宿主 LLDB 流程保留为可选诊断方式。

## 日常使用

```bash
python3 scripts/run-qemu-arm64.py --desktop --persistent
```

选择 1024×768，以 `user` 登录，密码为空。在桌面终端执行：

```bash
cd ~/dev-demo
cosmic-edit hello.rs
make run
```

当前源码中的 `input` 是 `22`，预期输出为 `DEV_RESULT=44`。最初在编辑器输入的版本是 `21 → 42`；之后实际编辑为 `22 → 44`，保存、重新编译和冷启动读回均纳入验证。项目在 `/home/user/dev-demo`，工具在 `/usr`，都位于已有的 `root.img`。

新项目可复制 `/usr/lib/devkit/Makefile`，或执行 `dev-rustc hello.rs -o hello`。`dev-rustc` 使用编译器自带的 LLD，链接与当前 TLS、信号返回修复相同版本的私有静态 libc SDK，启用调试信息和帧指针。SDK 放在 `/usr/lib/devkit`；原生调试安装器同时安装与内核匹配的系统动态 libc。

已观测版本：rustc `1.98.0-dev`、Cargo `1.98.0-dev (8b241a6b6 2026-06-01)`、rustfmt `1.9.0-dev`、GNU Make `4.4`。版本、可执行文件哈希及软件包头记录见 `qemu/development/packages-record.json`。它是安装记录，官方软件仓库会更新，不是完整的软件包版本锁。

## 重建工具环境

当前磁盘已经配置完成，无需重新安装。以下步骤供新建持久桌面时使用。

先在来宾 root 终端安装官方原生包并创建 SDK 目录：

```bash
pkg --curl --yes install rust gnu-make
mkdir -p /usr/lib/devkit
```

使用 curl 后端保留 HTTPS 校验。然后完全关闭 QEMU，在宿主工程目录执行：

```bash
python3 scripts/build-macos-usbhidd.py --driver redoxfs
python3 scripts/update-qemu-filesystem.py
python3 scripts/prepare-qemu-development.py
```

文件系统更新器检查源码补丁和组件哈希，在 `work/qemu-arm64/filesystem-backups/` 保存完整磁盘、启动镜像及配置副本，然后更新 initfs 中的 `/bin/redoxfs`、系统服务和缓存回归程序。SDK 安装器检查当前 libc 与构建产物匹配、验证私有库和逐文件读回；两个安装器均持有持久磁盘锁，必须在 QEMU 关闭后执行。

这些命令不会创建用户项目源码。模板在 `qemu/development/hello.rs` 和 `Makefile`，可按需在来宾编辑器中创建自己的项目。当前流程只实测了无第三方依赖的小型 Rust 程序；Cargo 版本命令已验证，第三方 Cargo 项目、C/C++ 编译器和大型构建尚未验收。

## 来宾原生断点调试

在来宾终端执行：

```text
cd ~/dev-demo
gdb ./hello
(gdb) break dev_double
(gdb) run
(gdb) print value
(gdb) next
(gdb) bt
(gdb) continue
(gdb) quit
```

构建、安装和两个冷启动中的桌面终端验收见 [原生调试说明](qemu-native-debugging.md)。

## 可选：宿主 LLDB 诊断

先关闭 QEMU，从停止的磁盘导出与来宾逐字节相同的程序和源码：

```bash
mkdir -p work/dev-demo-source
tools/redox-image/target/release/redox-image cat work/qemu-arm64/persistent-desktop/root.img /home/user/dev-demo/hello > work/dev-demo-hello
tools/redox-image/target/release/redox-image cat work/qemu-arm64/persistent-desktop/root.img /home/user/dev-demo/hello.rs > work/dev-demo-source/hello.rs
python3 scripts/run-qemu-arm64.py --desktop --persistent --gdb-port 12345
```

登录并打开来宾终端，再从 macOS 的另一个终端运行：

```text
lldb --no-lldbinit work/dev-demo-hello
settings set target.source-map /home/user/dev-demo /绝对工程路径/work/dev-demo-source
gdb-remote 127.0.0.1:12345
target modules load --file dev-demo-hello --slide 0
breakpoint set -H -n dev_double
continue
```

然后在来宾终端执行 `cd ~/dev-demo; ./hello`。命中后在 LLDB 执行：

```text
frame variable value
register read x0 pc sp
thread step-over
register read x0
bt
process detach
quit
```

GDB stub 只监听宿主机回环地址。它调试整个虚拟机，暂停时桌面也暂停；必须继续或 detach 才会恢复。Redox 不向该 stub 提供用户进程模块信息，因此必须显式设置 ELF 的零装载偏移。包装器把 `.text` 固定在 `0x50000000`，方便隔离当前小程序的断点。不要同时运行多个使用相同地址的测试程序。

已实测源码断点、整数参数、返回寄存器、源码单步及调用栈。Apple LLDB 提示缺少 Rust 语言插件，部分局部变量和 Rust 表达式无法求值；不能将其等同于完整的 Rust 调试器。来宾内原生 GDB 的验收范围与限制单独记录在原生调试说明中。

## 文件系统修复与回归

开发验证暴露出 RedoxFS 的关闭映射缓存问题：最后一个描述符/映射释放后，缓存仍以可复用的 node ID 保留；后续覆盖或创建文件可能再次取得旧页。出现过中间目标文件格式异常、ELF 调试节为空，甚至长度为零的文件运行了旧代码。只检查本次程序能运行，无法排除这种错误。

锁定补丁 `patches/redoxfs/0001-release-closed-mapping-cache.patch` 在最后一个引用释放后移除映射缓存，并释放文件系统服务持有的虚拟映射。静态早期服务的 env_logger TLS 缓冲区另有兼容性异常，Redox 版本改用直接输出的简单日志器，仍保留 Info/Warn/Error 日志。

`qemu/development/fs-cache-reuse.c` 重复执行共享映射、关闭、覆盖、再次映射和删除。在旧文件系统的独立磁盘副本上，第 0 轮即返回 `FAIL fs-cache-reuse`（退出 10）。修复后该测试与真实工具链冷启动回归共同执行。LLD 包装器保留 `--no-mmap-output-file`；曾用于诊断的编译后复制处理已经移除。

关闭交互 QEMU 后执行：

```bash
python3 scripts/verify-qemu-development.py
# 若有意修改了示例的预期结果，可指定 --expected 整数
```

该命令启动两个独立 QEMU 进程。每轮先运行上次留下的程序，然后原生 `make clean && make run`；关闭 QEMU 后离线比较源码和 ELF 的 SHA256，并检查 ARM64 ELF 的 `.text`、`.symtab`、`.debug_info` 和 `.debug_line`。另执行缓存重用测试与信号/TLS 等用户态回归。它不创建或改写源码，重建的程序交还 `user` 所有。

原始编辑器、LLDB 和读回证据主目录为 `artifacts/qemu-development/20261023T132723Z/`。来宾原生 GNU GDB 的两次冷启动最终证据为 `artifacts/qemu-native-debug/20261024T040117Z-v6fqu8rt/`，其中包含构建输出、批处理 GDB 文本、普通用户终端截图、断点参数和单步 PC 哈希，以及每次关机后的源码/ELF。最终索引见 `docs/verification.json` 的 `development.debug` 项。
