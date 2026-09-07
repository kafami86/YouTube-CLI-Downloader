# 🎬 YouTube CLI Downloader

> 🚀 Download **YouTube videos and MP3 audio** easily, quickly, and directly from your terminal.

---

## ✨ Features

* 🎥 **Download MP4 videos**
* 🎵 **Download MP3 audio**
* 📺 Choose your preferred video quality
* 🎚️ Select audio quality
* 📋 Download playlists
* ⚡ Fast downloads powered by **yt-dlp**
* 🔧 Automatic video + audio merging with **FFmpeg**
* 📊 Beautiful real-time download progress
* 🌑 Clean, dark **YouTube-inspired CLI**
* 🛠️ Easy configuration with advanced options

---

## ⚠️ First Run

> **FFmpeg and yt-dlp are not included in the project by default.**

Before downloading anything for the first time, open the application and run:

```text
🔄 Update yt-dlp
🔄 Update FFmpeg
```

The required components will automatically be downloaded and added to the project.

✅ **After this one-time setup, the downloader is ready to use.**

---

## 🚀 Easy to Use

Run the application:

```bash
python main.py
```

Then choose an option from the menu:

```text
[1] 🎥 Download MP4
[2] 🎵 Download MP3
[3] 📺 Download Playlist
[4] 🔍 Video Information
[5] ⚙️ Settings
```

Simply paste your **YouTube URL** and let the application handle the rest.

---

## 🎥 Download MP4

Choose your desired video quality:

```text
[1] 2160p
[2] 1440p
[3] 1080p
[4] 720p
[5] 480p
[6] Best Available
```

The application automatically downloads the required video and audio streams and uses **FFmpeg** to merge them when necessary.

Your videos are saved in:

```text
download/video/
```

---

## 🎵 Download MP3

Choose your preferred audio quality:

```text
[1] 128 kbps
[2] 192 kbps
[3] 256 kbps
[4] 320 kbps
[5] Best Available
```

Your MP3 files are saved in:

```text
download/audio/
```

---

## 💡 Example

### 🎥 Video Download

```text
🎬 YouTube URL:
> https://youtube.com/watch?v=...

🔎 Fetching video information...
⬇️ Downloading video...
🎵 Downloading audio...
🔧 Merging with FFmpeg...
✅ Download complete!

📁 Saved to:
download/video/Example Video.mp4
```

### 🎵 MP3 Download

```text
🎵 YouTube URL:
> https://youtube.com/watch?v=...

🔎 Processing...
🎵 Extracting audio...
✅ Download complete!

📁 Saved to:
download/audio/Example Song.mp3
```

---

## 🧰 Powered By

| Technology    | Purpose                    |
| ------------- | -------------------------- |
| 🐍 **Python** | Application & CLI          |
| ⚡ **yt-dlp**  | YouTube downloading engine |
| 🔧 **FFmpeg** | Merging & media processing |
| 🎨 **Rich**   | Terminal UI                |

---

## ❤️ Simple. Fast. Powerful.

A clean and easy-to-use command-line tool for downloading **YouTube MP4 videos and MP3 audio** with **Python + yt-dlp + FFmpeg**.
