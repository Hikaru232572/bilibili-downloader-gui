# 构建 v1.1.0-rc.1

需要 Windows x64、Python 3.12.10 和 PowerShell。依赖固定在 requirements-lock.txt，上游来源与改动见 vendor/UPSTREAM.json。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_tools.ps1
.\launch.bat
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_portable.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_tests.ps1
```

setup_tools.ps1 从本版本 Release 取得固定工具包，校验整包及各文件后安装到 bin/；也可用 -ArchivePath <ZIP 路径> 离线安装。工具包括 yt-dlp、aria2、FFmpeg/FFprobe 和所需 DLL。本期不需要 VLC。

输出为 dist/BilibiliDownloader。VERSION、app.py 的 APP_VERSION 和 version_info.txt 标识版本，EXE 文件属性也有版本。请保留整个文件夹。

测试含实时平台调用，权限/限流/网络失败与本地回归失败分开记录。个人凭据不随源码提供。再分发时保留原版权、第三方许可及对应源码；详情见 THIRD_PARTY_NOTICES.md 和 Release 附件。原始媒体、凭据、设置和开发日志不能作为发行 ZIP 输入。
