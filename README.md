# Telegram Contact Bot

<b>Chat With Owner Directly Through This Bot.</b>

![Typing SVG](https://readme-typing-svg.herokuapp.com/?lines=Welcome+To+Telegram+Contact+Bot+!)

## Features

- [x] Forward User Messages To Owner/Admins
- [x] Reply To User Directly From Owner/Admin Chat (reply mapping is stored in MongoDB, so old messages stay repliable after a restart and for users who hide their identity)
- [x] Anti-Spam Rate Limit (per user, configurable)
- [x] Restart Bot Anytime (Admin Only)
- [x] Settings Panel (`/settings`): Automatic Replies, Force Join, Statistics
- [x] Automatic Keyword Replies (whole-word match, so `hi` does not fire on `this`)
- [x] Force Join One Or More Channels (normal mode **and** join-request mode, cached checks)
- [x] Permanent Start Message With Image (set in `texts.py`)
- [x] Fast Broadcast: parallel sending, FloodWait handling, preview + confirm, live progress bar and summary, auto-cleanup of users who blocked the bot
- [x] `/broadcast` And `/send <user_id>`: send any message type (text, photo, video, file, sticker, albums) with a preview before sending (same flow as the ChillFlizX file store bot)
- [x] Multi-Admin Support With Permissions (settings / broadcast / manage admins)
- [x] Silent Ban Guard, Bulk `/ban` `/unban` (ids, `.txt` file, or reply), `/banlist` Export
- [x] `/status` And `/info`
- [x] Every Bot Message Is Bold
- [x] 24x7 Uptime With Built-In Web Server

## Commands

```
start - check I'm alive
settings - open the control panel (admin)
broadcast - send any messages to all users, with preview (admin)
send - send messages to ONE user: /send user_id (admin)
ban - ban users: ids, reply to a forwarded message, or .txt file (admin)
unban - unban users (admin)
banlist - download the ban list as a file (admin)
status - users, banned, CPU, RAM, uptime (admin)
info - who sent this message / who is this id (admin)
restart - restart bot (admin only)
```

`/settings` has three buttons: **Automatic Replies**, **Force Join** and **Statistics**.
The `/start` message (image + text) is fixed in `texts.py`; change it there and redeploy.

### Broadcast flow (`/broadcast`)

1. Send `/broadcast`, then send as many messages as you want, one by one (albums included).
2. Send `/done` and the bot shows you a **preview** of everything, in order.
3. Tap **Confirm Broadcast**. A live progress bar and a summary (success / blocked / deleted / failed) are shown.

`/broadcast` as a reply to a message (or album) jumps straight to the preview.
`/cancel` stops the flow at any time. `/send <user_id>` works the same but to one user only.

### Admin permissions

Owners (the `ADMIN` variable, several ids separated by a space) can do everything.
Admins stored in the database keep their permissions (**Settings panel**, **Broadcast**,
**Manage admins**); the owner list is the simplest way to give someone full access.

## Variables

* `API_ID` API Id from my.telegram.org
* `API_HASH` API Hash from my.telegram.org
* `BOT_TOKEN` Bot token from @BotFather
* `ADMIN` Telegram Account Id of Owner (several owners: separate with a space, e.g. `123456 789012`)
* `DATABASE_URI` MongoDB connection string (required — used for users, admins, bans, settings)
* `DATABASE_NAME` MongoDB database name (optional, defaults to `MRNContactBot`)

Optional tuning (all have sensible defaults):

| Variable | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | empty | Enables AI replies when set |
| `RATE_LIMIT_MESSAGES` | `6` | Max messages per user in the window (`0` = no limit) |
| `RATE_LIMIT_SECONDS` | `10` | Length of the rate-limit window |
| `BROADCAST_CONCURRENCY` | `25` | Users messaged at the same time during a broadcast |
| `INDEX_MIN_SCORE` | `1.0` | Minimum match score before an indexed post is sent as the answer (raise it if unrelated posts are sent) |
| `INDEX_MIN_QUERY_CHARS` | `4` | Shorter user texts never trigger an indexed-post answer |
| `MESSAGE_MAP_DAYS` | `30` | How long "which user sent this forwarded message" is remembered |

Force-join channels are managed from `/settings → Force join` (no env var needed). For a
**join-request** channel the bot must be an admin there with the "Invite users" right.

### Railway

`railway.json` is included (start command `python3 bot.py`, health check on `/`, restart on failure).
Set the variables above in Railway and deploy.


## How To Deploy

* Get `API_ID` & `API_HASH` from [my.telegram.org](https://my.telegram.org)
* Get `BOT_TOKEN` from [@BotFather](https://t.me/BotFather)
* Get your `ADMIN` id from [@userinfobot](https://t.me/userinfobot)
* Deploy using any one method below, fill the variables, and you're done.

### Deploy To Heroku

[![Deploy](https://www.herokucdn.com/deploy/button.svg)](https://heroku.com/deploy?template=https://github.com/Steve-Botz/TG-Contact-Bot)

### Deploy To Koyeb

[![Deploy](https://www.koyeb.com/static/images/deploy/button.svg)](https://app.koyeb.com/deploy?type=git&repository=github.com/Steve-Botz/TG-Contact-Bot&branch=main&run_command=python3+bot.py)

### Deploy To Render

[![Deploy](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/Steve-Botz/TG-Contact-Bot)

### Run Cmd
```
python3 bot.py
```
### Deploy Locally

```
git clone https://github.com/Steve-Botz/TG-Contact-Bot
cd TG-Contact-Bot
pip install -r requirements.txt
python3 bot.py
```

## Contact Developer  👨‍💻

[![Contact Developer](https://img.shields.io/badge/Contact-Developer-blue?logo=telegram)](https://t.me/AmaniContactBot)    
[![Telegram Channel](https://img.shields.io/badge/Telegram-Updates%20Channel-blue?logo=telegram)](https://t.me/SteveBotz)  
[![Support Group](https://img.shields.io/badge/Telegram-Support%20Group-blue?logo=telegram)](https://t.me/SteveBotzSupport)

Join My <a href='https://t.me/SteveBotz'>Update Channel</a> For Latest Updates & Features.

---

<div align="center">
<b>⭐ Star this repository if you found it helpful! ⭐</b>
</div>
