#!/usr/bin/env python3
"""
Monitor Eye (Mac Edition)
─────────────────────────
F1  → Capture & Analyze
F2  → Clear answer
Ctrl+Shift+Q → Quit
"""

from claude_subscription import ClaudeSubscription
from live_view import LiveView
from prompt_presets import SYSTEM_PROMPT, default_selection, prompt_for
from capture_feed import CaptureFeed
import atexit
import signal
import html
import io
import json
import os
import re
import subprocess
import sys
import time
import threading
import urllib.request
import urllib.parse
from pathlib import Path
from pynput import keyboard

# ============================================================
#  CONFIG
# ============================================================

CAPTURE_HOTKEY = {keyboard.Key.f1}
CLEAR_HOTKEY   = {keyboard.Key.f2}
RETRY_HOTKEY   = {keyboard.Key.f3}
QUIT_HOTKEY    = {keyboard.Key.ctrl_l, keyboard.Key.shift_l, keyboard.KeyCode.from_char('q')}

MODEL = os.getenv("MODEL", "claude-sonnet-5-5")
EFFORT = os.getenv("CLAUDE_EFFORT", "medium")
RETRY_MODEL = os.getenv("RETRY_MODEL", "claude-opus-5-5")
RETRY_EFFORT = os.getenv("RETRY_EFFORT", "high")

# ── Capture source ──────────────────────────────────────────
# "screen"  → grab the whole Mac display with `screencapture` (default)
# "device"  → grab a frame straight from a video capture card via ffmpeg
CAPTURE_SOURCE = os.getenv("CAPTURE_SOURCE", "screen")

# For CAPTURE_SOURCE="device": select the AVFoundation device BY NAME, not index —
# indices reshuffle when iPhone Continuity cameras register/deregister.
# List devices:  ffmpeg -f avfoundation -list_devices true -i ""
VIDEO_DEVICE       = os.getenv("VIDEO_DEVICE", "USB Video")
VIDEO_SIZE         = os.getenv("VIDEO_SIZE", "1920x1080")   # must match a mode the device reports
VIDEO_FRAMERATE    = os.getenv("VIDEO_FRAMERATE", "60")
VIDEO_PIXEL_FORMAT = os.getenv("VIDEO_PIXEL_FORMAT", "uyvy422")

# Seconds to wait before a SCREEN capture so you can switch windows.
# Not used for device capture (external feed needs no switch), so it's ~instant.
CAPTURE_DELAY = float(os.getenv("CAPTURE_DELAY", "0"))
TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "0") == "1"
LIVE_PORT = int(os.getenv("LIVE_PORT", "8765"))
live_view = None

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ============================================================
#  CAPTURE
# ============================================================

TMP_CAPTURE = Path("/tmp/monitor_eye_capture.png")
capture_feed = None
capture_feed_lock = threading.Lock()


def _grab_screen(tmp_path: Path) -> bool:
    """Grab the whole display to tmp_path with macOS screencapture."""
    tmp_path.unlink(missing_ok=True)
    time.sleep(CAPTURE_DELAY)  # give yourself time to switch windows
    try:
        subprocess.run(["screencapture", "-x", "-t", "png", str(tmp_path)], timeout=5)
    except subprocess.TimeoutExpired:
        print("  Capture timed out")
        return False
    except Exception as e:
        print(f"  Capture error: {e}")
        return False
    return tmp_path.exists()


def _resolve_device_index(name: str) -> str:
    """Look up a video device's current AVFoundation index by name.

    ffmpeg 8.x can't open avfoundation devices by name (matching is broken), and
    indices reshuffle when Continuity cameras come/go — so we resolve the index
    on each feed open/reconnect. Returns the index as a string, or the name as a fallback.
    """
    try:
        proc = subprocess.run(
            ["ffmpeg", "-nostdin", "-f", "avfoundation",
             "-list_devices", "true", "-i", ""],
            capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL,
        )
    except Exception:
        return name  # let ffmpeg try the name and report its own error

    # Video devices are listed before the "AVFoundation audio devices:" line.
    for line in proc.stderr.splitlines():
        if "AVFoundation audio devices" in line:
            break
        m = re.search(r"\[(\d+)\]\s+(.+?)\s*$", line)
        if m and m.group(2) == name:
            return m.group(1)
    print(f"  Device '{name}' not in current list — check the connection")
    return name


