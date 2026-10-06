# MonitorEye

Press F1 on your Mac and read the live answer in Safari on your iPhone.

MonitorEye captures your screen, runs OCR to extract text, sends both to Claude for analysis, and streams a structured answer to your iPhone browser. Works for LeetCode-style coding problems, SQL questions, multiple choice, and open-ended SWE interview questions.

## Live iPhone viewer (default)

```bash
cd ~/Desktop/MonitorEye
source .venv/bin/activate
# Optional: load your existing configuration
# set -a; source .env; set +a
python monitor_eye_mac.py
```

Open the **iPhone (same Wi-Fi)** link printed in Terminal on your iPhone in Safari.
Keep the page foregrounded for live updates; it reconnects and restores the latest
answer when reopened. Press F1 on the Mac to capture with **Sonnet 5.5, medium effort**.
Press F3 to retry the same image and OCR using **Opus 5.5, high effort**.
Only the requested model runs; there is no automatic second call. F2 clears
the standalone answer, cached capture, and standalone Claude conversations when idle.
Ctrl+Shift+Q quits. No Telegram configuration or iPhone app installation is needed.
Both answer views render Markdown: **bold**, *italic*, bullet/numbered lists,
headings, quotes, links, tables, task lists, and inline/fenced code. Code wraps on
iPhone portrait screens. Copy answer preserves the original Markdown text.
The page streams text as Claude generates it and shows
capture, OCR, first-text and total timings. It never triggers captures from the phone.

The viewer is a local HTTP server, not a hosted website. The private random link
changes on restart; anyone with the link on the LAN can read the latest answer.
Use a trusted Wi-Fi network. If macOS asks, allow incoming local connections for
Python. For F1/F2, enable your launching app (Terminal, or Codex when launched
from Codex) under System Settings → Privacy & Security → Accessibility, then restart
MonitorEye. Screen capture also needs Screen Recording permission.
Guest Wi-Fi/client isolation can prevent the phone reaching the Mac.
`LIVE_PORT` defaults to 8765. Screen capture defaults to zero delay; set
`CAPTURE_DELAY=3` if you need time to switch windows. Existing `.env` values override
the defaults. Set `TELEGRAM_ENABLED=1` to additionally deliver completed answers to
Telegram using the existing bot settings.

## Project sessions from screenshots

No repo upload or local files are required. On the phone page, open **Project
session**, keep **Screenshots only** selected, choose Sonnet or Opus, and press
**Start session**. Starting a project sets **F1 sends to → Project session**.

Show each relevant screen on your configured capture source (Mac display or
capture card), then press **F1**. Each captured image and its OCR join the same
conversation. Wait for the reply before adding another screenshot. Use the phone's
**Message or follow-up** box to describe your goal, ask questions, or request code
changes. Claude connects the screenshots and messages it has received; it cannot
inspect unseen files, edit anything, or run commands. All tools are disabled for
screenshot-only sessions. The phone shows the number of screenshots submitted.

Switch **F1 sends to → Snapshot** for separate answers without ending the project.
Switch it back to continue adding project screenshots. Merely browsing between
the two tabs does not change F1's destination. **F2** clears standalone snapshots
only; **F3** retries the last standalone capture with Opus. Neither resets or
retries the project conversation. Snapshot prompt presets apply to standalone
captures; give project instructions in the message box.

**New session** clears the project context and can change the model/source.
**Stop** cancels the project worker; start a new session afterward. F1 remains
pointed at Project until you change its selector, so a stopped/failed project
cannot silently turn into a standalone capture. A failed capture can be retried
with F1 without losing context; a failed/timed-out Claude worker requires a new
session. A capture in progress belongs to the session selected when F1 was
pressed and is discarded if that session is stopped or replaced.

The chat survives phone-page reloads while MonitorEye is running, but is held
only in memory: restarting MonitorEye clears it. Long conversations may be
compacted by Claude Code; resend screenshots if important details are missing.
Start a new session after 100 exchanges (screenshots and text questions combined).
Model calls still use your Claude subscription.

### Optional local repository access

Choose **Local repository** only if the repo is already available on this Mac.
Enter its absolute folder path before starting the session. This optional mode
adds Read, Glob, and Grep tools to inspect the repo and also accepts F1 screenshots.
It cannot edit files or run shell commands. Claude Code's restricted mode confines
file tools to the selected working directory; additional permissions are denied.
Plugins, MCP servers, and project hooks are not loaded. This is permission
scoping, not an operating-system sandbox. Keep the private viewer link private:
anyone with it on your LAN can see the conversation and control project sessions.
Use trusted Wi-Fi.

## Choose a prompt before capturing

Use **Prompt for your next capture** above the answer on the iPhone page:

- **Auto**: detect coding, SQL, or conceptual questions (the original behavior).
- **Quick answer**: answer first with minimal explanation.
- **Explain / learn**: reasoning, a worked example, solution, and relevant complexity.
- **Review my code**: bugs, edge cases, and the smallest useful correction.
- **Custom**: enter up to 6,000 characters and tap **Save & use custom**.

Wait for **Saved for the next capture** before pressing F1. The browser remembers
your selection and custom text locally and reapplies them on reopening the same
viewer address (a changed IP, port, or browser has separate storage). Editing
custom text does not apply it until you save. If several viewers are open, the
last saved selection controls the next capture; **Next F1** shows that selection.

