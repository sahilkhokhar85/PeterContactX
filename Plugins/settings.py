"""/settings panel: Automatic replies, Force join, Statistics, Restart, Users management (Plugins/users_panel.py).

(Start message is permanent in texts.py, broadcast is done with /broadcast, bans with /ban.)
Every message of the panel is bold; ids / numbers stay monospace.
"""
import asyncio
import html
import os
import re
import sys

from pyrogram import Client, filters, enums, StopPropagation
from pyrogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)

from texts import SETTINGS_IMAGE
from logchannel import log_event, log_now, clip
from database import (
    admin_filter, admin_perm, admins_count, banned_users_count, total_users_count,
    get_auto_replies, add_auto_reply, remove_auto_reply,
    get_force_channel_docs, add_force_channel, remove_force_channel, move_force_channel,
)

HTML = enums.ParseMode.HTML

# Only admins with the "settings" permission (owners always) may use the panel.
settings_filter = admin_perm("can_settings")

# Per-admin pending "waiting for text input" state.
# pending[admin_id] = {"action": "...", "temp": {...}}
pending = {}

MAIN_TEXT = (
    "<b>⚙️ Control Panel !!</b>\n\n"
    "<b>👇 Choose An Option Below !!</b>"
)


def _no_urls(markup):
    """Same keyboard with link buttons turned into plain ones (used if Telegram refuses a link button)."""
    if not markup:
        return markup
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(b.text, callback_data="noop") if getattr(b, "url", None) else b for b in row]
        for row in markup.inline_keyboard
    ])


def _button_refused(e) -> bool:
    return "BUTTON" in (type(e).__name__ + str(e)).upper()


async def edit(query: CallbackQuery, text: str, reply_markup=None, parse_mode=HTML):
    """Edit the panel message: every panel message is a photo (edit its caption), older plain ones are edited as text."""
    msg = query.message

    async def go(markup):
        if msg.photo:
            return await msg.edit_caption(text, reply_markup=markup, parse_mode=parse_mode)
        return await msg.edit_text(text, reply_markup=markup, parse_mode=parse_mode)

    try:
        return await go(reply_markup)
    except Exception as e:
        if "NOTMODIFIED" in (type(e).__name__ + str(e)).upper().replace("_", ""):
            return None   # same screen tapped again
        if reply_markup and _button_refused(e):
            return await go(_no_urls(reply_markup))
        raise


async def send_panel(client: Client, message: Message, text: str, reply_markup=None):
    """Send a panel screen as a NEW message, with the panel image on top (so the image never disappears)."""
    chat_id = message.chat.id
    for markup in ([reply_markup, _no_urls(reply_markup)] if reply_markup else [None]):
        try:
            return await client.send_photo(chat_id, SETTINGS_IMAGE, caption=text, reply_markup=markup, parse_mode=HTML)
        except Exception as e:
            if markup is reply_markup and reply_markup and _button_refused(e):
                continue
            break
    return await client.send_message(chat_id, text, parse_mode=HTML, reply_markup=reply_markup and _no_urls(reply_markup))


def cancel_btn():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])


# ============================================================
# MAIN MENU
# ============================================================

def main_menu_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔤 Automatic Replies", callback_data="ar_menu"),
         InlineKeyboardButton("🔑 Force Join", callback_data="fj_menu")],
        [InlineKeyboardButton("📊 Statistics", callback_data="stats"),
         InlineKeyboardButton("⏱ Restart Bot", callback_data="rs_ask")],
        [InlineKeyboardButton("👤 Users Management", callback_data="um_menu")],
    ])

@Client.on_message(filters.command(["settings", "menu"]) & filters.private & settings_filter)
async def settings_cmd(client: Client, message: Message):
    pending.pop(message.from_user.id, None)
    log_event(client, "⚙️ Settings Panel Opened", by=message.from_user)
    await send_panel(client, message, MAIN_TEXT, reply_markup=main_menu_markup())

@Client.on_callback_query(filters.regex("^noop$") & settings_filter)
async def noop_cb(client: Client, query: CallbackQuery):
    await query.answer()

@Client.on_callback_query(filters.regex("^menu$") & settings_filter)
async def menu_cb(client: Client, query: CallbackQuery):
    pending.pop(query.from_user.id, None)
    await edit(query, MAIN_TEXT, reply_markup=main_menu_markup(), parse_mode=HTML)

