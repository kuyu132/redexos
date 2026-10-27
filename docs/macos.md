# Apple Silicon Mac 编译

2026-10-22 已在本机 macOS arm64 实际完成交叉编译及链接：

| 产物 | 格式 | 状态 |
| --- | --- | --- |
| kernel.all | AArch64 ELF，带调试信息 | 已编译 |
| kernel.sym | 单独调试信息 | 已生成 |
| kernel | AArch64 ELF，移除调试信息 | 已生成 |
| bootloader.efi | AArch64 PE32+ EFI 应用 | 已编译 |
| bootloader-live.efi | AArch64 PE32+ EFI 应用，live feature | 已编译 |

使用 `sources.lock.json` 中的上游 commit 和 `nightly-2026-05-24`，没有修改内核或 bootloader 源码。编译保留上游默认 features，包括 kernel 的 multi_core；仍有上游警告，不是无警告构建。

```bash
# 在本工程根目录执行；本机已补装该工具链
rustup toolchain install nightly-2026-05-24 --profile minimal --component rust-src
bash scripts/build-macos-components.sh
```

还需要 Python 3、Git、Rustup 和 LLVM 的 `llvm-objcopy`。本机使用 `/opt/homebrew/opt/llvm/bin/llvm-objcopy`。脚本不改变 Rust 默认工具链，不安装 Podman，不写 SD 卡。

本次 Rustup 默认下载后端超时，改为一次性设置 `RUSTUP_USE_CURL=1` 后固定工具链安装成功；随后的 Rustup 自更新网络请求失败，不影响已装工具链及成功的编译结果。这个 curl 后端已被 Rustup 标记为 deprecated，不作为默认要求。

每次输出保存于 `artifacts/macos-arm64/<UTC时间>-<后缀>/`，含二进制、日志、工具链详情、源码 commit、diff、SHA256SUMS 和退出码。缓存位于 `work/macos-arm64`。运行目录中新生成的 SHA256SUMS 可用 `shasum -a 256 -c SHA256SUMS` 核验。

这验证了 Mac 可承担 ARM64 内核和 EFI 开发。后续已用独立 QEMU 版本锁完成内核到用户态测试，见 [QEMU ARM64 验证](qemu-arm64.md)。QEMU 使用官方预构建用户态，并不代表完整发行版已在 Mac 上从源码构建，也不代表 Pi 4B/Pi 5 板级启动通过。本机目前没有 Podman/Docker；完整系统构建继续使用已有 Ubuntu 构建机。
