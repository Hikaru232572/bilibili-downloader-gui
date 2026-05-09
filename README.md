# Bilibili Downloader GUI

A simple Windows desktop app for downloading Bilibili videos with `yt-dlp`, `aria2c`, and `ffmpeg`.

The app is intentionally small:

- Paste a Bilibili URL or bare `BV...` id.
- Choose a download folder.
- Pick cookies only when needed for logged-in videos.
- Click **Analyze** to load available qualities and native subtitle tracks.
- Click **Download**.

The packaged release bundles the downloader tools, so ordinary users do not need to install Python, `yt-dlp`, `aria2c`, or `ffmpeg`.

## Download

Use the latest GitHub Release and download `BilibiliDownloader-portable.zip`.

Extract it, then run:

```text
BilibiliDownloader.exe
```

## Cookies

Some Bilibili videos need logged-in cookies. The app supports three modes:

- **File**: choose a Netscape-format `cookies.txt` file.
- **Browser**: let `yt-dlp` read cookies from a local browser profile.
- **None**: use no cookies.

If browser cookie detection fails, export cookies from a logged-in browser session and pick the file in the app.

Suggested browser extensions:

- Chrome, Edge, Brave: [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
- Firefox: [cookies.txt](https://addons.mozilla.org/firefox/addon/cookies-txt/)

Keep exported cookie files private. They can grant access to logged-in sessions.

## Subtitles

The app only offers subtitle tracks that already exist in the video metadata. It does not translate, transcribe, generate, or request auto-generated subtitles.

## Build From Source

Requirements for building:

- Windows
- Python 3.12+
- PyInstaller
- `bin/yt-dlp.exe`
- `bin/aria2c.exe`
- `bin/ffmpeg.exe`

Build:

```powershell
python -m pip install pyinstaller
.\build_portable.ps1
```

The portable app is created at:

```text
dist\BilibiliDownloader
```

## Project Layout

```text
app.py              Main GUI and downloader logic
ui_kit.py           Small local Tk UI helper layer
build_portable.ps1  PyInstaller portable build script
launch.bat          Run from source with local Python
```

## License

MIT License. See [LICENSE](LICENSE).
