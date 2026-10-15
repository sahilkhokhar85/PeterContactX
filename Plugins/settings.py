import re
from pyrogram import Client, filters, enums, StopPropagation
from pyrogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from config import OWNERS
from database import (
    admin_filter, admin_perm, get_admins, add_admin, remove_admin, admins_count,
    ban_users, unban_users, get_banned_users, banned_users_count, total_users_count,
    get_auto_replies, add_auto_reply, remove_auto_reply,
    get_force_channel_docs, add_force_channel, remove_force_channel,
    get_start_message, set_start_message_field,
    PERMISSIONS, get_permissions, set_permission, get_broadcast_targets,
)
from Plugins.contact import build_buttons_markup, send_start_message
from Plugins.broadcast import run_broadcast, make_draft_sender, is_running
from Plugins.moderation import ids_from_text

# Who may use what. Owners (ADMIN variable) always pass every filter; admins added
# from the panel get the permissions an owner gave them (User Management → Bot administrators).
settings_filter = admin_perm("can_settings")
bc_filter = admin_perm("can_broadcast")
manage_filter = admin_perm("can_manage_admins")

# Per-admin pending "waiting for text/media input" state.
# pending[admin_id] = {"action": "...", "temp": {...}}
pending = {}

# Per-admin in-progress broadcast draft.
bc_draft = {}


def cancel_btn():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])


# ============================================================
# MAIN MENU
# ============================================================

def main_menu_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔤 Automatic replies", callback_data="ar_menu"),
         InlineKeyboardButton("🔑 Force join", callback_data="fj_menu")],
        [InlineKeyboardButton("👋 Start message", callback_data="sm_menu"),
         InlineKeyboardButton("📬 Broadcast", callback_data="bc_menu")],
        [InlineKeyboardButton("👤 User Management", callback_data="um_menu"),
         InlineKeyboardButton("📊 Statistics", callback_data="stats")]
    ])

@Client.on_message(filters.command(["settings", "menu"]) & filters.private & settings_filter)
async def settings_cmd(client: Client, message: Message):
    pending.pop(message.from_user.id, None)
    await message.reply(
        "**⚙️ Bot settings**\n_Choose one of the available options to customize the bot according to your needs._",
        reply_markup=main_menu_markup()
    )

@Client.on_callback_query(filters.regex("^menu$") & settings_filter)
async def menu_cb(client: Client, query: CallbackQuery):
    pending.pop(query.from_user.id, None)
    await query.message.edit_text(
        "**⚙️ Bot settings**\n_Choose one of the available options to customize the bot according to your needs._",
        reply_markup=main_menu_markup()
    )

@Client.on_callback_query(filters.regex("^cancel$") & settings_filter)
async def cancel_cb(client: Client, query: CallbackQuery):
    pending.pop(query.from_user.id, None)
    await query.answer("Cancelled.")
    await query.message.edit_text(
        "**⚙️ Bot settings**\n_Choose one of the available options to customize the bot according to your needs._",
        reply_markup=main_menu_markup()
    )


# ============================================================
# AUTOMATIC REPLIES
# ============================================================

async def render_ar_menu():
    replies = await get_auto_replies()
    buttons = [[InlineKeyboardButton(f"❌ {r['keyword']}", callback_data=f"ar_del:{r['keyword']}")] for r in replies]
    buttons.append([InlineKeyboardButton("➕ Add keyword", callback_data="ar_add")])
    buttons.append([InlineKeyboardButton("🏠 Menu", callback_data="menu"), InlineKeyboardButton("⬅ Back", callback_data="menu")])
    text = "**🔤 Automatic replies**\n_Send an automatic reply when a user types a keyword._"
    return text, InlineKeyboardMarkup(buttons)

@Client.on_callback_query(filters.regex("^ar_menu$") & settings_filter)
async def ar_menu_cb(client: Client, query: CallbackQuery):
    text, markup = await render_ar_menu()
    await query.message.edit_text(text, reply_markup=markup)

