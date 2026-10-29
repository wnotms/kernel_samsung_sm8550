# GitHub Actions 手动编译

打开仓库 **Actions → Build Samsung SM8550 kernel (stock identity) → Run workflow**，选择分支和配置后运行。首次使用 fork 的 Actions 时，先在 Actions 页面启用工作流。

## 可选项目

| 输入 | 作用 |
| --- | --- |
| `mode` | `lkm` 不内置 KernelSU；`resukisu` 内置 ReSukiSU。`lkm` 名称沿用原构建脚本，不表示此工作流会额外编译 KernelSU `.ko`。 |
| `package_zip` | 同时生成 AnyKernel3 刷机包。 |
| `droidspaces` | 开启 SYSVIPC、POSIX_MQUEUE、IPC_NS、PID_NS、devtmpfs 及相关容器依赖；取消时关闭前五项，保留 Android 原有 UTS/网络命名空间。 |
| `ntsync` | 开启或关闭 NTSync。 |
| `bbr` | 勾选启用 BBR 并设为默认；取消关闭 BBR，默认使用 CUBIC。 |
| `zram_lz4` | 勾选默认 LZ4；取消改用 lzo-rle，保留 ZRAM。 |
| `susfs` | 仅在 `resukisu` 模式开启 SUSFS。 |
| `zeromount` | 仅在 `resukisu` 且 SUSFS 开启时生效。 |
| `rekernel` | 开启或关闭 Re:Kernel；开启时使用 Generic Netlink。 |
| `extra_config` | 分号或换行分隔的额外 Kconfig 设置，覆盖上面的普通功能选项。 |

额外配置示例：

```text
CONFIG_USER_NS=y;CONFIG_IKCONFIG=y;CONFIG_IKCONFIG_PROC=y
```

支持 `y`、`m`、`n`、整数、十六进制和简单双引号字符串，不执行输入中的 shell 命令。选择 `m` 只修改配置：此流程仅构建 `Image`，不构建/安装模块。需要的功能请优先选择 `y`。

配置会在原脚本的默认设置之后应用，再运行 `olddefconfig`。依赖导致请求的配置无法生效时，工作流会报出具体项目并停止。KSU/SUSFS/ZeroMount、版本字符串、SMP/PREEMPT、ThinLTO/CFI 使用专用控制或固定值，不允许通过额外配置覆盖。不存在的配置无法开启。

## 原 zImage 的版本信息

已从上传的 **45,816,320 字节 ARM64 Image** 提取并保存到 `scripts/build/reference-zimage.json`；虽然原文件名为 zImage，它实际是未压缩的 ARM64 Image。CI 无需重新上传原镜像。

| 项目 | 固定值 |
| --- | --- |
| 内核 release | `5.15.153-android13-8-2370170-abX710ZCU5CYH4` |
| 编译者 | `ms@DESKTOP-EAS21AS` |
| 构建编号 | `#1` |
| 构建时间 | `2025-08-20 08:06:12 UTC` |
| 编译器 | Android clang 14.0.7，`r450784e` / build 8508608，LLD 14.0.7 |
| UTS version | `#1 SMP PREEMPT 2025-08-20 08:06:12 UTC` |

工作流使用仓库原有的 Samsung `clang-r450784e` 工具链和 ThinLTO + CFI。只通过构建参数固定显示的 release，不修改顶层 Makefile 的源码版本，不伪造编译器标识；最终会检查 `kernel.release` 和 Image 中完整的 `Linux version ...` 横幅与原镜像完全相同。编译器标识或构建身份不一致会使校验失败。

**实际源码仍是仓库的 Linux 5.15.220，不会降级为 5.15.153。** `LINUX_VERSION_CODE` 等内部源码版本也保持原值。相同的 `uname -r` 与时间不代表二进制相同，也不保证原厂 vendor 模块的符号 CRC/KMI 兼容或真机启动成功。`build-metadata.json` 会记录实际源码提交。

## 下载产物

完成后进入该次运行页面，在 **Artifacts** 下载 `SM8550-<mode>-<run_number>`，其中包含 `Image`、最终 `.config`、请求的 `selected.config`、`kernel.release`、`compile.h`、`build-metadata.json`、可用的符号文件，以及勾选打包时生成的 `SM8550_*.zip`。

Image 是裸内核，不是完整 `boot.img`。AnyKernel3 保持原脚本的三星 SM8550 设备检查。失败时会另存构建日志与配置。

上游原有的三星安全栈处理、Baseband-guard 等其他默认行为继续由 `scripts/build/apply_features.sh` 管理。本工作流没有把所有现有补丁都做成独立开关。
