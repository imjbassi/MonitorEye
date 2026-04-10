#!/usr/bin/env python3
"""
Monitor Eye (Mac Edition)
─────────────────────────
F1  → Capture & Analyze
F2  → Clear Telegram chat
Ctrl+Shift+Q → Quit
"""

import anthropic
import base64
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
QUIT_HOTKEY    = {keyboard.Key.ctrl_l, keyboard.Key.shift_l, keyboard.KeyCode.from_char('q')}

MODEL = "claude-sonnet-4-5"

SYSTEM_PROMPT = (
    "You are an expert software engineering interview coach and coding assistant. "
    "You will be given a screenshot of a screen showing an interview or coding problem. "
    "IMPORTANT: Scan the ENTIRE screen carefully before responding.\n\n"

    "First, classify the problem into exactly one of these types:\n"
    "- CODING: There is a code editor visible with a language selector (C++, Python, Java, etc) "
    "and a function/class template to fill in.\n"
    "- SQL: The problem asks for a database query, or shows table schemas with no code editor.\n"
    "- CONCEPTUAL: A written question, multiple choice, system design, or open-ended question "
    "with no code editor.\n\n"

    "Then respond based on the type:\n\n"

    "CODING → "
    "Read the language selector carefully (top of editor). "
    "Copy the exact function/class signature shown. "
    "Respond with:\n"
    "- Line 1: Approach in plain English\n"
    "- Line 2: Time and space complexity\n"
    "- Then the full working solution in that language with brief inline comments, "
    "wrapped in triple backticks.\n\n"

    "SQL → "
    "Write a clean, correct SQL query. "
    "Add 1 line explaining the logic. "
    "Wrap in triple backticks with sql tag.\n\n"

    "CONCEPTUAL → "
    "Give a concise, structured, interview-ready answer in plain text. "
    "For multiple choice: read ALL answer choices carefully before deciding. "
    "State the single correct answer letter and explain why it is correct in 2-3 sentences. "
    "Then in one sentence explain why each other option is wrong. "
    "For open-ended/system design: define the concept, key tradeoffs, and a brief example. "
    "Max 200 words. No code unless essential.\n\n"

    "NEVER refuse or ask for more info. Always commit to an answer based on what is visible."
)

