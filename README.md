<p align="center">
  <img src="https://files.catbox.moe/glbqmw.png" alt="Contact Bot" width="320">
</p>

<h1 align="center">💬 Telegram Contact Bot</h1>

<p align="center">
  <img src="https://readme-typing-svg.herokuapp.com/?lines=Welcome+To+The+Contact+Bot;Talk+To+The+Owner+Directly;Replies+%7C+Edits+%7C+Broadcast+%7C+Log+Channel;Thank+You!&center=true&width=560" alt="Typing SVG">
</p>

---

## ✨ What This Bot Can Do

<details>
<summary><b>👉 Tap to open the feature list</b></summary>

<br>

| | Feature |
|---|---|
| 💬 | Users message the bot, the owner and admins get it and reply from the bot chat |
| ✏️ | Edits are synced: an edited reply updates the user's message, an edited user message is sent again marked as edited |
| 🖼 | Permanent start message with a picture (`texts.py`) |
| 🔤 | Automatic keyword replies |
| 📢 | Force Join (normal mode + join request mode) |
| 📣 | Fast broadcast with preview and `/send` to one user |
| 🚫 | Ban / unban users, bot status and user info |
| 🗒 | Log channel: every message with name, id and username, even for users who hide their profile |
| 🛡 | Anti-spam rate limit |
| 👥 | Settings panel (`/settings`) and multiple admins with permissions |

</details>

## 🔧 Environment Variables

> ⚠️ `API_HASH`, `BOT_TOKEN` and `DATABASE_URI` have no default value. Set them in your host's variables and never commit them.

<details>
<summary><b>👉 Tap to open the variable list</b></summary>

<br>

| Variable | Meaning |
|---|---|
| `API_ID` | From [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | From [my.telegram.org](https://my.telegram.org) |
| `BOT_TOKEN` | From [BotFather](https://t.me/BotFather) |
| `ADMIN` | Owner id, several owners separated by a space |
| `DATABASE_URI` | MongoDB connection URL |
| `DATABASE_NAME` | MongoDB database name (optional, default `MRNContactBot`) |
| `LOG_CHANNEL` | Log channel id (starts with `-100`), the bot must be an admin there. Optional |
| `RATE_LIMIT_MESSAGES` | Max messages per user in the window (default `6`, `0` = no limit) |
| `RATE_LIMIT_SECONDS` | Length of that window (default `10`) |
| `BROADCAST_CONCURRENCY` | Users messaged at the same time in a broadcast (default `25`) |
| `MESSAGE_MAP_DAYS` | Days that replies and edits keep working for old messages (default `30`) |

Force Join channels are added from `/settings`, no variable needed.

</details>

## 🤖 Commands

<details>
<summary><b>👉 Tap to open the command list</b></summary>

<br>

| Command | What it does |
|---|---|
| `/start` | Check that the bot is alive |
| `/settings` | Control panel: Automatic Replies, Force Join, Statistics |
| `/broadcast` | Send messages to all users: send any number of messages, then `/done`, check the preview and confirm. Reply to a message with it to skip ahead |
| `/send` | `/send user_id`: send messages to one user only |
| `/ban` `/unban` | Ban or unban users: `/ban id1 id2 id3`, reply to a forwarded message, or send a `.txt` file of ids |
| `/banlist` | Download the ban list as a `.txt` file |
| `/status` | Users, banned users, CPU, RAM and uptime |
| `/info` | Who sent a message: reply to a forwarded message, or `/info user_id` |
| `/restart` | Restart the bot |

</details>

## 💝 Credits

<details>
<summary><b>👉 Tap to see the credits</b></summary>

<br>

- 🧩 Based on an open-source contact bot released under the MIT License (see `LICENSE`)
- ❤️ Customised and maintained for the **ChillFlizX** community

</details>

---

<p align="center">© ChillFlizX. All rights reserved.</p>
