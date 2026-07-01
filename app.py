import os
import re
import json
import urllib.request
import urllib.parse
from collections import defaultdict
from flask import Flask, request, abort
from google import genai
from google.genai import types

app = Flask(__name__)

# All secrets come from the environment - nothing sensitive lives in this file.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")  # must match the secret_token set on the webhook
PEXELS_KEY = os.environ.get("PEXELS_API_KEY")      # optional; if unset, the bot just skips photos

MODEL = "gemini-2.5-flash"          # free-tier model; change here if it ever stops being free
MAX_INPUT = 300                     # ponytail: one message is short; cap stops giant pastes
MAX_TURNS = 8                       # remember last 8 messages (~4 back-and-forths) for follow-ups
TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

SYSTEM_PROMPT = """You are Chef Tan, a calm, practical cooking coach helping a nervous home cook on her phone.
Her food often comes out bland and dry. Your job is to give steps that reliably work.

Before you answer, think it through carefully and check yourself: are the steps correct, in the right order, and will they actually fix flavour and moisture for THIS food? Reason about WHY each tip works (the cooking reason) so your advice is sound. Keep this thinking to yourself - do not write it out.

Get straight to the point. Skip small talk and praise. Do NOT explain the reasons in your reply unless she asks "why" - then explain simply. Stay kind, just brief.

You are chatting back and forth, so use what was said earlier. She might:
- Name a dish she is cooking -> help her make THAT tasty and juicy.
- List ingredients she has -> suggest ONE simple, beginner-friendly dish.
- Ask for an easy idea -> give ONE simple, cheap, hard-to-mess-up dish.
- Raise a worry like "it's still dry" -> answer it simply, using the earlier chat.

Always cover her two problems:
1. FLAVOUR - season well (salt early, aromatics like garlic/ginger/onion, a sauce, a squeeze of acid like lime or vinegar at the end).
2. MOISTURE - stop it drying out (right heat, don't overcook, a quick marinade, rest meat a few minutes after cooking).

Answer format (plain text only - no markdown, no asterisks, no hash symbols):
- A few short numbered steps.
- Every step gives a simple time and heat, for example "medium heat, 3 to 4 minutes each side". Never say "until done" without a rough time.
- End with a line starting "Try next:" giving one or two quick suggestions (a variation, a side, or a small upgrade).
- After the Try next line, add ONE final line for the system only: PHOTO: two to four words naming the finished dish (example: PHOTO: chicken fried rice). Only add this when you gave cooking steps for a dish - never for quick follow-up answers or non-cooking replies. She will not see this line.
- Do NOT add any closing disclaimer; that line is added separately.
- If she asks something not about cooking, briefly say you only help with cooking and ask what she is making."""

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


def dish_photo(query):
    """Fetch one real photo of the dish from Pexels (free). Returns an image URL, or None."""
    if not PEXELS_KEY:
        return None
    try:
        url = "https://api.pexels.com/v1/search?" + urllib.parse.urlencode(
            {"query": query, "per_page": 1, "orientation": "landscape"})
        req = urllib.request.Request(url, headers={"Authorization": PEXELS_KEY})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.load(r)
        photos = data.get("photos") or []
        return photos[0]["src"]["large"] if photos else None
    except Exception:
        return None


def coach(chat_id, question):
    """Run the message (with recent chat history) through Gemini.
    Returns (reply_text, photo_query) where photo_query may be None."""
    if client is None:
        return "The bot isn't set up yet (missing API key). Let Yong Han know.", None
    contents = HISTORY[chat_id] + [{"role": "user", "parts": [{"text": question}]}]
    try:
        resp = client.models.generate_content(
            model=MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.7,
                # Thinking ON so it reasons and self-checks before answering. Budget is
                # counted inside max_output_tokens, so keep max well above the budget or
                # the visible answer gets starved and cut short.
                max_output_tokens=2048,
                thinking_config=types.ThinkingConfig(thinking_budget=1024),
            ),
        )
        text = clean(resp.text or "")
        if not text:
            return "I couldn't think of an answer for that one. Try saying it a different way.", None
        # Pull out the hidden "PHOTO: ..." search term and strip it from what she sees.
        photo_query = None
        m = re.search(r"(?im)^\s*PHOTO\s*[:\-]\s*(.+)$", text)
        if m:
            photo_query = m.group(1).strip()
            text = re.sub(r"(?im)^\s*PHOTO\s*[:\-].*$", "", text).strip()
        remember(chat_id, "user", question)
        remember(chat_id, "model", text)
        return text + "\n\n" + TAGLINE, photo_query
    except Exception:
        # Never show mum a stack trace. Quota hits, network drops, bad key all land here.
        return "Something went wrong. Please wait a moment and try again.", None


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

    photo_query = None
    if low.startswith("/idea"):
        tg("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        reply, photo_query = coach(chat_id, "Please give me one easy dish idea to cook.")
    elif text in BUTTON_PROMPTS:
        reply = BUTTON_PROMPTS[text]
        remember(chat_id, "user", text)     # so her next message has context
        remember(chat_id, "model", reply)
    else:
        tg("sendChatAction", {"chat_id": chat_id, "action": "typing"})  # shows "typing..." while it thinks
        reply, photo_query = coach(chat_id, text[:MAX_INPUT])

    try:
        tg("sendMessage", {"chat_id": chat_id, "text": reply})
    except Exception:
        pass  # if Telegram is unreachable there's nothing more we can do

    # A gentle photo of the finished dish, if we have a search term and a Pexels key.
    if photo_query:
        img = dish_photo(photo_query)
        if img:
            try:
                tg("sendPhoto", {"chat_id": chat_id, "photo": img,
                                 "caption": "A rough idea of how it looks - yours doesn't need to match!"})
            except Exception:
                pass

    return "ok"


if __name__ == "__main__":
    app.run(debug=False, port=5000)
