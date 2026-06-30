# Cooking Helper (Telegram bot)

A tiny Telegram bot that helps a nervous home cook make food tastier and juicier.
You text it what you're cooking; it texts back simple flavour + moisture tips.
Powered by Google Gemini (free tier). Built to run $0 on Render's free tier.

## Safe to be public
No secrets live in this repo. The Gemini key, Telegram token, and webhook password
are read from environment variables and are only set in Render's dashboard.
`.gitignore` blocks `.env` files so they can't be committed by accident.

## Settings it needs (set these in Render, not in the code)
| Name | What it is |
|------|------------|
| `GEMINI_API_KEY` | Your key from aistudio.google.com |
| `TELEGRAM_TOKEN` | The token @BotFather gave you when you made the bot |
| `WEBHOOK_SECRET`  | Any random password you make up (e.g. 20 random letters/numbers) |

## Deploy (one time)
1. Push this folder to a GitHub repo.
2. On render.com: New > Web Service > connect the repo. It reads `render.yaml`.
3. In the service's **Environment** tab, add the three settings above.
4. Deploy. Note your app URL, e.g. `https://cooking-helper.onrender.com`
5. Connect the bot to that URL by opening this link once in your browser
   (fill in your token, app URL, and secret):
   ```
   https://api.telegram.org/bot<TELEGRAM_TOKEN>/setWebhook?url=https://<your-app>.onrender.com/telegram&secret_token=<WEBHOOK_SECRET>
   ```
   You should see `{"ok":true, ... "description":"Webhook was set"}`.
6. Open your bot in Telegram, send `/start`, and try it.

Note: Render's free tier sleeps after ~15 min idle, so the first message after a
quiet spell takes 30-50 seconds to wake up. Normal for free hosting.