USER_PROMPT = (
    "Classify and answer the problem on screen. "
    "If CODING: find the language selector and exact function signature, use them. "
    "If SQL: write the query. "
    "If CONCEPTUAL: answer concisely and structured. "
    "Do not ask me anything — just answer."
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ============================================================
#  CAPTURE
# ============================================================

def capture_obs_window():
    """Capture the full screen and return JPEG bytes."""
    tmp_path = Path("/tmp/monitor_eye_capture.png")

    time.sleep(3)

    try:
        subprocess.run(
            ["screencapture", "-x", "-t", "png", str(tmp_path)],
            timeout=5
        )
    except subprocess.TimeoutExpired:
        print("  Capture timed out")
        return None
    except Exception as e:
        print(f"  Capture error: {e}")
        return None

    if not tmp_path.exists():
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


def ocr_screenshot() -> str:
    """Extract text from the screenshot using macOS Vision OCR."""
    tmp_path = Path("/tmp/monitor_eye_capture.png")
    if not tmp_path.exists():
        return ""
    try:
        result = subprocess.run(
            ["python3", "-c", f"""
import Vision
import Foundation

url = Foundation.NSURL.fileURLWithPath_("{tmp_path}")
handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, {{}})
req = Vision.VNRecognizeTextRequest.alloc().init()
req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
handler.performRequests_error_([req], None)
lines = []
for obs in (req.results() or []):
    cands = obs.topCandidates_(1)
    if cands:
        lines.append(cands[0].string())
print("\\n".join(lines))
"""],
            capture_output=True, text=True, timeout=10
        )
        text = result.stdout.strip()
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

def _telegram_request(endpoint: str, payload: dict):
    """Make a POST request to the Telegram Bot API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{endpoint}"
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def send_telegram(text: str):
    """Format and send a message to Telegram using HTML, splitting if over 4096 chars."""

    # Convert ```code``` blocks to <pre><code>
    def replace_code_block(m):
        code = m.group(1).strip()
        return f"<pre><code>{html.escape(code)}</code></pre>"

    # Split on code blocks, escape plain text segments, leave code blocks as-is
    parts = re.split(r"(```(?:\w+)?\n?.*?```)", text, flags=re.DOTALL)
    formatted_parts = []
    for part in parts:
        if part.startswith("```"):
            formatted_parts.append(re.sub(r"```(?:\w+)?\n?(.*?)```", replace_code_block, part, flags=re.DOTALL))
        else:
            formatted_parts.append(html.escape(part))
    formatted = "".join(formatted_parts)

    # Split into 4096-char chunks so Telegram never silently truncates
    MAX_TG = 4096
    chunks = [formatted[i:i+MAX_TG] for i in range(0, len(formatted), MAX_TG)]
    try:
        for chunk in chunks:
            _telegram_request("sendMessage", {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
                "parse_mode": "HTML",
            })
        print(f"  Sent to Telegram ({len(chunks)} message(s)).")
    except Exception as e:
        print(f"  Telegram error: {e}")


def clear_telegram():
    """Delete all recent messages from the bot in the chat."""
    print("  Clearing Telegram chat...")
    try:
        # Get recent updates to find message IDs
        result = _telegram_request("getUpdates", {"limit": 100, "offset": -100})
        deleted = 0
        seen_ids = set()

        for update in result.get("result", []):
            msg = update.get("message") or update.get("channel_post")
            if msg and msg.get("chat", {}).get("id") == int(TELEGRAM_CHAT_ID):
                mid = msg["message_id"]
                if mid not in seen_ids:
                    seen_ids.add(mid)
                    try:
                        _telegram_request("deleteMessage", {
                            "chat_id": TELEGRAM_CHAT_ID,
                            "message_id": mid,
                        })
                        deleted += 1
                    except Exception:
                        pass

        # Also try deleting a range of recent message IDs directly
        # (bot can only delete its own messages; try last 200)
        try:
            latest = max(seen_ids) if seen_ids else 1000
        except Exception:
            latest = 1000

        for mid in range(max(1, latest - 200), latest + 1):
            if mid not in seen_ids:
                try:
                    _telegram_request("deleteMessage", {
                        "chat_id": TELEGRAM_CHAT_ID,
                        "message_id": mid,
                    })
                    deleted += 1
                except Exception:
                    pass

        print(f"  Cleared {deleted} messages from Telegram.")
        send_telegram("<b>Chat cleared.</b>")

    except Exception as e:
        print(f"  Clear error: {e}")


# ============================================================
#  CLAUDE API
# ============================================================

client = None

def init_client():
    global client
    try:
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
        print("  Claude API client ready")
    except Exception as e:
        print(f"  API client error: {e}")
        sys.exit(1)


def analyze_image(jpeg_bytes: bytes, ocr_text: str = "") -> str:
    img_b64 = base64.standard_b64encode(jpeg_bytes).decode("utf-8")
    prompt = USER_PROMPT
    if ocr_text:
        prompt += f"\n\nHere is the exact text extracted from the screen via OCR — use this for accuracy:\n\n{ocr_text}"
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            temperature=0,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": img_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt,
                    },
                ],
            }],
        )
        return "".join(block.text for block in response.content if block.type == "text")
    except anthropic.APIError as e:
        return f"API Error: {e}"
    except Exception as e:
        return f"Error: {e}"


# ============================================================
#  HOTKEY LISTENER
# ============================================================

current_keys = set()
capturing = False


def on_press(key):
    global capturing
    current_keys.add(key)

    if QUIT_HOTKEY.issubset(current_keys):
        print("\nQuitting Monitor Eye.")
        return False

    if CLEAR_HOTKEY.issubset(current_keys):
        threading.Thread(target=clear_telegram, daemon=True).start()

    if CAPTURE_HOTKEY.issubset(current_keys) and not capturing:
        capturing = True
        run_pipeline()
        capturing = False


def on_release(key):
    current_keys.discard(key)


def run_pipeline():
    print("\n" + "=" * 60)
    print("CAPTURING...")
    print("=" * 60)

    start = time.time()

    jpeg_bytes = capture_obs_window()
    if not jpeg_bytes:
        print("  Capture failed. Try again.")
        return

    # Run OCR in parallel with the API call setup
    ocr_result = {"text": ""}
    def run_ocr():
        ocr_result["text"] = ocr_screenshot()
    ocr_thread = threading.Thread(target=run_ocr)
    ocr_thread.start()
    ocr_thread.join(timeout=8)

    print("Sending to Claude...")
    response = analyze_image(jpeg_bytes, ocr_result["text"])
    elapsed = time.time() - start

    threading.Thread(target=send_telegram, args=(response,), daemon=True).start()

    print("\n" + "-" * 60)
    print(response)
    print("-" * 60)
    print(f"Done in {elapsed:.1f}s")
    print("=" * 60)
    print(f"\nReady — F1 capture | F2 clear Telegram")


# ============================================================
#  MAIN
# ============================================================

def main():
    print(r"""
    ╔══════════════════════════════════════╗
    ║       Monitor Eye (Mac Edition)      ║
    ╠══════════════════════════════════════╣
    ║  F1  →  Capture & Analyze            ║
    ║  F2  →  Clear Telegram chat          ║
    ║  Ctrl+Shift+Q  →  Quit               ║
    ╚══════════════════════════════════════╝
    """)

    print("Starting up...")
    init_client()
    print(f"\n  Ready. F1 to capture, F2 to clear Telegram.\n")

    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()


if __name__ == "__main__":
    main()
