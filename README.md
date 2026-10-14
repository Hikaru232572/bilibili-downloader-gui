# Bili

Windows 多平台下载工具。v1.1.0-rc.1 为用户已验收基础功能的初步完成版。

## 下载与运行

前往 [v1.1.0-rc.1 Release](https://github.com/Hikaru232572/bilibili-downloader-gui/releases/tag/v1.1.0-rc.1)，下载 **Bili-v1.1.0-rc.1-windows-x64.zip**，完整解压后运行 BilibiliDownloader.exe。无需安装 Python 或下载工具。请保留同目录的 _internal 文件夹，不要只复制 EXE。Windows 10/11 x64。

## 功能

- 统一接受三平台链接及分享文案，B站也支持 BV 号，多作品输入先选择一个。
- B站：视频画质、MP3/WAV、已有原生字幕。
- 抖音：视频/音频单选或同时保存，默认保留音频编码，也可选择 MP3/WAV；静态图文。
- 小红书：静态图文、按图选择、小图/源图预览、原尺寸查看。
- FIFO 队列、任务页、取消/重试、成品校验与复用、同名避让。
- 三平台独立的游客、浏览器或 Cookie 文件认证。
- 退出/后台托管，可记住并在设置修改，真正退出后手动继续任务；整批结束尝试一次通知。

## 使用

粘贴链接并解析，选择画质、视频/音频或图片，选择保存目录后加入队列，在任务页查看结果或重试。下载期间可继续解析其他作品。

需要登录时在设置选择对应平台和认证来源。浏览器可能因加密或锁库读取失败，可改用 Netscape cookies.txt。Cookie 相当于登录凭据，请勿分享或提交 Git。配置/任务/预览默认放在 %LOCALAPPDATA%\Bili，成品在所选目录；清除认证仅解除来源引用，不删除外部文件或退出浏览器账号。

## 支持边界

- **动态图文暂不支持**，识别为动态时仅保存可得静态图并提示；小红书普通视频不在本期范围。
- 不自动采集主页、合集或收藏，仅下载有权访问的内容。
- 优先无平台保存附加水印资源，保留原生水印，未知质量/水印不作保证。
- 接口、权限、网络可能变化，失败保留在任务记录；B站游客在线测试曾未返回格式，复试通过。
- 真实字幕失败后补入、通知显示/点击及独立干净机器完整验证尚未全部覆盖，本版以候选版发布。
- 程序未商业代码签名，Windows 可能提示未知发布者，请核对 Release SHA256。

## 源码与许可

构建见 [BUILDING.md](BUILDING.md)。仅保留当前源码、必要解析子集、构建脚本、依赖锁定与回归测试，开发 PRD/探针/凭据/媒体/产物不在源码中。

组合程序按 [GPL-3.0](LICENSE) 发行，保留 [原 MIT 声明](licenses/project-original-MIT.txt)。基于 [Lani27/bilibili-downloader-gui](https://github.com/Lani27/bilibili-downloader-gui) 继续开发，感谢 yt-dlp、aria2、FFmpeg、F2、XHS-Downloader。

第三方版本、许可和对应源码见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 与 licenses/。Release 同时提供源码和校验文件。FFmpeg 使用 LGPL 构建，对应源码随 Release 提供；第三方各自许可保持有效。