@Client.on_callback_query(filters.regex("^ar_add$") & settings_filter)
async def ar_add_cb(client: Client, query: CallbackQuery):
    pending[query.from_user.id] = {"action": "ar_add_keyword", "temp": {}}
    await query.message.edit_text("**Send me the keyword:**", reply_markup=cancel_btn())

@Client.on_callback_query(filters.regex(r"^ar_del:") & settings_filter)
async def ar_del_cb(client: Client, query: CallbackQuery):
    keyword = query.data.split("ar_del:", 1)[1]
    await remove_auto_reply(keyword)
    await query.answer("Removed.")
    text, markup = await render_ar_menu()
    await query.message.edit_text(text, reply_markup=markup)


# ============================================================
# FORCE JOIN
# ============================================================

async def render_fj_menu():
    channels = await get_force_channel_docs()
    buttons = [
        [InlineKeyboardButton(f"❌ {'📨' if c['mode'] == 'request' else '📢'} {c['chat']}", callback_data=f"fj_del:{c['chat']}")]
        for c in channels
    ]
    buttons.append([InlineKeyboardButton("➕ Add chat", callback_data="fj_add")])
    buttons.append([InlineKeyboardButton("🏠 Menu", callback_data="menu"), InlineKeyboardButton("⬅ Back", callback_data="menu")])
    text = (
        "**🔑 Force join**\n_Forces bot users to subscribe to specific channels._\n\n"
        "📢 = user must be a member   •   📨 = a join request is enough\n"
        "_Tap a chat to remove it._"
    )
    return text, InlineKeyboardMarkup(buttons)

@Client.on_callback_query(filters.regex("^fj_menu$") & settings_filter)
async def fj_menu_cb(client: Client, query: CallbackQuery):
    text, markup = await render_fj_menu()
    await query.message.edit_text(text, reply_markup=markup)

@Client.on_callback_query(filters.regex("^fj_add$") & settings_filter)
async def fj_add_cb(client: Client, query: CallbackQuery):
    pending[query.from_user.id] = {"action": "fj_add", "temp": {}}
    await query.message.edit_text(
        "**Send the channel username (e.g. `@mychannel`) or ID.**\n"
        "_Make sure the bot is an admin in that channel._",
        reply_markup=cancel_btn()
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
                f"❌ **Couldn't create a join-request link:**\n`{e}`\n\n"
                "_Make the bot an admin of that channel with the \"Invite users\" right, then add it again._",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Back", callback_data="fj_menu")]])
            )

    await add_force_channel(temp["chat"], mode=mode, link=link, chat_id=temp["chat_id"])
    pending.pop(query.from_user.id, None)
    await query.answer("Added.")
    text, markup = await render_fj_menu()
    await query.message.edit_text(
        f"✅ Added `{temp['chat']}` ({temp['title'] or 'chat'}) in **{'join request' if mode == 'request' else 'normal'}** mode.\n\n{text}",
        reply_markup=markup
    )

@Client.on_callback_query(filters.regex(r"^fj_del:") & settings_filter)
async def fj_del_cb(client: Client, query: CallbackQuery):
    chat = query.data.split("fj_del:", 1)[1]
    await remove_force_channel(chat)
    await query.answer("Removed.")
    text, markup = await render_fj_menu()
    await query.message.edit_text(text, reply_markup=markup)


# ============================================================
# START MESSAGE
# ============================================================

def sm_menu_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼 Media", callback_data="sm_media"), InlineKeyboardButton("👀 See", callback_data="sm_media_see")],
        [InlineKeyboardButton("🔤 Text", callback_data="sm_text"), InlineKeyboardButton("👀 See", callback_data="sm_text_see")],
        [InlineKeyboardButton("⌨️ Buttons", callback_data="sm_buttons"), InlineKeyboardButton("👀 See", callback_data="sm_buttons_see")],
        [InlineKeyboardButton("👀 Full preview", callback_data="sm_preview")],
        [InlineKeyboardButton("🏠 Menu", callback_data="menu"), InlineKeyboardButton("⬅ Back", callback_data="menu")]
    ])

