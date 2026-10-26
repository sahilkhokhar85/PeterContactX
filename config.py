import os
import re

API_ID = int(os.environ.get("API_ID", ""))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# ADMIN = owner Telegram id. You can give more than one owner, separated by a
# space or comma ("123 456"). The first id is the "main" owner (gets the start
# notification). Owners can never be banned or removed from the panel.
_raw_admin = os.environ.get("ADMIN", "")
OWNERS = [int(x) for x in re.split(r"[\s,]+", _raw_admin.strip()) if re.fullmatch(r"-?\d+", x)]
if not OWNERS:
    raise ValueError("ADMIN variable is missing or invalid (set your numeric Telegram id)")
ADMIN = OWNERS[0]

# Optional log channel: id of a private channel where the bot records every message,
# edit, reply, ban and broadcast (with name / id / username). The bot must be an admin there.
_raw_log = os.environ.get("LOG_CHANNEL", "").strip()
LOG_CHANNEL = int(_raw_log) if re.fullmatch(r"-?\d+", _raw_log) else (_raw_log or 0)

DATABASE_URI = os.environ.get("DATABASE_URI", "")
DATABASE_NAME = os.environ.get("DATABASE_NAME", "MRNContactBot")

# ---------------- Anti-spam (per user) ----------------
# A user may send at most RATE_LIMIT_MESSAGES messages in RATE_LIMIT_SECONDS.
# Extra messages are ignored (the user gets one "slow down" notice per window).
RATE_LIMIT_MESSAGES = int(os.environ.get("RATE_LIMIT_MESSAGES", "6"))
RATE_LIMIT_SECONDS = int(os.environ.get("RATE_LIMIT_SECONDS", "10"))

# ---------------- Broadcast ----------------
# How many users are messaged at the same time (Telegram limits are per chat,
# so many different users in parallel is fine).
BROADCAST_CONCURRENCY = int(os.environ.get("BROADCAST_CONCURRENCY", "25"))

# How long (days) the "which user sent this forwarded message" mapping is kept.
# Needed so you can reply to old messages even after a restart.
MESSAGE_MAP_DAYS = int(os.environ.get("MESSAGE_MAP_DAYS", "30"))

# ---------------- Optional: a user account that only LOOKS UP names ----------------
# Pyrogram session string of a normal Telegram account. The bot uses it ONLY to find the name
# of users it has never seen (for example in the banned users list). Leave empty to switch it off.
# It is a full login of that account: keep it in the .env file only, never share it or put it on GitHub.
SESSION_STRING = os.environ.get("SESSION_STRING", "").strip()