def _get_capture_feed():
    global capture_feed
    with capture_feed_lock:
        if capture_feed is None:
            capture_feed = CaptureFeed(_resolve_device_index, VIDEO_DEVICE, VIDEO_SIZE,
                                       VIDEO_FRAMERATE, VIDEO_PIXEL_FORMAT)
        return capture_feed


def _grab_device(tmp_path: Path) -> bool:
    tmp_path.unlink(missing_ok=True)
    try:
        _get_capture_feed().snapshot(tmp_path)
        return True
    except Exception as exc:
        print(f"  Device capture failed: {exc}")
        return False



def capture_obs_window():
    """Capture from the configured source and return JPEG bytes."""
    tmp_path = TMP_CAPTURE

    if CAPTURE_SOURCE == "device":
        ok = _grab_device(tmp_path)
    else:
        ok = _grab_screen(tmp_path)

    if not ok:
        print("  Screenshot file not created")
        return None

    try:
        from PIL import Image
        img = Image.open(tmp_path)
        buf = io.BytesIO()
        img = img.convert("RGB")

        max_dim = 1600
        if max(img.size) > max_dim:
            ratio = max_dim / max(img.size)
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)

        img.save(buf, format="JPEG", quality=85)
        jpeg_bytes = buf.getvalue()
        print(f"  Captured {img.width}x{img.height} ({len(jpeg_bytes) // 1024}KB)")
        return jpeg_bytes

    except Exception as e:
        print(f"  Image processing error: {e}")
        return None
    finally:
        # Keep tmp_path for OCR — deleted after ocr_screenshot() runs
        pass


_vision_ready = None  # None = untried, True/False = import result cached


def _init_vision() -> bool:
    """Import the macOS Vision framework once and cache the result."""
    global _vision_ready
    if _vision_ready is not None:
        return _vision_ready
    try:
        import Vision  # noqa: F401  (pyobjc-framework-Vision)
        import Foundation  # noqa: F401
        globals()["Vision"] = Vision
        globals()["Foundation"] = Foundation
        _vision_ready = True
    except Exception as e:
        print(f"  Vision OCR unavailable ({e}); relying on Claude vision only")
        _vision_ready = False
    return _vision_ready


def ocr_screenshot() -> str:
    """Extract text from the screenshot using macOS Vision OCR (in-process)."""
    tmp_path = Path("/tmp/monitor_eye_capture.png")
    if not tmp_path.exists():
        return ""
    if not _init_vision():
        tmp_path.unlink(missing_ok=True)
        return ""
    try:
        url = Foundation.NSURL.fileURLWithPath_(str(tmp_path))
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, {})
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        handler.performRequests_error_([req], None)
        lines = []
        for obs in (req.results() or []):
            cands = obs.topCandidates_(1)
            if cands:
                lines.append(cands[0].string())
        text = "\n".join(lines).strip()
        if text:
            print(f"  OCR extracted {len(text)} chars")
        return text
    except Exception as e:
        print(f"  OCR skipped: {e}")
        return ""
    finally:
        tmp_path.unlink(missing_ok=True)


# ============================================================
#  TELEGRAM
# ============================================================

SENT_IDS_PATH = Path("/tmp/monitor_eye_sent_ids.json")