@Client.on_callback_query(filters.regex("^sm_menu$") & settings_filter)
async def sm_menu_cb(client: Client, query: CallbackQuery):
    await query.message.edit_text(
        "**👋 Start message**\n_In this menu you can set the message sent to users when they start the bot. Press /start to see the result._",
        reply_markup=sm_menu_markup()
    )

@Client.on_callback_query(filters.regex("^sm_media$") & settings_filter)
async def sm_media_cb(client: Client, query: CallbackQuery):
    pending[query.from_user.id] = {"action": "sm_media", "temp": {}}
    await query.message.edit_text("**Send a photo or video to use as the start message media.**", reply_markup=cancel_btn())

@Client.on_callback_query(filters.regex("^sm_media_see$") & settings_filter)
async def sm_media_see_cb(client: Client, query: CallbackQuery):
    doc = await get_start_message()
    await query.answer(f"Current media: {doc.get('media_type') or 'none'}", show_alert=True)

@Client.on_callback_query(filters.regex("^sm_text$") & settings_filter)
async def sm_text_cb(client: Client, query: CallbackQuery):
    pending[query.from_user.id] = {"action": "sm_text", "temp": {}}
    await query.message.edit_text(
        "**Send the new start message text.**\n_Use `{mention}` to insert the user's mention._",
        reply_markup=cancel_btn()
    )

@Client.on_callback_query(filters.regex("^sm_text_see$") & settings_filter)
async def sm_text_see_cb(client: Client, query: CallbackQuery):
    doc = await get_start_message()
    await query.message.reply(f"<b>Current start text:</b>\n\n{doc.get('text', '')}", parse_mode=enums.ParseMode.HTML)
    await query.answer()

@Client.on_callback_query(filters.regex("^sm_buttons$") & settings_filter)
async def sm_buttons_cb(client: Client, query: CallbackQuery):
    pending[query.from_user.id] = {"action": "sm_buttons", "temp": {}}
    await query.message.edit_text(
        "**Send the buttons.**\n"
        "_Format: `Text - URL` per row, use `|` to put two buttons in one row._\n"
        "Example:\n`Join Channel - https://t.me/Mrn_Officialx`\n`Yes - https://t.me/a | No - https://t.me/b`",
        reply_markup=cancel_btn()
    )

@Client.on_callback_query(filters.regex("^sm_buttons_see$") & settings_filter)
async def sm_buttons_see_cb(client: Client, query: CallbackQuery):
    doc = await get_start_message()
    rows = doc.get("buttons") or []
    if not rows:
        return await query.answer("No buttons set.", show_alert=True)
    lines = " | ".join(b["text"] for row in rows for b in row)
    await query.answer(lines[:200], show_alert=True)

@Client.on_callback_query(filters.regex("^sm_preview$") & settings_filter)
async def sm_preview_cb(client: Client, query: CallbackQuery):
    await send_start_message(client, query.message.chat.id, query.from_user.mention)
    await query.answer()


def parse_buttons_text(text: str):
    rows = []
    for line in text.strip().splitlines():
        if not line.strip():
            continue
        row = []
        for part in line.split("|"):
            if " - " not in part:
                continue
            btn_text, url = part.split(" - ", 1)  # split on first ' - ' only, so hyphens inside the URL (e.g. invite links) are safe
            btn_text, url = btn_text.strip(), url.strip()
            if btn_text and url:
                row.append({"text": btn_text, "url": url})
        if row:
            rows.append(row)
    return rows


# ============================================================
# BROADCAST
# ============================================================

def get_draft(admin_id):
    return bc_draft.setdefault(admin_id, {"media_type": None, "media_file_id": None, "text": None, "buttons": [], "pin": False})

