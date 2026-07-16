# Google Calendar setup

One-time, free, no billing account required.

1. Go to https://console.cloud.google.com/ and create a project (any name).
2. APIs & Services → Library → search "Google Calendar API" → Enable.
3. APIs & Services → OAuth consent screen:
   - User type: **External**
   - Fill in app name and your email. Skip scopes.
   - Under **Test users**, add your own Gmail address.
   - Leave it in **Testing** status. Publishing is not needed for personal use.
4. APIs & Services → Credentials → Create Credentials → **OAuth client ID**
   - Application type: **Desktop app**
   - Download the JSON, save it as `credentials.json` in the repo root.
5. Run the auth flow once on your **laptop** (it opens a browser):

       .venv\Scripts\python -c "from pathlib import Path; from jarvis.gcal import build_service; build_service(Path('credentials.json'), Path('token.json'))"

6. Copy the generated `token.json` to the Pi. The Pi is headless and cannot run
   the browser flow itself.

`credentials.json` and `token.json` are gitignored. Never commit them.

**Note:** apps in Testing status get refresh tokens that expire after 7 days.
If Jarvis stops syncing after a week, either re-run step 5, or set the consent
screen to **In production** (still free, and no verification is required for
personal use with sensitive scopes you own).