def _telegram_request(endpoint: str, payload: dict):
    """Make a POST request to the Telegram Bot API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{endpoint}"
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def _load_sent_ids() -> list:
    """Load the list of message IDs this bot has sent (persists across restarts)."""
    try:
        return json.loads(SENT_IDS_PATH.read_text())
    except Exception:
        return []


def _save_sent_ids(ids: list):
    try:
        SENT_IDS_PATH.write_text(json.dumps(ids))
    except Exception:
        pass


def _record_sent_id(message_id: int):
    ids = _load_sent_ids()
    ids.append(message_id)
    _save_sent_ids(ids)


MAX_TG = 4096                                  # Telegram's per-message char limit
_CODE_WRAP = len("<pre><code></code></pre>")    # overhead of wrapping a code block


def _split_escaped(text: str, budget: int) -> list:
    """Split raw text into pieces whose HTML-escaped length is <= budget,
    breaking on line boundaries (and hard-splitting a single over-long line)."""
    pieces, cur, cur_len = [], [], 0
    for line in text.split("\n"):
        line_len = len(html.escape(line)) + 1  # +1 for the newline
        if cur and cur_len + line_len > budget:
            pieces.append("\n".join(cur))
            cur, cur_len = [], 0
        if line_len > budget:
            # A single line too long to fit — hard-split by characters.
            s = line
            while s:
                lo, hi = 1, len(s)
                while lo < hi:  # largest prefix whose escaped form fits
                    mid = (lo + hi + 1) // 2
                    if len(html.escape(s[:mid])) <= budget:
                        lo = mid
                    else:
                        hi = mid - 1
                pieces.append(s[:lo])
                s = s[lo:]
            continue
        cur.append(line)
        cur_len += line_len
    if cur:
        pieces.append("\n".join(cur))
    return pieces


def _telegram_messages(text: str) -> list:
    """Turn Claude's markdown into a list of HTML messages, each <= MAX_TG,
    never splitting inside an HTML tag or a code block."""
    segments = re.split(r"(```(?:\w+)?\n?.*?```)", text, flags=re.DOTALL)
    fragments = []  # each fragment is valid HTML and individually <= MAX_TG
    for seg in segments:
        if not seg:
            continue
        if seg.startswith("```"):
            m = re.match(r"```(?:\w+)?\n?(.*?)```", seg, flags=re.DOTALL)
            code = (m.group(1) if m else seg).strip()
            for chunk in _split_escaped(code, MAX_TG - _CODE_WRAP):
                fragments.append(f"<pre><code>{html.escape(chunk)}</code></pre>")
        else:
            for chunk in _split_escaped(seg, MAX_TG):
                fragments.append(html.escape(chunk))

    # Greedily pack fragments into as few messages as possible.
    messages, buf = [], ""
    for frag in fragments:
        if buf and len(buf) + len(frag) > MAX_TG:
            messages.append(buf)
            buf = frag
        else:
            buf += frag
    if buf:
        messages.append(buf)
    return messages


def send_telegram(text: str):
    """Format and send a message to Telegram as HTML, split safely at boundaries."""
    messages = _telegram_messages(text)
    try:
        for msg in messages:
            resp = _telegram_request("sendMessage", {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": msg,
                "parse_mode": "HTML",
            })
            mid = (resp.get("result") or {}).get("message_id")
            if mid is not None:
                _record_sent_id(mid)
        print(f"  Sent to Telegram ({len(messages)} message(s)).")
    except Exception as e:
        print(f"  Telegram error: {e}")


def clear_telegram():
    """Delete the messages this bot has sent (tracked by ID, no ID-range guessing)."""
    print("  Clearing Telegram chat...")
    try:
        ids = _load_sent_ids()
        deleted = 0
        for mid in ids:
            try:
                _telegram_request("deleteMessage", {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "message_id": mid,
                })
                deleted += 1
            except Exception:
                # Message already gone, too old (>48h), or not ours — skip.
                pass

        _save_sent_ids([])  # reset the tracked set
        print(f"  Cleared {deleted} messages from Telegram.")
        send_telegram("<b>Chat cleared.</b>")

    except Exception as e:
        print(f"  Clear error: {e}")


# ============================================================
#  CLAUDE SUBSCRIPTION (Claude Code)
# ============================================================

client = None


def init_client(warm=True):
    global client
    try:
        client = ClaudeSubscription(model=MODEL, timeout=float(os.getenv("CLAUDE_TIMEOUT", "120")), effort=EFFORT)
        if warm:
            client.warm(SYSTEM_PROMPT)
        else:
            client.check_auth()
        print("  Claude Code ready (Claude account login; subscription limits apply)")
    except (RuntimeError, ValueError) as e:
        print(f"  Claude setup error: {e}")
        sys.exit(1)


def analyze_image(jpeg_bytes: bytes, ocr_text: str = "", on_text=None, retry=False, selection=None) -> str:
    preset_label, instructions = prompt_for(selection or default_selection())
    prompt = f"Selected response style: {preset_label}\n\n{instructions}"
    if ocr_text:
        prompt += f"\n\nHere is the exact text extracted from the screen via OCR — use this for accuracy:\n\n{ocr_text}"
    try:
        return client.analyze(jpeg_bytes, prompt, SYSTEM_PROMPT, on_text=on_text,
                              model=RETRY_MODEL if retry else MODEL,
                              effort=RETRY_EFFORT if retry else EFFORT)
    except RuntimeError as e:
        return f"Claude Error: {e}"
    except Exception as e:
        return f"Error: {e}"


# ============================================================
#  HOTKEY LISTENER
# ============================================================

current_keys = set()
capturing = False
last_capture = None


def on_press(key):
    global capturing, last_capture
    if key in current_keys:
        return  # Ignore key-repeat; one request per physical press.
    current_keys.add(key)

    if QUIT_HOTKEY.issubset(current_keys):
        print("\nQuitting Monitor Eye.")
        return False

    if CLEAR_HOTKEY.issubset(current_keys) and not capturing:
        last_capture = None
        if client:
            client.reset()
        if live_view:
            live_view.update(text="", status="Ready — press F1 on your Mac", timing="", used_prompt="")
        if TELEGRAM_ENABLED:
            threading.Thread(target=clear_telegram, daemon=True).start()

    if RETRY_HOTKEY.issubset(current_keys) and not capturing:
        if last_capture is None:
            print("Capture with F1 before retrying with F3.")
            return
        capturing = True
        threading.Thread(target=run_pipeline, kwargs={"retry": True}, daemon=True).start()
        return

    if CAPTURE_HOTKEY.issubset(current_keys) and not capturing:
        capturing = True
        project_id = live_view.capture_destination() if live_view else None
        threading.Thread(target=run_pipeline, kwargs={'project_id':project_id}, daemon=True).start()


def on_release(key):
    current_keys.discard(key)


def run_pipeline(retry=False, project_id=None):
    global capturing, last_capture
    if project_id is not None and not retry:
        try:
            live_view.update(capture_notice='')
            live_view.project.capture(project_id, capture_obs_window, ocr_screenshot)
        except Exception as exc:
            print(f'  Project capture: {exc}')
            live_view.update(capture_notice=str(exc))
        finally:
            capturing = False
            TMP_CAPTURE.unlink(missing_ok=True)
            print('Ready — F1 adds to the selected conversation; F2/F3 affect standalone snapshots.')
        return
    start = time.monotonic()
    first_text = None
    # Freeze the selection before capture/OCR so later phone changes affect only the next F1.
    selection = (last_capture[2] if retry and last_capture else
                 live_view.prompt_snapshot() if live_view else default_selection())
    preset_label, _ = prompt_for(selection)
    if live_view:
        live_view.update(text="", status="Retrying last capture…" if retry else "Capturing…", timing="", used_prompt=preset_label)

    def receive(text):
        nonlocal first_text
        if text and first_text is None:
            first_text = time.monotonic() - start
            print(f"  First text in {first_text:.1f}s")
        if live_view:
            live_view.append(text)

    try:
        if retry:
            if last_capture is None:
                raise RuntimeError("Capture with F1 before retrying with F3.")
            jpeg_bytes, ocr_text, selection = last_capture
            captured = ocr_done = start
        else:
            last_capture = None
            jpeg_bytes = capture_obs_window()
            if not jpeg_bytes:
                raise RuntimeError("Capture failed. Check macOS Screen Recording or Camera permission.")
            captured = time.monotonic()
            if live_view:
                live_view.update(status="Reading screenshot…")
            ocr_text = ocr_screenshot()
            ocr_done = time.monotonic()
            last_capture = (jpeg_bytes, ocr_text, selection)
        label = f"{RETRY_MODEL} · {RETRY_EFFORT}" if retry else f"{MODEL} · {EFFORT}"
        if live_view:
            live_view.update(status=f"{label} — thinking…", timing=label)
        response = analyze_image(jpeg_bytes, ocr_text, on_text=receive, retry=retry, selection=selection)
        elapsed = time.monotonic() - start
        timing = label + (" · Reused capture" if retry else f" · Capture {captured-start:.1f}s · OCR {ocr_done-captured:.1f}s")
        if first_text is not None:
            timing += f" · First text {first_text:.1f}s"
        timing += f" · Total {elapsed:.1f}s"
        failed = response.startswith(("Claude Error:", "Error:"))
        if live_view:
            live_view.update(text=response, status="Request failed" if failed else "Done", timing=timing)
        if TELEGRAM_ENABLED:
            threading.Thread(target=send_telegram, args=(response,), daemon=True).start()
        print(response)
        print(timing)
    except Exception as exc:
        print(f"  Error: {exc}")
        if live_view:
            live_view.update(status="Request failed", text=str(exc))
    finally:
        capturing = False
        print("Ready — F1 capture | F2 clear answer | F3 retry with Opus")


# ============================================================
#  MAIN
# ============================================================

def test_capture():
    """Grab a single frame from the configured source, save it, and open it.

    Sanity-checks the capture path (and OCR) without starting the hotkey listener.
    Usage: python3 monitor_eye_mac.py --test-capture
    """
    if CAPTURE_SOURCE == "device":
        print(f"Test capture from device '{VIDEO_DEVICE}' "
              f"({VIDEO_SIZE}@{VIDEO_FRAMERATE}, {VIDEO_PIXEL_FORMAT})...")
    else:
        print(f"Test capture from screen ({CAPTURE_DELAY:.0f}s delay)...")

    jpeg_bytes = capture_obs_window()
    if not jpeg_bytes:
        print("  Capture FAILED — see the error above.")
        sys.exit(1)

    out = Path("/tmp/monitor_eye_test.jpg")
    out.write_bytes(jpeg_bytes)
    print(f"  Saved {len(jpeg_bytes) // 1024}KB → {out}")

    # Also exercise OCR so you can see what text the model would receive.
    _init_vision()
    ocr_text = ocr_screenshot()
    if ocr_text:
        preview = ocr_text[:200].replace("\n", " ")
        print(f"  OCR ({len(ocr_text)} chars): {preview}...")
    else:
        print("  OCR: no text extracted")

    subprocess.run(["open", str(out)])
    print("  Opened the captured frame. Looks right? Then run without --test-capture.")


def main():
    global live_view
    if "--test-claude" in sys.argv:
        init_client(warm=False)
        return

    if "--test-capture" in sys.argv:
        test_capture()
        return

    print(r"""
    ╔══════════════════════════════════════╗
    ║       Monitor Eye (Mac Edition)      ║
    ╠══════════════════════════════════════╣
    ║  F1  →  Capture & Analyze            ║
    ║  F2  →  Clear live answer            ║
    ║  F3  →  Retry last capture with Opus ║
    ║  Ctrl+Shift+Q  →  Quit               ║
    ╚══════════════════════════════════════╝
    """)

    print("Starting up...")
    init_client()
    live_view = LiveView(port=LIVE_PORT).start()
    print(f"\n  iPhone (same Wi-Fi): {live_view.phone_url()}")
    print(f"  This Mac: {live_view.url()}")
    print("  Keep the private viewer link open in Safari. It changes each restart.")
    print("  Start a Project session on your phone to build context with F1 screenshots.")
    _init_vision()  # warm the OCR framework so the first capture isn't slow
    if CAPTURE_SOURCE == "device":
        try:
            _get_capture_feed().start()
            print("  Capture feed kept open (latest frames only)")
        except Exception as exc:
            print(f"  Capture warm-up failed; F1 will retry: {exc}")
        print(f"  Capture source: device '{VIDEO_DEVICE}' "
              f"({VIDEO_SIZE}@{VIDEO_FRAMERATE}, {VIDEO_PIXEL_FORMAT})")
    else:
        print(f"  Capture source: screen ({CAPTURE_DELAY:.0f}s delay)")
    print(f"\n  Ready. F1 to capture, F2 to clear the answer. F3 to retry with Opus.\n")

    try:
        with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
            listener.join()
    finally:
        close_resources()


def close_resources():
    if client:
        client.close()
    if capture_feed:
        capture_feed.close()
    if live_view:
        live_view.close()


atexit.register(close_resources)

if __name__ == "__main__":
    def stop_on_signal(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop_on_signal)
    try:
        main()
    except KeyboardInterrupt:
        pass