def bc_menu_markup(admin_id):
    d = get_draft(admin_id)
    pin_label = "📌 Pin: YES" if d["pin"] else "📌 Pin: NO"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼 Media", callback_data="bc_media"), InlineKeyboardButton("👀 See", callback_data="bc_media_see")],
        [InlineKeyboardButton("🔤 Text", callback_data="bc_text"), InlineKeyboardButton("👀 See", callback_data="bc_text_see")],
        [InlineKeyboardButton("⌨️ Buttons", callback_data="bc_buttons"), InlineKeyboardButton("👀 See", callback_data="bc_buttons_see")],
        [InlineKeyboardButton(pin_label, callback_data="bc_pin")],
        [InlineKeyboardButton("👀 Full preview", callback_data="bc_preview")],
        [InlineKeyboardButton("⬅ Back", callback_data="menu"), InlineKeyboardButton("✅ Send", callback_data="bc_send")]
    ])

@Client.on_callback_query(filters.regex("^bc_menu$") & bc_filter)
async def bc_menu_cb(client: Client, query: CallbackQuery):
    await query.message.edit_text(
        "**📬 Broadcast**\n_Send a message to all bot users simultaneously._",
        reply_markup=bc_menu_markup(query.from_user.id)
    )

@Client.on_callback_query(filters.regex("^bc_media$") & bc_filter)
async def bc_media_cb(client: Client, query: CallbackQuery):
    pending[query.from_user.id] = {"action": "bc_media", "temp": {}}
    await query.message.edit_text("**Send a photo or video for the broadcast.**", reply_markup=cancel_btn())

@Client.on_callback_query(filters.regex("^bc_media_see$") & bc_filter)
async def bc_media_see_cb(client: Client, query: CallbackQuery):
    d = get_draft(query.from_user.id)
    await query.answer(f"Media: {d['media_type'] or 'none'}", show_alert=True)

@Client.on_callback_query(filters.regex("^bc_text$") & bc_filter)
async def bc_text_cb(client: Client, query: CallbackQuery):
    pending[query.from_user.id] = {"action": "bc_text", "temp": {}}
    await query.message.edit_text(
        "**Send the broadcast text/caption.**\n_Use `{mention}` to insert each user's name._",
        reply_markup=cancel_btn()
    )

@Client.on_callback_query(filters.regex("^bc_text_see$") & bc_filter)
async def bc_text_see_cb(client: Client, query: CallbackQuery):
    d = get_draft(query.from_user.id)
    await query.message.reply(f"<b>Current broadcast text:</b>\n\n{d['text'] or '<i>empty</i>'}", parse_mode=enums.ParseMode.HTML)
    await query.answer()

@Client.on_callback_query(filters.regex("^bc_buttons$") & bc_filter)
async def bc_buttons_cb(client: Client, query: CallbackQuery):
    pending[query.from_user.id] = {"action": "bc_buttons", "temp": {}}
    await query.message.edit_text(
        "**Send the buttons.**\n_Format: `Text - URL` per row, `|` for same row._",
        reply_markup=cancel_btn()
    )

@Client.on_callback_query(filters.regex("^bc_buttons_see$") & bc_filter)
async def bc_buttons_see_cb(client: Client, query: CallbackQuery):
    d = get_draft(query.from_user.id)
    if not d["buttons"]:
        return await query.answer("No buttons set.", show_alert=True)
    lines = " | ".join(b["text"] for row in d["buttons"] for b in row)
    await query.answer(lines[:200], show_alert=True)

@Client.on_callback_query(filters.regex("^bc_pin$") & bc_filter)
async def bc_pin_cb(client: Client, query: CallbackQuery):
    d = get_draft(query.from_user.id)
    d["pin"] = not d["pin"]
    await query.message.edit_reply_markup(bc_menu_markup(query.from_user.id))
    await query.answer()

