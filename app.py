import os
import re
import json
import urllib.request
from flask import Flask, request, abort
from google import genai
from google.genai import types

app = Flask(__name__)

# All secrets come from the environment - nothing sensitive lives in this file.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")  # must match the secret_token set on the webhook

MODEL = "gemini-2.5-flash"          # free-tier model; change here if it ever stops being free
MAX_INPUT = 300                     # ponytail: one dish description is short; cap stops giant pastes
TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

SYSTEM_PROMPT = """You are a warm, encouraging cooking coach for a nervous home cook.
Her food often comes out bland and dry and she gets criticised at home. Your job is to build her confidence, never add pressure.

She will tell you what she is cooking, or just what she has. You are NOT a recipe finder.
Help her make THAT dish taste good and stay juicy. Focus only on two things:
1. FLAVOUR - how to season it (salt early, aromatics like garlic/ginger/onion, a sauce, a small squeeze of acid like lime or vinegar at the end).
2. MOISTURE - how to keep it from drying out (right heat, don't overcook, a quick marinade, rest meat a few minutes after cooking).

If she only names an ingredient with no cooking method, suggest ONE simple way to cook it well, then give the flavour and moisture tips for that.

How to reply:
- Plain, simple English. No chef jargon. Keep it short.
- A few clear numbered steps, not a wall of text.
- Kind and encouraging. She is nervous; reassure her.
- Plain text only. No markdown, no asterisks, no hash symbols, no bold.
- Do NOT add a closing disclaimer; that line is added separately.
- If the message is not about cooking food, gently say you only help with cooking and ask what she is making."""

TAGLINE = "These are just ideas to help - trust your own taste."
WELCOME = ("Hello! I'm your cooking helper. Tell me what you're cooking - for example "
           "\"pan-frying chicken breast\" - and I'll give you simple steps to make it "
           "tasty and juicy. What are you making today?")

# Build the Gemini client once. If the key is missing, leave it None and fail gently later.
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


def clean(text):
    # Strip any stray markdown the model emits anyway, so mum never sees * or # symbols.
    return re.sub(r"[*#`]", "", text).strip()


def tg(method, payload):
    """Call the Telegram Bot API. Stdlib only - no extra dependency to install."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(f"{TG_API}/{method}", data=data,
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20)


def coach(question):
    """Run the cooking question through Gemini and return a ready-to-send reply."""
    if client is None:
        return "The bot isn't set up yet (missing API key). Let Yong Han know."
    try:
        resp = client.models.generate_content(
            model=MODEL,
            contents=question,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.7,
                max_output_tokens=600,
                # 2.5-flash spends output budget on hidden "thinking" and truncates the
                # visible answer. This app is simple; turn it off for full, fast replies.
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        text = clean(resp.text or "")
        return (text + "\n\n" + TAGLINE) if text else \
            "I couldn't think of an answer for that one. Try saying it a different way."
    except Exception:
        # Never show mum a stack trace. Quota hits, network drops, bad key all land here.
        return "Something went wrong. Please wait a moment and try again."


@app.route("/")
def health():
    # Plain page so Render (and you) can see the service is awake.
    return "Cooking bot is running."


@app.route("/telegram", methods=["POST"])
def telegram():
    # Only Telegram knows the secret; reject anyone else who finds this address.
    if not WEBHOOK_SECRET or request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        abort(403)

    update = request.get_json(silent=True) or {}
    message = update.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    text = (message.get("text") or "").strip()

    if not chat_id:
        return "ok"  # nothing to reply to (sticker, photo, edited message, etc.)

    if text.startswith("/start"):
        reply = WELCOME
    elif not text:
        reply = "Just type what you're cooking and I'll help. For example: pan-frying chicken breast."
    else:
        tg("sendChatAction", {"chat_id": chat_id, "action": "typing"})  # shows "typing..." while it thinks
        reply = coach(text[:MAX_INPUT])

    try:
        tg("sendMessage", {"chat_id": chat_id, "text": reply})
    except Exception:
        pass  # if Telegram is unreachable there's nothing more we can do

    return "ok"


if __name__ == "__main__":
    app.run(debug=False, port=5000)