@Client.on_callback_query(filters.regex("^cancel$") & settings_filter)
async def cancel_cb(client: Client, query: CallbackQuery):
    pending.pop(query.from_user.id, None)
    await query.answer("Cancelled.")
    await edit(query, MAIN_TEXT, reply_markup=main_menu_markup(), parse_mode=HTML)


# ============================================================
# RESTART (asks first, so a stray tap never restarts the bot)
# ============================================================

@Client.on_callback_query(filters.regex("^rs_ask$") & settings_filter)
async def restart_ask_cb(client: Client, query: CallbackQuery):
    await edit(
        query,
        "<b>🔄 Restart The Bot?\n\nIt Is Back In A Few Seconds.</b>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes, Restart", callback_data="rs_yes")],
            [InlineKeyboardButton("⬅ Back", callback_data="menu")],
        ]),
        parse_mode=HTML,
    )

@Client.on_callback_query(filters.regex("^rs_yes$") & settings_filter)
async def restart_yes_cb(client: Client, query: CallbackQuery):
    await query.answer("Restarting...")
    await edit(query, "<b>🔄 Restarting...</b>", reply_markup=None, parse_mode=HTML)
    await log_now(client, f"<b>🔄 Bot Restarting</b>\n\n<b>👮 By:</b> <code>{query.from_user.id}</code>")
    await asyncio.sleep(2)
    os.execl(sys.executable, sys.executable, *sys.argv)


# ============================================================
# AUTOMATIC REPLIES
# ============================================================

async def render_ar_menu():
    replies = await get_auto_replies()
    buttons = [[InlineKeyboardButton(f"❌ {r['keyword']}", callback_data=f"ar_del:{r['keyword']}")] for r in replies]
    buttons.append([InlineKeyboardButton("➕ Add Keyword", callback_data="ar_add")])
    buttons.append([InlineKeyboardButton("⬅ Back", callback_data="menu")])
    text = (
        "<b>🔤 Automatic Replies !!\n\n"
        "💬 Replies When A Keyword Is Typed\n\n"
        "👇 Tap A Keyword To Remove It.</b>"
    )
    return text, InlineKeyboardMarkup(buttons)

@Client.on_callback_query(filters.regex("^ar_menu$") & settings_filter)
async def ar_menu_cb(client: Client, query: CallbackQuery):
    text, markup = await render_ar_menu()
    await edit(query, text, reply_markup=markup, parse_mode=HTML)

@Client.on_callback_query(filters.regex("^ar_add$") & settings_filter)
async def ar_add_cb(client: Client, query: CallbackQuery):
    pending[query.from_user.id] = {"action": "ar_add_keyword", "temp": {}}
    await edit(query, "<b>Send Me The Keyword:</b>", reply_markup=cancel_btn(), parse_mode=HTML)

@Client.on_callback_query(filters.regex(r"^ar_del:") & settings_filter)
async def ar_del_cb(client: Client, query: CallbackQuery):
    keyword = query.data.split("ar_del:", 1)[1]
    await remove_auto_reply(keyword)
    log_event(client, "🗑 Auto Reply Removed", by=query.from_user, lines=[f"<b>🔑 Keyword:</b> <code>{html.escape(keyword)}</code>"])
    await query.answer("Removed.")
    text, markup = await render_ar_menu()
    await edit(query, text, reply_markup=markup, parse_mode=HTML)


# ============================================================
# FORCE JOIN
# ============================================================

def _clean_chat(raw):
    """What the admin typed -> (chat text kept in the database, target for the API), or (None, None) when it
    can't be a chat. Accepts @username, username, t.me/username links, t.me/c/ID/.. links and numeric ids."""
    parts = (raw or "").split()
    text = parts[0] if parts else ""
    link = re.match(r"(?:https?://)?(?:t|telegram)\.me/(.+)", text, re.I)
    if link:
        part = link.group(1).split("?")[0].strip("/")
        pieces = part.split("/")
        if pieces[0] == "c" and len(pieces) > 1 and pieces[1].isdigit():
            number = int("-100" + pieces[1])
            return str(number), number
        if part.startswith("+") or part.lower().startswith("joinchat"):
            return None, None   # an invite link can't be looked up by the bot
        text = pieces[0]
    text = text.lstrip("@")
    if re.fullmatch(r"-?\d+", text):
        number = int(text)
        return str(number), number
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", text):
        return "@" + text, "@" + text
    return None, None