@Client.on_callback_query(filters.regex("^bc_preview$") & bc_filter)
async def bc_preview_cb(client: Client, query: CallbackQuery):
    d = get_draft(query.from_user.id)
    if not d["text"] and not d["media_file_id"]:
        return await query.answer("Nothing to preview yet.", show_alert=True)
    markup = build_buttons_markup(d["buttons"])
    text = (d["text"] or "").replace("{mention}", query.from_user.mention)
    if d["media_type"] == "photo":
        await client.send_photo(query.message.chat.id, d["media_file_id"], caption=text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
    elif d["media_type"] == "video":
        await client.send_video(query.message.chat.id, d["media_file_id"], caption=text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
    else:
        await client.send_message(query.message.chat.id, text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
    await query.answer()

@Client.on_callback_query(filters.regex("^bc_send$") & bc_filter)
async def bc_send_cb(client: Client, query: CallbackQuery):
    d = get_draft(query.from_user.id)
    if not d["text"] and not d["media_file_id"]:
        return await query.answer("Nothing to send. Add text or media first.", show_alert=True)

    if is_running():
        return await query.answer("A broadcast is already running. Wait for it to finish.", show_alert=True)

    # One extra tap so a broadcast can never be sent by accident.
    total = len(await get_broadcast_targets())
    await query.message.edit_text(
        f"**📢 Send this broadcast to `{total}` users?**\n_Use 👀 Full preview first if you haven't checked it._",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes, send now", callback_data="bc_go")],
            [InlineKeyboardButton("⬅ Back to draft", callback_data="bc_menu")]
        ])
    )

@Client.on_callback_query(filters.regex("^bc_go$") & bc_filter)
async def bc_go_cb(client: Client, query: CallbackQuery):
    d = get_draft(query.from_user.id)
    if not d["text"] and not d["media_file_id"]:
        return await query.answer("Nothing to send. Add text or media first.", show_alert=True)
    if is_running():
        return await query.answer("A broadcast is already running. Wait for it to finish.", show_alert=True)

    await query.answer("Broadcasting...")
    draft = dict(d)                                  # snapshot: edits made while it runs don't change what is sent
    bc_draft.pop(query.from_user.id, None)
    targets = await get_broadcast_targets()
    await run_broadcast(client, query.message, targets, make_draft_sender(client, draft))


# ============================================================
# USER MANAGEMENT
# ============================================================

def um_menu_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👮 Bot administrators", callback_data="um_admins")],
        [InlineKeyboardButton("🚫 Banned users", callback_data="um_banned")],
        [InlineKeyboardButton("⬅ Back", callback_data="menu")]
    ])

@Client.on_callback_query(filters.regex("^um_menu$") & settings_filter)
async def um_menu_cb(client: Client, query: CallbackQuery):
    await query.message.edit_text(
        "**👤 User Management**",
        reply_markup=um_menu_markup()
    )

async def render_um_admins():
    admins = [uid for uid in await get_admins() if uid not in OWNERS]
    buttons = [[InlineKeyboardButton(f"👑 {owner} (owner)", callback_data="noop")] for owner in OWNERS]
    for uid in admins:
        buttons.append([
            InlineKeyboardButton(f"⚙️ {uid}", callback_data=f"um_adm:{uid}"),
            InlineKeyboardButton("❌", callback_data=f"um_admins_del:{uid}")
        ])
    buttons.append([InlineKeyboardButton("➕ Add administrator", callback_data="um_admins_add")])
    buttons.append([InlineKeyboardButton("🏠 Menu", callback_data="menu"), InlineKeyboardButton("⬅ Back", callback_data="um_menu")])
    text = (
        "**👮 Bot administrators**\n"
        "_Administrators receive and reply to user messages. Tap ⚙️ to choose what each one may do, ❌ to remove._"
    )
    return text, InlineKeyboardMarkup(buttons)

