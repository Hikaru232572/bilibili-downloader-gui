# 第三方来源、许可与对应源码

Bili v1.1.0-rc.1 按根 LICENSE 的 GPL-3.0 组合发行，保留原项目 MIT 声明；各第三方组件仍遵循自己的许可。版权与原声明在 licenses/，Python/原生组件版本和工具哈希在 licenses/dependency-manifest.json。

| 组件 | 固定来源 | 许可/说明 |
|---|---|---|
| 原项目 | Lani27/bilibili-downloader-gui | 原 MIT 声明完整保留 |
| F2 | Johnserf-Seed/f2 f6be8c0ffba9a127075bbeafe4838716650b6325 | Apache-2.0，当前抖音导入闭包；未改 Python 源内容，裁剪 CLI/无关平台，调整包装/配置 |
| XHS-Downloader | JoeanAmier/XHS-Downloader 3261312721f0b37c705ba6515885bc7f34349f2f | GPL-3.0，Converter/Namespace/Image 子集，相对导入和纯 URL shim 变更 |
| yt-dlp | 2026.08.19，官方 Windows EXE | 上游源码/构建资料及原许可保留；独立工具未经修改 |
| aria2 | 1.37.0，官方 Windows x64 build1 | GPL-2.0-or-later，完整源码附件；二进制未经修改 |
| FFmpeg/FFprobe | N-126734-ga9cbcc2bbb-20260921 | version3 LGPL shared 构建，未启用 GPL/nonfree；原 DLL 名称保留，源码/配置/构建脚本与依赖修订附件 |
| Pillow/pillow-heif | 12.3.0 / 1.5.0 | Python 原许可与 bundled notices；实际 libheif 1.23.1、libde265 1.1.1、x265 e444744 源码随附件提供 |
| Python/Tcl/Tk | Python 3.12.10 / Tcl/Tk 8.6 | 原声明与第三方条款保留 |
| 其他 Python/native 组件 | dependency-manifest.json | 原声明与对应 sdist/源码附件；包含 PyInstaller 的 bootloader 例外声明 |

本软件使用 FFmpeg 的 LGPL 构建。对应源码和构建信息在同一 Release 的 Bili-v1.1.0-rc.1-third-party-sources.zip，可与二进制一起免费下载：
https://github.com/Hikaru232572/bilibili-downloader-gui/releases/tag/v1.1.0-rc.1

应用和解析子集的完整对应源码在本仓库 v1.1.0-rc.1 标签，以及 Release 的 Bili-v1.1.0-rc.1-source.zip。必要编译/打包步骤在 BUILDING.md 和脚本中。固定工具包另附 Release，用于复现相同工具字节。

第三方源码附件包括固定 FFmpeg 核心、构建脚本、依赖源码/精确仓库修订、aria2、yt-dlp、Python、Python sdist 和 HEIF 原生库。FFmpeg 部分源码下载服务器使用镜像，但均按原构建修订定位；原仓库位置保留在 FFMPEG_DEPENDENCY_REFS.json。FFmpeg 配置与库名未混淆，不限制替换库、修改或为调试修改而逆向工程。

本版动态图文暂缓，未附 VLC/ffplay；不因此改写第三方已有版权或许可。
