# MonitorEye

Press a hotkey, get an interview-ready answer in your Telegram — instantly.

MonitorEye captures your screen, runs OCR to extract text, sends both to Claude for analysis, and delivers a structured answer to your Telegram chat. Works for LeetCode-style coding problems, SQL questions, multiple choice, and open-ended SWE interview questions.

## How it works

1. Press **F1** — screen is captured after a 3-second delay (time to switch windows)
2. OCR extracts all text from the screenshot for accuracy
3. Claude classifies the problem type and generates an answer
4. Answer is sent to your Telegram bot

## Features

- Detects problem type automatically: **coding**, **SQL**, or **conceptual/MCQ**
- Matches the exact language and function signature shown in the code editor
- Interview-ready answers: approach, complexity, and full solution with inline comments
- Sends to Telegram with proper code formatting
- **F2** clears the Telegram chat between sessions
- Runs as a background service — starts on boot, restarts on crash
- Works with lid closed (use `sudo pmset -a disablesleep 1`)

## Requirements

- macOS (uses `screencapture` and Vision OCR)
- Python 3.9+
- An [Anthropic API key](https://console.anthropic.com/)
- A Telegram bot token and chat ID ([setup guide](https://core.telegram.org/bots#how-do-i-create-a-bot))

## Installation

```bash
pip install anthropic pillow pynput pyobjc-framework-Vision
```

`pyobjc-framework-Vision` powers the on-device OCR. If it's not installed, MonitorEye still works — it just relies on Claude's vision alone.

## Configuration

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

```
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

Then export them before running:

```bash
export $(cat .env | xargs)
python3 monitor_eye_mac.py
```

## Capture source (screen vs. capture card)

MonitorEye can capture either the whole Mac display or a frame straight from a
video **capture card** (e.g. an HDMI feed from a second device) — no OBS needed.

Set these in `.env`:

```
CAPTURE_SOURCE=screen        # or "device"
VIDEO_DEVICE=USB Video       # AVFoundation device name (NOT an index)
VIDEO_SIZE=1920x1080         # must match a mode the device reports
VIDEO_FRAMERATE=60
VIDEO_PIXEL_FORMAT=uyvy422
```

- `screen` — grabs the display with `screencapture` (waits `CAPTURE_DELAY`, default 3s).
- `device` — grabs one frame via `ffmpeg` (`brew install ffmpeg`), near-instant.

List available devices and their names:

```bash
ffmpeg -f avfoundation -list_devices true -i ""
```

Select the device **by name**, not by index — indices reshuffle when iPhone
Continuity cameras register/deregister. The first device run triggers a macOS
**Camera** permission prompt for your terminal; grant it and rerun.

Test a single grab manually:

```bash
ffmpeg -f avfoundation -pixel_format uyvy422 -video_size 1920x1080 \
  -framerate 60 -i "USB Video" -frames:v 1 -y /tmp/test.png && open /tmp/test.png
```

## Testing the capture (`--test-capture`)

Before a session, verify the capture path without launching the hotkey listener
or spending an API call:

```bash
set -a; source .env; set +a
python3 monitor_eye_mac.py --test-capture
```

It grabs one frame from the configured source, saves it to
`/tmp/monitor_eye_test.jpg`, runs OCR and prints a text preview, then opens the
image so you can confirm the feed is live and framed correctly.

## Running as a background service (launchd)

To auto-start on login and keep running in the background, create a launchd plist at `~/Library/LaunchAgents/com.monitoreye.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.monitoreye</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/path/to/monitor_eye_mac.py</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>ANTHROPIC_API_KEY</key>
        <string>sk-ant-...</string>
        <key>TELEGRAM_BOT_TOKEN</key>
        <string>your_bot_token</string>
        <key>TELEGRAM_CHAT_ID</key>
        <string>your_chat_id</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/monitoreye.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/monitoreye.log</string>
</dict>
</plist>
```

Load it:

```bash
launchctl load ~/Library/LaunchAgents/com.monitoreye.plist
```

Check logs:

```bash
tail -f /tmp/monitoreye.log
```

## Hotkeys

| Key | Action |
|-----|--------|
| F1 | Capture screen and analyze |
| F2 | Clear Telegram chat |
| Ctrl+Shift+Q | Quit |

> On Mac, F1/F2 may control brightness by default. Go to System Settings → Keyboard → enable "Use F1, F2, etc. as standard function keys", or press Fn+F1 / Fn+F2.

## Lid-closed usage

To keep the Mac awake with the lid closed (no external monitor needed):

```bash
sudo pmset -a disablesleep 1
```

To re-enable sleep when done:

```bash
sudo pmset -a disablesleep 0
```