async def render_admin_perms(uid: int):
    perms = await get_permissions(uid)
    buttons = [
        [InlineKeyboardButton(f"{'✅' if perms[p] else '⬜'} {label}", callback_data=f"um_perm:{uid}:{p}")]
        for p, label in PERMISSIONS.items()
    ]
    buttons.append([InlineKeyboardButton("⬅ Back", callback_data="um_admins")])
    text = f"**⚙️ Permissions for** `{uid}`\n_Tap to switch on/off. Owners always have everything._"
    return text, InlineKeyboardMarkup(buttons)

@Client.on_callback_query(filters.regex(r"^um_adm:\d+$") & manage_filter)
async def um_adm_cb(client: Client, query: CallbackQuery):
    uid = int(query.data.split(":", 1)[1])
    text, markup = await render_admin_perms(uid)
    await query.message.edit_text(text, reply_markup=markup)

@Client.on_callback_query(filters.regex(r"^um_perm:\d+:\w+$") & manage_filter)
async def um_perm_cb(client: Client, query: CallbackQuery):
    _, uid, perm = query.data.split(":")
    uid = int(uid)
    if perm not in PERMISSIONS:
        return await query.answer("Unknown permission.", show_alert=True)
    current = (await get_permissions(uid))[perm]
    await set_permission(uid, perm, not current)
    await query.answer("Updated.")
    text, markup = await render_admin_perms(uid)
    await query.message.edit_text(text, reply_markup=markup)

@Client.on_callback_query(filters.regex("^um_admins$") & settings_filter)
async def um_admins_cb(client: Client, query: CallbackQuery):
    text, markup = await render_um_admins()
    await query.message.edit_text(text, reply_markup=markup)

@Client.on_callback_query(filters.regex("^noop$"))
async def noop_cb(client: Client, query: CallbackQuery):
    await query.answer("Owners cannot be removed or limited.", show_alert=True)

@Client.on_callback_query(filters.regex("^um_admins_add$") & manage_filter)
async def um_admins_add_cb(client: Client, query: CallbackQuery):
    pending[query.from_user.id] = {"action": "um_admins_add", "temp": {}}
    await query.message.edit_text("**Send the user ID to make administrator:**", reply_markup=cancel_btn())

@Client.on_callback_query(filters.regex(r"^um_admins_del:") & manage_filter)
async def um_admins_del_cb(client: Client, query: CallbackQuery):
    uid = int(query.data.split(":", 1)[1])
    await remove_admin(uid)
    await query.answer("Removed.")
    text, markup = await render_um_admins()
    await query.message.edit_text(text, reply_markup=markup)

def um_banned_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚫 Ban a user", callback_data="um_ban")],
        [InlineKeyboardButton("🟢 Unban a user", callback_data="um_unban")],
        [InlineKeyboardButton("📋 List of banned users", callback_data="um_banned_list")],
        [InlineKeyboardButton("🏠 Menu", callback_data="menu"), InlineKeyboardButton("⬅ Back", callback_data="um_menu")]
    ])

@Client.on_callback_query(filters.regex("^um_banned$") & settings_filter)
async def um_banned_cb(client: Client, query: CallbackQuery):
    await query.message.edit_text(
        "**🚫 Banned users**\n_In this menu you can manage banned users._\n\n"
        "Shortcuts: reply to a forwarded message with /ban or /unban, `/ban id1 id2 ...`, /banlist (file), /info.",
        reply_markup=um_banned_markup()
    )

@Client.on_callback_query(filters.regex("^um_ban$") & settings_filter)
async def um_ban_cb(client: Client, query: CallbackQuery):
    pending[query.from_user.id] = {"action": "um_ban", "temp": {}}
    await query.message.edit_text("**Send the user ID(s) to ban** (several ids allowed):", reply_markup=cancel_btn())

@Client.on_callback_query(filters.regex("^um_unban$") & settings_filter)
async def um_unban_cb(client: Client, query: CallbackQuery):
    pending[query.from_user.id] = {"action": "um_unban", "temp": {}}
    await query.message.edit_text("**Send the user ID(s) to unban** (several ids allowed):", reply_markup=cancel_btn())

