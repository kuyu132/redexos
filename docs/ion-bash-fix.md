# Ion 普通启动 Bash：根因与回归

修复范围是 QEMU ARM64 中 Ion 的普通子进程启动路径。未修改 Ion 本身，也没有禁用信号或绕过 relibc 启动检查。

## 两个错误

### 内核过早保存 x0

relibc 的宿主检查以 `x0=-1` 调用 SYS_YIELD，预期返回 0。`sched_yield` 内部调用信号交付函数时，ARM64 的系统调用异常入口尚未把返回值写入 x0。信号上下文因此保存了输入值 -1，用户态信号 trampoline 随后恢复它，使启动检查触发 BRK。

诊断日志 `artifacts/qemu-arm64/20261022T092543Z-09z0f_3s/serial-clean.log` 中能看到同一次调用：

```text
[DEBUG-ion] signal ip=0x1a4388 saved_x0=0xffffffffffffffff inside_syscall=true
[DEBUG-ion] yield ret=0x0 return_ip=0xf6480
```

内核补丁在 ARM64 系统调用执行期间延迟普通信号交付，在 EL0 SVC 入口写回结果后重试。SIGKILL 检查仍保留在原位置，其他架构没有加入该延迟条件。诊断打印已从最终源码移除。

### relibc 的 x1/x2 栈顺序相反

内核修复后，重复启动暴露了动态链接器 memcpy 的地址/长度交换。信号 trampoline 从临时区取出原 x1/x2，按 x1、x2 顺序压栈，返回时却按 x2、x1 顺序出栈。

`qemu/signal-registers.c` 在异步 SIGUSR1 到来前保持不同的寄存器值。原库稳定输出：

```text
FAIL signal-registers x1=2222 x2=1111
```

relibc 补丁修正压栈顺序。使用官方镜像内嵌的 relibc commit 作为基线，从源码重新构建动态库，而不是修改 ELF 指令字节。修复后测试输出：

```text
PASS signal-registers x1=1111 x2=2222
```

## 回归入口

```bash
python3 scripts/run-qemu-arm64.py --probe-ion
python3 scripts/run-qemu-arm64.py --probe-signals
python3 scripts/run-qemu-arm64.py --verify --repeat 5
```

完整测试使用普通 `bash /usr/lib/qemu-smoke.sh`，覆盖文件、管道、子进程退出码、sleep、信号交付、寄存器保留，并确认返回 Ion 后仍能执行命令。仅打印测试成功但未返回 Ion 不算通过。

本机保留的失败对照，可用相同验证器复现（预期退出码 1，不会覆盖当前镜像）：

```bash
python3 scripts/run-qemu-arm64.py --probe-ion \
  --image-metadata work/debug-ion/baseline-image.json
python3 scripts/run-qemu-arm64.py --probe-signals \
  --image-metadata artifacts/qemu-arm64/20261022T093651Z-1tz962vc/inputs.json
```

正式源码补丁、SHA256、依赖版本均在 `qemu/sources.lock.json` 中。`scripts/source_patches.py` 的测试覆盖干净应用、重复应用、修改保护及补丁篡改拒绝。完整编译和启动记录见 `docs/verification.json`。

这类错误可以由两类测试提前发现：有待处理信号的首次系统调用，以及跨异步信号保存不同寄存器哨兵值。两项都已成为可直接运行的回归入口。当前证据覆盖 QEMU virt 单核；其他架构、实机、多核和完整 POSIX 信号语义尚未由这些测试验证。
