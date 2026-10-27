# QEMU ARM64 网络与重启恢复

2026-10-22，复用现有 4 GiB 持久桌面磁盘，通过 E1000 和 QEMU 用户态 NAT 完成 DHCP、DNS、HTTPS 下载及退出后重新启动恢复验证。无需重新创建磁盘或安装网卡驱动；使用官方桌面镜像中已有的 `e1000d`，以及此前修复的 GIC 外设中断支持。

## 使用

```bash
python3 scripts/run-qemu-arm64.py --desktop --persistent
```

持久桌面默认启用网络。选择 1024×768，用户 `user`，密码为空。网络随系统启动自动配置：

| 配置 | 值 |
| --- | --- |
| 网卡 | E1000，MAC `52:54:00:12:34:56` |
| 网络 | QEMU user NAT，IPv4 |
| 地址 | DHCP `10.0.2.15/24` |
| 默认网关 | `10.0.2.2` |
| DNS | `10.0.2.3`，QEMU 转发给宿主机解析器 |

来宾中的启动服务按设备发现、网络栈、DHCP 顺序完成配置。验证过程中没有手工运行 dhcpd、写静态 IP 或修改 DNS。

图形终端可检查：

```bash
cat /scheme/netcfg/ifaces/eth0/addr/list
cat /scheme/netcfg/route/list
cat /scheme/netcfg/resolv/nameserver
curl -fL https://static.redox-os.org/img/aarch64/SHA256SUM -o ~/Downloads/redox-SHA256SUM
sha256sum ~/Downloads/redox-SHA256SUM
```

该网址的校验清单可能随官方发布更新。自动回归使用下面固定提交的 Cargo.lock，进行稳定的字节数和 SHA256 比较。

`--no-network` 可显式关闭网络；其他原有非持久配置默认保持无网络，需要时可用 `--network` 启用。当前验证范围是持久桌面配置。用户态 NAT 不需要 TAP 或管理员权限，未配置入站端口转发。

## 实际验证

自动回归命令：

```bash
# 先关闭正在使用同一块持久磁盘的 QEMU
python3 scripts/verify-qemu-network.py
```

本次通过证据：`artifacts/qemu-network/20261022T141956Z-gu67ngzm/`。测试启动两个独立 QEMU 进程，两轮分别验证：

1. 启动后读取地址、路由和 DNS 配置，检查 DHCP 地址及默认网关。
2. 在网络 PCAP 中检查同一客户端、同一事务的 DHCP Discover、Offer、Request、Ack，并检查 Ack 中的地址、网关、DNS。
3. 捕获 `gitlab.redox-os.org` 的 DNS A 请求及配对的成功响应。
4. 用来宾 curl 下载 [固定版本 Cargo.lock](https://gitlab.redox-os.org/redox-os/base/-/raw/06bd7b3ffb16c547f9cc545d16d39b325e3f67d4/Cargo.lock)。关闭 curl 代理使用，保留 TLS 证书校验；两轮均得到成功响应和证书校验成功记录。
5. 下载长度均为 **71,193 字节**，SHA256 均为 `d998d1ee7048243693019dbd905ad3ae31e5323a8c465d6ccc7ad1bbd542fc1b`，与本地锁定提交的 Git 对象内容一致。
6. 第一轮完全退出 QEMU，第二轮启动后先检查旧下载仍在，再执行新的域名解析和下载。通过后清理本次自动回归的两个临时下载。

证据包含 `config-1/2.txt`、`curl-1/2.txt`、`download-1/2.txt`、`packets-1/2.json`、`previous-download-after-restart.txt` 及 `result.json`。两次启动的完整命令、串口和 `network.pcap` 分别在：

- `artifacts/qemu-arm64/20261022T141957Z-95fumila/`
- `artifacts/qemu-arm64/20261022T142013Z-3n4y9qxq/`

额外验证了官方 `SHA256SUM` 的 HTTPS 下载，其 715 字节内容与同一时段宿主机下载结果一致。初次诊断下载保留在来宾 `/home/user/network-Cargo.lock`（71,193 字节）和 `/home/user/network-download.txt`（715 字节），可直接查看。

原有 `/home/user/terminal-persist.txt` 和 `/home/user/editor-persist.txt` 与启用网络前的磁盘副本逐字节相同。12 项工程测试与 Python/Shell 语法检查通过。

## 实现与范围

`run-qemu-arm64.py` 为持久桌面默认添加 user 网络后端和 E1000 PCI 设备，记录实际配置。`--network-capture` 在本次运行的证据目录保存网络 PCAP。`network_capture.py` 读取实际 Ethernet/IPv4/UDP/DHCP/DNS 数据并验证协议交换；`verify-qemu-network.py` 管理两次完整启动、下载校验和清理。

此次没有修改内核、网卡驱动、initfs、只读启动镜像或重建根磁盘。E1000 驱动来自已固定的官方 desktop build 562；二进制 SHA256 为 `623c4f768ad64ad01dbccc8073736797dc93a908042c56c136803c3d6c8e95cc`。

通过范围是当前宿主机网络环境下的 IPv4 用户态 NAT、DHCP/DNS、HTTPS 下载和正常 QEMU 退出后恢复。IPv6、桥接、入站服务和树莓派实机网络尚未纳入验证。持久存储的使用和备份范围见 [持久存储说明](qemu-persistence.md)。