@Client.on_callback_query(filters.regex("^um_banned_list$") & settings_filter)
async def um_banned_list_cb(client: Client, query: CallbackQuery):
    banned = await get_banned_users()
    if not banned:
        return await query.answer("No banned users.", show_alert=True)
    if len(banned) > 100:
        return await query.answer(f"{len(banned)} banned users - use /banlist to get them as a file.", show_alert=True)
    text = "**🚫 Banned Users:**\n\n" + "\n".join(f"`{uid}`" for uid in banned)
    await query.message.reply(text)
    await query.answer()


# ============================================================
# STATISTICS
# ============================================================

@Client.on_callback_query(filters.regex("^stats$") & settings_filter)
async def stats_cb(client: Client, query: CallbackQuery):
    total = await total_users_count()
    banned = await banned_users_count()
    admins = await admins_count()
    await query.message.edit_text(
        f"**📊 Statistics**\n\n"
        f"• Total users: `{total}`\n"
        f"• Banned users: `{banned}`\n"
        f"• Administrators: `{admins}`",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Back", callback_data="menu")]])
    )


# ============================================================
# PENDING TEXT / MEDIA INPUT HANDLER (runs before other handlers)
# ============================================================

@Client.on_message(filters.private & admin_filter & (filters.text | filters.photo | filters.video), group=-1)
async def pending_input_handler(client: Client, message: Message):
    admin_id = message.from_user.id
    state = pending.get(admin_id)
    if not state:
        return  # let normal handlers process this message

    # An admin command (/settings, /ban ...) while the panel waits for input cancels that
    # input and runs as a command - it must never be saved as a keyword/text/button.
    if message.text and message.text.startswith("/"):
        pending.pop(admin_id, None)
        return

    action = state["action"]
    temp = state["temp"]

    # ---------- Automatic replies ----------
    if action == "ar_add_keyword":
        if not message.text:
            return await message.reply("Please send text.", reply_markup=cancel_btn())
        temp["keyword"] = message.text.strip()
        pending[admin_id] = {"action": "ar_add_reply", "temp": temp}
        await message.reply("**Now send the reply text for this keyword:**", reply_markup=cancel_btn())

    elif action == "ar_add_reply":
        if not message.text:
            return await message.reply("Please send text.", reply_markup=cancel_btn())
        await add_auto_reply(temp["keyword"], message.text.html)
        pending.pop(admin_id, None)
        text, markup = await render_ar_menu()
        await message.reply(f"✅ Auto-reply added for keyword `{temp['keyword']}`.")
        await message.reply(text, reply_markup=markup)

    # ---------- Force join ----------
    elif action == "fj_add":
        if not message.text:
            return await message.reply("Please send text.", reply_markup=cancel_btn())
        chat = message.text.strip()
        target = int(chat) if re.fullmatch(r"-?\d+", chat) else chat
        try:
            info = await client.get_chat(target)
        except Exception as e:
            return await message.reply(
                f"❌ **Couldn't find that chat:**\n`{e}`\n\n"
                "_Check the @username / ID and make sure the bot is an admin there. Send it again or cancel._",
                reply_markup=cancel_btn()
            )
        pending[admin_id] = {"action": "fj_mode", "temp": {"chat": chat, "chat_id": info.id, "title": info.title}}
        await message.reply(
            f"**{info.title or chat}** found.\n\nHow should users join it?\n"
            "📢 **Normal**: they must be a member.\n"
            "📨 **Join request**: for channels with \"approve new members\"; sending the request is enough, no approval needed.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Normal", callback_data="fj_mode:normal"),
                 InlineKeyboardButton("📨 Join request", callback_data="fj_mode:request")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
            ])
        )

    elif action == "fj_mode":
        return await message.reply("Please choose how users join using the buttons above, or cancel.", reply_markup=cancel_btn())

    # ---------- Start message ----------
    elif action == "sm_media":
        if message.photo:
            await set_start_message_field("media_type", "photo")
            await set_start_message_field("media_file_id", message.photo.file_id)
        elif message.video:
            await set_start_message_field("media_type", "video")
            await set_start_message_field("media_file_id", message.video.file_id)
        else:
            return await message.reply("Please send a photo or video.", reply_markup=cancel_btn())
        pending.pop(admin_id, None)
        await message.reply("✅ Start message media updated.", reply_markup=sm_menu_markup())

    elif action == "sm_text":
        if not message.text:
            return await message.reply("Please send text.", reply_markup=cancel_btn())
        await set_start_message_field("text", message.text.html)
        pending.pop(admin_id, None)
        await message.reply("✅ Start message text updated.", reply_markup=sm_menu_markup())

    elif action == "sm_buttons":
        if not message.text:
            return await message.reply("Please send text.", reply_markup=cancel_btn())
        rows = parse_buttons_text(message.text)
        await set_start_message_field("buttons", rows)
        pending.pop(admin_id, None)
        await message.reply("✅ Start message buttons updated.", reply_markup=sm_menu_markup())

    # ---------- Broadcast ----------
    elif action == "bc_media":
        d = get_draft(admin_id)
        if message.photo:
            d["media_type"] = "photo"
            d["media_file_id"] = message.photo.file_id
        elif message.video:
            d["media_type"] = "video"
            d["media_file_id"] = message.video.file_id
        else:
            return await message.reply("Please send a photo or video.", reply_markup=cancel_btn())
        pending.pop(admin_id, None)
        await message.reply("✅ Broadcast media set.", reply_markup=bc_menu_markup(admin_id))

    elif action == "bc_text":
        if not message.text:
            return await message.reply("Please send text.", reply_markup=cancel_btn())
        get_draft(admin_id)["text"] = message.text.html
        pending.pop(admin_id, None)
        await message.reply("✅ Broadcast text set.", reply_markup=bc_menu_markup(admin_id))

    elif action == "bc_buttons":
        if not message.text:
            return await message.reply("Please send text.", reply_markup=cancel_btn())
        get_draft(admin_id)["buttons"] = parse_buttons_text(message.text)
        pending.pop(admin_id, None)
        await message.reply("✅ Broadcast buttons set.", reply_markup=bc_menu_markup(admin_id))

    # ---------- User management ----------
    elif action == "um_admins_add":
        try:
            uid = int(message.text.strip())
        except (ValueError, AttributeError):
            return await message.reply("❌ Invalid user ID. Send a numeric ID.", reply_markup=cancel_btn())
        await add_admin(uid)
        pending.pop(admin_id, None)
        text, markup = await render_um_admins()
        await message.reply(f"✅ `{uid}` is now a bot administrator.")
        await message.reply(text, reply_markup=markup)

    elif action == "um_ban":
        ids = ids_from_text(message.text)
        if not ids:
            return await message.reply("❌ Invalid user ID. Send one or more numeric IDs.", reply_markup=cancel_btn())
        protected = set(OWNERS) | set(await get_admins())
        skipped = ids & protected
        ids -= protected
        new = await ban_users(ids) if ids else 0
        pending.pop(admin_id, None)
        note = f"\nSkipped admins: `{len(skipped)}`" if skipped else ""
        await message.reply(f"✅ Banned `{new}` new id(s).{note}", reply_markup=um_banned_markup())

    elif action == "um_unban":
        ids = ids_from_text(message.text)
        if not ids:
            return await message.reply("❌ Invalid user ID. Send one or more numeric IDs.", reply_markup=cancel_btn())
        removed = await unban_users(ids)
        pending.pop(admin_id, None)
        await message.reply(f"✅ Unbanned `{removed}` id(s).", reply_markup=um_banned_markup())

    raise StopPropagation