def _chat_url(c):
    """A link that opens the chat when its button is tapped (None when there is no usable one)."""
    if c.get("link"):
        return c["link"]
    chat = c["chat"]
    if chat.startswith("@"):
        return f"https://t.me/{chat[1:]}"
    cid = str(c.get("chat_id") or chat)
    if cid.startswith("-100"):
        return f"https://t.me/c/{cid[4:]}/1"
    return None

def _short(text, n=18):
    text = str(text)
    return text if len(text) <= n else text[:n - 1] + "…"

def num_emoji(n: int) -> str:
    """12 -> 1️⃣2️⃣"""
    return "".join(d + "\ufe0f\u20e3" for d in str(n))

def _is_public(c) -> bool:
    return str(c["chat"]).startswith("@")

async def render_fj_menu():
    channels = await get_force_channel_docs()
    buttons = [[InlineKeyboardButton("➕ Add Chat", callback_data="fj_add")]]
    lines = []
    for i, c in enumerate(channels, 1):
        title = html.escape(_short(c.get("title") or c["chat"], 35))
        url = _chat_url(c)
        shown = f'<a href="{html.escape(url)}">{title}</a>' if url else title
        icon = "📢" if _is_public(c) else "🔒"
        lines.append(f"{num_emoji(i)}{icon} {shown}" + (" 📨" if c["mode"] == "request" else ""))
        number = num_emoji(i)
        buttons.append([
            InlineKeyboardButton("❌", callback_data=f"fj_del:{c['chat']}"),
            InlineKeyboardButton(number, url=url) if url else InlineKeyboardButton(number, callback_data="noop"),
            InlineKeyboardButton("🔺", callback_data=f"fj_up:{c['chat']}"),
            InlineKeyboardButton("🔻", callback_data=f"fj_dn:{c['chat']}"),
        ])
    buttons.append([InlineKeyboardButton("⬅ Back", callback_data="menu")])
    listing = "\n".join(lines) if lines else "No Chats Added Yet."
    text = (
        "<b>🔑 Force Join !!\n\n"
        f"{listing}\n\n"
        "🔒 Private · 📢 Public · 📨 Join Request\n"
        "❌ Remove · 🔺 Up · 🔻 Down</b>"
    )
    return text, InlineKeyboardMarkup(buttons)

@Client.on_callback_query(filters.regex("^fj_menu$") & settings_filter)
async def fj_menu_cb(client: Client, query: CallbackQuery):
    text, markup = await render_fj_menu()
    await edit(query, text, reply_markup=markup, parse_mode=HTML)

@Client.on_callback_query(filters.regex("^fj_add$") & settings_filter)
async def fj_add_cb(client: Client, query: CallbackQuery):
    pending[query.from_user.id] = {"action": "fj_add", "temp": {}}
    await edit(query, 
        "<b>Send The Channel Username (e.g.</b> <code>@mychannel</code><b>) Or ID, Or Forward Any Post From It.\n"
        "Make Sure The Bot Is An Admin In That Channel.</b>",
        reply_markup=cancel_btn(), parse_mode=HTML
    )

@Client.on_callback_query(filters.regex(r"^fj_mode:(normal|request)$") & settings_filter)
async def fj_mode_cb(client: Client, query: CallbackQuery):
    state = pending.get(query.from_user.id)
    if not state or state.get("action") != "fj_mode":
        return await query.answer("This request has expired. Add the chat again.", show_alert=True)
    mode = query.data.split(":", 1)[1]
    temp = state["temp"]

    link = None
    if mode == "request":
        # Create the "approve new members" invite link now: it also proves the bot has the right to do so.
        try:
            invite = await client.create_chat_invite_link(temp["chat_id"], creates_join_request=True, name="Contact bot force join")
            link = invite.invite_link
        except Exception as e:
            return await edit(query, 
                f"<b>❌ Couldn't Create A Join-Request Link:</b>\n<code>{html.escape(str(e))}</code>\n\n"
                "<b>Make The Bot An Admin Of That Channel With The \"Invite Users\" Right, Then Add It Again.</b>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Back", callback_data="fj_menu")]]),
                parse_mode=HTML
            )

    await add_force_channel(temp["chat"], mode=mode, link=link, chat_id=temp["chat_id"], title=temp["title"])
    pending.pop(query.from_user.id, None)
    log_event(client, "➕ Force Join Chat Added", by=query.from_user, lines=[
        f"<b>📢 Chat:</b> {html.escape(str(temp['title'] or temp['chat']))} (<code>{html.escape(str(temp['chat']))}</code>)",
        f"<b>Mode:</b> {'Join Request' if mode == 'request' else 'Normal'}",
    ])
    await query.answer("Added.")
    text, markup = await render_fj_menu()
    title = html.escape(temp["title"] or "chat")
    await edit(query, 
        f"<b>✅ Added</b> <code>{html.escape(temp['chat'])}</code> <b>({title}) In "
        f"{'Join Request' if mode == 'request' else 'Normal'} Mode.</b>\n\n{text}",
        reply_markup=markup, parse_mode=HTML
    )

