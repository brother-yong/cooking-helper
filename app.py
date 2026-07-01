import os
import re
import json
import urllib.request
from collections import defaultdict
from flask import Flask, request, abort
from google import genai
from google.genai import types

app = Flask(__name__)

# All secrets come from the environment - nothing sensitive lives in this file.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")  # must match the secret_token set on the webhook

MODEL = "gemini-2.5-flash"          # free-tier model; change here if it ever stops being free
MAX_INPUT = 300                     # ponytail: one message is short; cap stops giant pastes
MAX_TURNS = 8                       # remember last 8 messages (~4 back-and-forths) for follow-ups
TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

SYSTEM_PROMPT = """You are a warm, patient cooking coach chatting with a nervous home cook on her phone.
Her food often comes out bland and dry and she gets criticised at home. Build her confidence; never make her feel judged or rushed.

You are chatting back and forth. She may ask follow-up questions or raise worries like "it's still dry" or "is it cooked yet?" - answer them simply and kindly, using what was said earlier in the chat.

She might do one of these:
- Tell you a dish she is already cooking -> help her make THAT dish tasty and juicy.
- Tell you what ingredients she has -> suggest ONE simple, beginner-friendly thing to cook with them, then how to make it tasty and juicy.
- Ask for an easy idea -> suggest ONE simple, cheap, hard-to-mess-up dish using common ingredients, then how to cook it.

Always focus on her two real problems:
1. FLAVOUR - season it well (salt early, aromatics like garlic/ginger/onion, a sauce, a small squeeze of acid like lime or vinegar at the end).
2. MOISTURE - keep it from drying out (right heat, don't overcook, a quick marinade, rest meat a few minutes after cooking).

VERY IMPORTANT - she needs timings. For every cooking step, give a simple time and heat, for example "medium heat, 3 to 4 minutes each side". Never say "until done" without also giving a rough time.

How to reply:
- Plain, simple English. No chef jargon. Keep it short.
- A few clear numbered steps, not a wall of text.
- Kind and encouraging. She is nervous; reassure her.
- Plain text only. No markdown, no asterisks, no hash symbols, no bold.
- Do NOT add a closing disclaimer; that line is added separately.
- If she asks something not about cooking, gently say you only help with cooking and ask what she is making."""

TAGLINE = "These are just ideas to help - trust your own taste."
WELCOME = ("Hi! I'm your cooking helper. I'll help your food taste good and stay juicy, "
           "and I'll always tell you how long to cook things.\n\n"
           "Tap a button below, or just type to me:\n"
           "\U0001F373 Help me cook this - when you know what you're making\n"
           "\U0001F9CA What can I make? - for an idea from what you have\n"
           "\U0001F3B2 Give me an easy idea - for a simple dish to try\n\n"
           "You can chat with me too. If something isn't working, just tell me and I'll help.")

# Big tappable buttons (one per row) that stay at the bottom of her chat.
KEYBOARD = {
    "keyboard": [["\U0001F373 Help me cook this"],
                 ["\U0001F9CA What can I make?"],
                 ["\U0001F3B2 Give me an easy idea"]],
    "resize_keyboard": True,
    "is_persistent": True,
}

# The two "prompt" buttons just ask her to type next - answered instantly, no AI call needed.
BUTTON_PROMPTS = {
    "\U0001F373 Help me cook this":
        "Great! Tell me what you're cooking - for example \"pan-frying chicken breast\".",
    "\U0001F9CA What can I make?":
        "Tell me what you've got in the kitchen - for example \"chicken, eggs, tomato\" - and I'll suggest something easy.",
}

# Per-chat short memory. ponytail: in-RAM only, one user; wiped on restart. Never touches disk.
# Add a size cap / time-out per chat if this ever serves many people.
HISTORY = defaultdict(list)

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


def remember(chat_id, role, text):
    """Keep the recent conversation so follow-up questions make sense. Trims to the last MAX_TURNS."""
    h = HISTORY[chat_id]
    h.append({"role": role, "parts": [{"text": text}]})
    del h[:-MAX_TURNS]


def coach(chat_id, question):
    """Run the message (with recent chat history) through Gemini and return a reply."""
    if client is None:
        return "The bot isn't set up yet (missing API key). Let Yong Han know."
    contents = HISTORY[chat_id] + [{"role": "user", "parts": [{"text": question}]}]
    try:
        resp = client.models.generate_content(
            model=MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.8,
                max_output_tokens=700,
                # 2.5-flash spends output budget on hidden "thinking" and truncates the
                # visible answer. This app is simple; turn it off for full, fast replies.
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        text = clean(resp.text or "")
        if not text:
            return "I couldn't think of an answer for that one. Try saying it a different way."
        remember(chat_id, "user", question)
        remember(chat_id, "model", text)
        return text + "\n\n" + TAGLINE
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

    low = text.lower()

    if low.startswith("/start"):
        HISTORY.pop(chat_id, None)  # fresh conversation
        tg("sendMessage", {"chat_id": chat_id, "text": WELCOME, "reply_markup": KEYBOARD})
        return "ok"

    if low.startswith("/help"):
        tg("sendMessage", {"chat_id": chat_id, "text": WELCOME, "reply_markup": KEYBOARD})
        return "ok"

    if not text:
        tg("sendMessage", {"chat_id": chat_id,
                           "text": "Just tap a button or type what you're cooking, and I'll help."})
        return "ok"

    if low.startswith("/idea"):
        tg("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        reply = coach(chat_id, "Please give me one easy dish idea to cook.")
    elif text in BUTTON_PROMPTS:
        reply = BUTTON_PROMPTS[text]
        remember(chat_id, "user", text)     # so her next message has context
        remember(chat_id, "model", reply)
    else:
        tg("sendChatAction", {"chat_id": chat_id, "action": "typing"})  # shows "typing..." while it thinks
        reply = coach(chat_id, text[:MAX_INPUT])

    try:
        tg("sendMessage", {"chat_id": chat_id, "text": reply})
    except Exception:
        pass  # if Telegram is unreachable there's nothing more we can do

    return "ok"


if __name__ == "__main__":
    app.run(debug=False, port=5000)
