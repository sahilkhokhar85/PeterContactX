"""/settings panel: Automatic replies, Force join, Statistics.

(Start message is permanent in texts.py, broadcast is done with /broadcast, bans with /ban.)
Every message of the panel is bold; ids / numbers stay monospace.
"""
import html
import re

from pyrogram import Client, filters, enums, StopPropagation
from pyrogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)

from database import (
    admin_filter, admin_perm, admins_count, banned_users_count, total_users_count,
    get_auto_replies, add_auto_reply, remove_auto_reply,
    get_force_channel_docs, add_force_channel, remove_force_channel,
)

HTML = enums.ParseMode.HTML

# Only admins with the "settings" permission (owners always) may use the panel.
settings_filter = admin_perm("can_settings")

# Per-admin pending "waiting for text input" state.
# pending[admin_id] = {"action": "...", "temp": {...}}
pending = {}

MAIN_TEXT = (
    "<b>⚙️ Control Panel\n\n"
    "Manage Your Contact Bot From Here.\n"
    "Choose An Option Below 👇</b>"
)


def cancel_btn():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])


# ============================================================
# MAIN MENU
# ============================================================

def main_menu_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔤 Automatic Replies", callback_data="ar_menu"),
         InlineKeyboardButton("🔑 Force Join", callback_data="fj_menu")],
        [InlineKeyboardButton("📊 Statistics", callback_data="stats")]
    ])

@Client.on_message(filters.command(["settings", "menu"]) & filters.private & settings_filter)
async def settings_cmd(client: Client, message: Message):
    pending.pop(message.from_user.id, None)
    await message.reply(MAIN_TEXT, reply_markup=main_menu_markup(), parse_mode=HTML)

@Client.on_callback_query(filters.regex("^menu$") & settings_filter)
async def menu_cb(client: Client, query: CallbackQuery):
    pending.pop(query.from_user.id, None)
    await query.message.edit_text(MAIN_TEXT, reply_markup=main_menu_markup(), parse_mode=HTML)

@Client.on_callback_query(filters.regex("^cancel$") & settings_filter)
async def cancel_cb(client: Client, query: CallbackQuery):
    pending.pop(query.from_user.id, None)
    await query.answer("Cancelled.")
    await query.message.edit_text(MAIN_TEXT, reply_markup=main_menu_markup(), parse_mode=HTML)


# ============================================================
# AUTOMATIC REPLIES
# ============================================================

async def render_ar_menu():
    replies = await get_auto_replies()
    buttons = [[InlineKeyboardButton(f"❌ {r['keyword']}", callback_data=f"ar_del:{r['keyword']}")] for r in replies]
    buttons.append([InlineKeyboardButton("➕ Add Keyword", callback_data="ar_add")])
    buttons.append([InlineKeyboardButton("⬅ Back", callback_data="menu")])
    text = (
        "<b>🔤 Automatic Replies\n\n"
        "Send An Automatic Reply When A User Types A Keyword.\n"
        "Tap A Keyword To Remove It.</b>"
    )
    return text, InlineKeyboardMarkup(buttons)

@Client.on_callback_query(filters.regex("^ar_menu$") & settings_filter)
async def ar_menu_cb(client: Client, query: CallbackQuery):
    text, markup = await render_ar_menu()
    await query.message.edit_text(text, reply_markup=markup, parse_mode=HTML)

@Client.on_callback_query(filters.regex("^ar_add$") & settings_filter)
async def ar_add_cb(client: Client, query: CallbackQuery):
    pending[query.from_user.id] = {"action": "ar_add_keyword", "temp": {}}
    await query.message.edit_text("<b>Send Me The Keyword:</b>", reply_markup=cancel_btn(), parse_mode=HTML)

@Client.on_callback_query(filters.regex(r"^ar_del:") & settings_filter)
async def ar_del_cb(client: Client, query: CallbackQuery):
    keyword = query.data.split("ar_del:", 1)[1]
    await remove_auto_reply(keyword)
    await query.answer("Removed.")
    text, markup = await render_ar_menu()
    await query.message.edit_text(text, reply_markup=markup, parse_mode=HTML)


# ============================================================
# FORCE JOIN
# ============================================================

async def render_fj_menu():
    channels = await get_force_channel_docs()
    buttons = [
        [InlineKeyboardButton(f"❌ {'📨' if c['mode'] == 'request' else '📢'} {c['chat']}", callback_data=f"fj_del:{c['chat']}")]
        for c in channels
    ]
    buttons.append([InlineKeyboardButton("➕ Add Chat", callback_data="fj_add")])
    buttons.append([InlineKeyboardButton("⬅ Back", callback_data="menu")])
    text = (
        "<b>🔑 Force Join\n\n"
        "Users Must Join These Chats To Use The Bot.\n"
        "📢 = Must Be A Member • 📨 = Join Request Is Enough\n"
        "Tap A Chat To Remove It.</b>"
    )
    return text, InlineKeyboardMarkup(buttons)

@Client.on_callback_query(filters.regex("^fj_menu$") & settings_filter)
async def fj_menu_cb(client: Client, query: CallbackQuery):
    text, markup = await render_fj_menu()
    await query.message.edit_text(text, reply_markup=markup, parse_mode=HTML)