async def _fj_label(chat):
    for c in await get_force_channel_docs():
        if c["chat"] == chat:
            return c.get("title") or chat
    return chat

@Client.on_callback_query(filters.regex(r"^fj_del:") & settings_filter)
async def fj_del_cb(client: Client, query: CallbackQuery):
    """Asks before removing."""
    chat = query.data.split("fj_del:", 1)[1]
    await query.answer()
    await edit(
        query,
        f"<b>⚠️ Remove</b> <code>{html.escape(str(await _fj_label(chat)))}</code> <b>From Force Join?</b>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes, Remove", callback_data=f"fj_dely:{chat}")],
            [InlineKeyboardButton("⬅ Back", callback_data="fj_menu")],
        ]),
        parse_mode=HTML,
    )

@Client.on_callback_query(filters.regex(r"^fj_dely:") & settings_filter)
async def fj_del_yes_cb(client: Client, query: CallbackQuery):
    chat = query.data.split("fj_dely:", 1)[1]
    await remove_force_channel(chat)
    log_event(client, "➖ Force Join Chat Removed", by=query.from_user, lines=[f"<b>📢 Chat:</b> <code>{html.escape(chat)}</code>"])
    await query.answer("Removed.")
    text, markup = await render_fj_menu()
    await edit(query, text, reply_markup=markup, parse_mode=HTML)

@Client.on_callback_query(filters.regex(r"^fj_(up|dn):") & settings_filter)
async def fj_move_cb(client: Client, query: CallbackQuery):
    kind, chat = query.data.split(":", 1)
    moved = await move_force_channel(chat, -1 if kind == "fj_up" else 1)
    if not moved:
        return await query.answer("Already At The Top." if kind == "fj_up" else "Already At The Bottom.")
    await query.answer("Moved.")
    text, markup = await render_fj_menu()
    await edit(query, text, reply_markup=markup, parse_mode=HTML)


# ============================================================
# STATISTICS
# ============================================================

@Client.on_callback_query(filters.regex("^stats$") & settings_filter)
async def stats_cb(client: Client, query: CallbackQuery):
    total = await total_users_count()
    banned = await banned_users_count()
    admins = await admins_count()
    await edit(query, 
        "<b>📊 Statistics !!</b>\n\n"
        f"<b>👤 Total Users:</b> <code>{total}</code>\n"
        f"<b>🚫 Banned Users:</b> <code>{banned}</code>\n"
        f"<b>👮 Administrators:</b> <code>{admins}</code>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Back", callback_data="menu")]]),
        parse_mode=HTML
    )


# ============================================================
# PENDING TEXT INPUT HANDLER (runs before other handlers)
# ============================================================