Each capture freezes its selected prompt before capture/OCR. **This answer** shows
which prompt it used. F3 reuses that capture's prompt along with its image and OCR,
even if you change the selector afterward. Changing a prompt makes no model call
and does not capture the screen. Sonnet/Opus model choices remain independent.
The private viewer link is also required to change prompt settings.

## Faster repeated captures

MonitorEye starts the Sonnet process during startup and keeps it alive. Opus gets
its own process on the first F3 retry. Subsequent requests reuse those processes;
login is checked only when starting or reconnecting a worker. Each standalone
snapshot request first clears its previous conversation locally. Project sessions
retain context instead. F2 clears standalone context without stopping healthy workers. A timeout or
crash closes that worker; press F1/F3 to reconnect. No automatic model request is
made at startup or during a context reset.

With `CAPTURE_SOURCE=device`, one ffmpeg process keeps the capture card open.
It drains video continuously at 10 frames/second for still-image sampling and
holds only the latest complete frame. F1 waits for the next complete frame;
it does not reuse an old frame. The configured input resolution and framerate
remain unchanged. Device discovery runs only when opening/reconnecting, not on
every capture. A stalled/disconnected feed is reopened once, then reports an
error. This uses some CPU while idle and holds the card until MonitorEye exits.
Do not open the same card in another app simultaneously.

Both worker processes and the capture feed stop on Ctrl+C, Ctrl+Shift+Q, or SIGTERM.
Restart MonitorEye after upgrading to activate these changes. These improvements
remove local startup overhead; Claude's network/model latency still varies.

## How it works

1. Press **F1** — screen is captured immediately by default
2. OCR extracts all text from the screenshot for accuracy
3. Claude classifies the problem type and generates an answer
4. Answer streams to the phone viewer; optional Telegram delivery follows completion

## Features

- Detects problem type automatically: **coding**, **SQL**, or **conceptual/MCQ**
- Matches the exact language and function signature shown in the code editor
- Interview-ready answers: approach, complexity, and full solution with inline comments
- Streams to Safari on your iPhone, with optional Telegram delivery
- **F2** clears the live answer when idle (and Telegram when enabled)
- Runs as a background service — starts on boot, restarts on crash
- Works with lid closed (use `sudo pmset -a disablesleep 1`)

## Requirements

- macOS (uses `screencapture` and Vision OCR)
- Python 3.9+
- [Claude Code](https://code.claude.com/docs/en/setup) installed and signed in with a paid Claude subscription (current version supporting `--safe-mode` and `auth status`)
- Optional: a Telegram bot token and chat ID ([setup guide](https://core.telegram.org/bots#how-do-i-create-a-bot))

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install pillow pynput pyobjc-framework-Vision
claude auth login
```

`pyobjc-framework-Vision` powers the on-device OCR. If it's not installed, MonitorEye still works — it just relies on Claude's vision alone.

## Configuration

Sign in with your Claude account when prompted (not an API/Console account).
MonitorEye uses `claude -p` with your login. It removes API credentials and provider
overrides from the child process and rejects non-Claude-account authentication.
Subscription usage limits still apply; account-level extra usage, if enabled, can
incur charges. [Current Anthropic guidance](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan).

Optional: copy `.env.example` to `.env` to customize settings. Telegram credentials are only required when `TELEGRAM_ENABLED=1`:

```bash
cp .env.example .env
```

```
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

Then export them before running:

```bash
set -a
source .env
set +a
python3 monitor_eye_mac.py
```

Optional settings: `MODEL=claude-sonnet-5-5` (or another model your plan supports),
`CLAUDE_TIMEOUT=120` (seconds), and `CLAUDE_BIN=/full/path/to/claude`.
The default is pinned to Sonnet 5.5 with `CLAUDE_EFFORT=medium`. F3 uses
`RETRY_MODEL=claude-opus-5-5` and `RETRY_EFFORT=high`. Update Claude Code with
`claude update`; Sonnet 5.5 requires version 2.1.284 or newer.

Check the local login without capturing your screen, calling the model, or sending Telegram messages:

```bash
python3 monitor_eye_mac.py --test-claude
```

If login is missing, run `claude auth login` in Terminal as the same macOS user
that runs MonitorEye. Update an older CLI with `claude update`. The screenshot
and OCR are passed together over stdin; Claude has no tools, MCP servers, or
saved conversation in this flow. No API key is needed.

## Capture source (screen vs. capture card)

MonitorEye can capture either the whole Mac display or a frame straight from a
video **capture card** (e.g. an HDMI feed from a second device) — no OBS needed.

Set these in `.env`:

```
CAPTURE_SOURCE=screen        # or "device"
VIDEO_DEVICE="USB Video"       # AVFoundation device name (NOT an index)
VIDEO_SIZE=1920x1080         # must match a mode the device reports
VIDEO_FRAMERATE=60
VIDEO_PIXEL_FORMAT=uyvy422
```

- `screen` — grabs the display with `screencapture` (waits `CAPTURE_DELAY`, default 0s).
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
or making a Claude request:

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
        <key>CLAUDE_BIN</key>
        <string>/opt/homebrew/bin/claude</string>
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

Keep `claude_subscription.py`, `claude_session.py`, `capture_feed.py`,
`live_view.py`, `live_view.html`, `prompt_presets.py`, `project_session.py`, and the
`vendor/` folder beside `monitor_eye_mac.py`. Use `command -v claude`
to find the `CLAUDE_BIN` path, and use the Python executable where you installed
the dependencies. Log in interactively before starting the service.

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
| F2 | Clear live answer and cached capture when idle |
| F3 | Retry last capture with Opus 5.5, high effort |
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