@Client.on_callback_query(filters.regex("^fj_add$") & settings_filter)
async def fj_add_cb(client: Client, query: CallbackQuery):
    pending[query.from_user.id] = {"action": "fj_add", "temp": {}}
    await query.message.edit_text(
        "<b>Send The Channel Username (e.g.</b> <code>@mychannel</code><b>) Or ID.\n"
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
            return await query.message.edit_text(
                f"<b>❌ Couldn't Create A Join-Request Link:</b>\n<code>{html.escape(str(e))}</code>\n\n"
                "<b>Make The Bot An Admin Of That Channel With The \"Invite Users\" Right, Then Add It Again.</b>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Back", callback_data="fj_menu")]]),
                parse_mode=HTML
            )

    await add_force_channel(temp["chat"], mode=mode, link=link, chat_id=temp["chat_id"])
    pending.pop(query.from_user.id, None)
    await query.answer("Added.")
    text, markup = await render_fj_menu()
    title = html.escape(temp["title"] or "chat")
    await query.message.edit_text(
        f"<b>✅ Added</b> <code>{html.escape(temp['chat'])}</code> <b>({title}) In "
        f"{'Join Request' if mode == 'request' else 'Normal'} Mode.</b>\n\n{text}",
        reply_markup=markup, parse_mode=HTML
    )

@Client.on_callback_query(filters.regex(r"^fj_del:") & settings_filter)
async def fj_del_cb(client: Client, query: CallbackQuery):
    chat = query.data.split("fj_del:", 1)[1]
    await remove_force_channel(chat)
    await query.answer("Removed.")
    text, markup = await render_fj_menu()
    await query.message.edit_text(text, reply_markup=markup, parse_mode=HTML)


# ============================================================
# STATISTICS
# ============================================================

@Client.on_callback_query(filters.regex("^stats$") & settings_filter)
async def stats_cb(client: Client, query: CallbackQuery):
    total = await total_users_count()
    banned = await banned_users_count()
    admins = await admins_count()
    await query.message.edit_text(
        "<b>📊 Statistics</b>\n\n"
        f"<b>• Total Users:</b> <code>{total}</code>\n"
        f"<b>• Banned Users:</b> <code>{banned}</code>\n"
        f"<b>• Administrators:</b> <code>{admins}</code>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Back", callback_data="menu")]]),
        parse_mode=HTML
    )


# ============================================================
# PENDING TEXT INPUT HANDLER (runs before other handlers)
# ============================================================

@Client.on_message(filters.private & admin_filter & filters.text, group=-1)
async def pending_input_handler(client: Client, message: Message):
    admin_id = message.from_user.id
    state = pending.get(admin_id)
    if not state:
        return  # let normal handlers process this message

    # An admin command (/settings, /ban ...) while the panel waits for input cancels that
    # input and runs as a command - it must never be saved as a keyword/reply.
    if message.text.startswith("/"):
        pending.pop(admin_id, None)
        return

    action = state["action"]
    temp = state["temp"]

    # ---------- Automatic replies ----------
    if action == "ar_add_keyword":
        temp["keyword"] = message.text.strip()
        pending[admin_id] = {"action": "ar_add_reply", "temp": temp}
        await message.reply("<b>Now Send The Reply Text For This Keyword:</b>", reply_markup=cancel_btn(), parse_mode=HTML)

    elif action == "ar_add_reply":
        await add_auto_reply(temp["keyword"], message.text.html)
        pending.pop(admin_id, None)
        text, markup = await render_ar_menu()
        await message.reply(
            f"<b>✅ Auto-Reply Added For Keyword</b> <code>{html.escape(temp['keyword'])}</code><b>.</b>",
            parse_mode=HTML
        )
        await message.reply(text, reply_markup=markup, parse_mode=HTML)

    # ---------- Force join ----------
    elif action == "fj_add":
        chat = message.text.strip()
        target = int(chat) if re.fullmatch(r"-?\d+", chat) else chat
        try:
            info = await client.get_chat(target)
        except Exception as e:
            return await message.reply(
                f"<b>❌ Couldn't Find That Chat:</b>\n<code>{html.escape(str(e))}</code>\n\n"
                "<b>Check The @username / ID And Make Sure The Bot Is An Admin There. Send It Again Or Cancel.</b>",
                reply_markup=cancel_btn(), parse_mode=HTML
            )
        pending[admin_id] = {"action": "fj_mode", "temp": {"chat": chat, "chat_id": info.id, "title": info.title}}
        await message.reply(
            f"<b>{html.escape(info.title or chat)} Found.\n\nHow Should Users Join It?\n"
            "📢 Normal: They Must Be A Member.\n"
            "📨 Join Request: For Channels With \"Approve New Members\"; Sending The Request Is Enough, No Approval Needed.</b>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Normal", callback_data="fj_mode:normal"),
                 InlineKeyboardButton("📨 Join Request", callback_data="fj_mode:request")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
            ]),
            parse_mode=HTML
        )

    elif action == "fj_mode":
        return await message.reply(
            "<b>Please Choose How Users Join Using The Buttons Above, Or Cancel.</b>",
            reply_markup=cancel_btn(), parse_mode=HTML
        )

    raise StopPropagation