@Client.on_message(filters.private & admin_filter & (filters.text | filters.forwarded), group=-1)
async def pending_input_handler(client: Client, message: Message):
    admin_id = message.from_user.id
    state = pending.get(admin_id)
    if not state:
        return  # let normal handlers process this message

    action = state["action"]
    temp = state["temp"]

    # only "add a force join chat" understands a forwarded channel post; anything without text is not input here
    forwarded_ok = (
        (action == "fj_add" and getattr(message, "forward_from_chat", None))
        or (action in ("adm_add", "ban_add", "unban_add") and getattr(message, "forward_from", None))
    )
    if not message.text and not forwarded_ok:
        return

    # An admin command (/settings, /ban ...) while the panel waits for input cancels that
    # input and runs as a command - it must never be saved as a keyword/reply.
    if message.text and message.text.startswith("/"):
        pending.pop(admin_id, None)
        return

    # ---------- Automatic replies ----------
    if action == "ar_add_keyword":
        temp["keyword"] = message.text.strip()
        pending[admin_id] = {"action": "ar_add_reply", "temp": temp}
        await send_panel(client, message, "<b>Now Send The Reply Text For This Keyword:</b>", reply_markup=cancel_btn())

    elif action == "ar_add_reply":
        await add_auto_reply(temp["keyword"], message.text.html)
        pending.pop(admin_id, None)
        log_event(client, "➕ Auto Reply Added", by=message.from_user, lines=[
            f"<b>🔑 Keyword:</b> <code>{html.escape(temp['keyword'])}</code>",
            f"<b>💬 Reply:</b> {clip(message.text)}",
        ])
        text, markup = await render_ar_menu()
        await send_panel(
            client, message,
            f"<b>✅ Auto-Reply Added For Keyword</b> <code>{html.escape(temp['keyword'])}</code><b>.</b>\n\n{text}",
            reply_markup=markup
        )

    # ---------- Force join ----------
    elif action == "fj_add":
        source = getattr(message, "forward_from_chat", None)
        if source is not None:   # a post forwarded from the channel: take the channel from it
            target = source.id
            chat = f"@{source.username}" if getattr(source, "username", None) else str(source.id)
        else:
            chat, target = _clean_chat(message.text)
        if chat is None:
            return await send_panel(
                client, message,
                "<b>❌ That Isn't A Usable Channel.</b>\n\n"
                "<b>Send The</b> <code>@username</code><b>, A</b> <code>t.me/username</code> <b>Link, Or The Channel ID "
                "(Starts With</b> <code>-100</code><b>).\n"
                "A Private Invite Link (</b><code>t.me/+...</code><b>) Can't Be Used, Send The Channel ID Instead.</b>",
                reply_markup=cancel_btn()
            )
        try:
            info = await client.get_chat(target)
        except Exception as e:
            return await send_panel(
                client, message,
                f"<b>❌ Couldn't Find That Chat:</b>\n<code>{html.escape(str(e))}</code>\n\n"
                "<b>Check The @username / ID And Make Sure The Bot Is An Admin There. Send It Again Or Cancel.</b>",
                reply_markup=cancel_btn()
            )
        if getattr(info, "username", None):
            # A public channel has no join requests, so there is nothing to ask: it is added as Normal.
            chat = f"@{info.username}"
            await add_force_channel(chat, mode="normal", link=None, chat_id=info.id, title=info.title)
            pending.pop(admin_id, None)
            log_event(client, "➕ Force Join Chat Added", by=message.from_user, lines=[
                f"<b>📢 Chat:</b> {html.escape(str(info.title or chat))} (<code>{html.escape(chat)}</code>)",
                "<b>Mode:</b> Normal",
            ])
            text, markup = await render_fj_menu()
            await send_panel(
                client, message,
                f"<b>✅ Added</b> <code>{html.escape(chat)}</code> <b>({html.escape(info.title or 'chat')}) In Normal Mode.</b>\n\n{text}",
                reply_markup=markup
            )
            raise StopPropagation
        pending[admin_id] = {"action": "fj_mode", "temp": {"chat": chat, "chat_id": info.id, "title": info.title}}
        await send_panel(
            client, message,
            f"<b>{html.escape(info.title or chat)} Found.\n\nHow Should Users Join It?\n"
            "📢 Normal: They Must Be A Member.\n"
            "📨 Join Request: For Channels With \"Approve New Members\"; Sending The Request Is Enough, No Approval Needed.</b>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Normal", callback_data="fj_mode:normal"),
                 InlineKeyboardButton("📨 Join Request", callback_data="fj_mode:request")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
            ])
        )

    elif action in ("adm_add", "ban_add", "unban_add"):
        from Plugins import users_panel   # imported here: users_panel itself imports this module
        await users_panel.handle_input(client, message, action)

    elif action == "fj_mode":
        return await send_panel(
            client, message,
            "<b>Please Choose How Users Join Using The Buttons Above, Or Cancel.</b>",
            reply_markup=cancel_btn()
        )

    raise StopPropagation
