"""👤 Users Management inside the /settings panel: Bot Administrators and Banned Users.

Every screen is a message with the panel image; names are clickable (they open the profile).
Adding / removing administrators needs the "Manage admins" right (owners always have it).
"""
import asyncio
import html
import re

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config import OWNERS
from database import (
    admin_perm, add_admin, remove_admin, get_admins, is_admin, is_owner, get_user,
    find_user_by_username, ban_users, unban_users, get_banned_users, banned_users_count,
)
from logchannel import log_event, log_text
from Plugins.moderation import _ids_log
from Plugins.settings import edit, send_panel, pending, settings_filter, HTML

PAGE = 8
manage_filter = admin_perm("can_manage_admins")

_names = {}   # user id -> name shown on the screens
_missed = set()   # ids Telegram doesn't know: not asked again


def num_emoji(n: int) -> str:
    """12 -> 1️⃣2️⃣"""
    return "".join(d + "️⃣" for d in str(n))


def _mention(uid: int, name: str) -> str:
    return f'<a href="tg://user?id={uid}">{html.escape(_short(name, 25))}</a>'


def _short(text, n=18):
    text = str(text)
    return text if len(text) <= n else text[:n - 1] + "…"


async def _name(client, uid: int, fetch: bool = True) -> str:
    if uid in _names:
        return _names[uid]
    name = None
    doc = await get_user(uid)
    if doc and doc.get("first_name"):
        name = doc["first_name"]
    if not name and fetch and uid not in _missed:
        try:
            u = await client.get_users(uid)
            name = " ".join(x for x in (u.first_name, u.last_name) if x)
        except Exception:
            name = None
    if name:
        if len(_names) > 2000:
            _names.clear()
        _names[uid] = name
    elif fetch:
        if len(_missed) > 5000:
            _missed.clear()
        _missed.add(uid)
    return name or "User"


def _nav(prefix, page, total, back):
    """Prev / Next row (only the buttons that make sense) + Menu and Back row."""
    rows = []
    pages = max(1, -(-total // PAGE))
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"{prefix}:{page - 1}"))
    if page < pages - 1:
        row.append(InlineKeyboardButton("Next ➡️", callback_data=f"{prefix}:{page + 1}"))
    if row:
        rows.append(row)
    rows.append(menu_back(back))
    return rows


def menu_back(back):
    return [InlineKeyboardButton("🏡 Menu", callback_data="menu"), InlineKeyboardButton("⬅ Back", callback_data=back)]


# ============================================================
# USERS MANAGEMENT MENU
# ============================================================

UM_TEXT = "<b>👤 Users Management !!\n\n👇 Choose An Option Below !!</b>"


def um_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧑‍💻 Bot Administrators", callback_data="adm_list:0")],
        [InlineKeyboardButton("🚷 Banned Users", callback_data="bn_menu")],
        [InlineKeyboardButton("⬅ Back", callback_data="menu")],
    ])


@Client.on_callback_query(filters.regex("^um_menu$") & settings_filter)
async def um_menu_cb(client: Client, query: CallbackQuery):
    pending.pop(query.from_user.id, None)
    await query.answer()
    await edit(query, UM_TEXT, reply_markup=um_markup())


# ============================================================
# BOT ADMINISTRATORS
# ============================================================

ADM_HEAD = (
    "👮‍♂️ Bot Administrators !!\n\n"
    "Bot Administrators Can Manage The Bot Settings. Additionally, They Receive User Messages And Can Respond To Them."
)


async def _all_admins() -> list:
    ids = list(OWNERS)
    for a in await get_admins():
        if a not in ids:
            ids.append(a)
    return ids


async def render_admins(client, page: int = 0):
    ids = await _all_admins()
    pages = max(1, -(-len(ids) // PAGE))
    page = min(max(page, 0), pages - 1)
    chunk = ids[page * PAGE:(page + 1) * PAGE]
    lines, rows = [], [[InlineKeyboardButton("➕ Add Administrator", callback_data="adm_add")]]
    for i, uid in enumerate(chunk, page * PAGE + 1):
        name = await _name(client, uid)
        lines.append(f"{num_emoji(i)} {_mention(uid, name)} [<code>{uid}</code>]")
        first = (InlineKeyboardButton("👑", callback_data=f"adm_own:{uid}") if is_owner(uid)
                 else InlineKeyboardButton("❌", callback_data=f"adm_del:{uid}"))
        rows.append([first, InlineKeyboardButton(f"👤 {_short(name)}", url=f"tg://user?id={uid}")])
    rows += _nav("adm_list", page, len(ids), "um_menu")
    text = f"<b>{ADM_HEAD}\n\n" + "\n".join(lines) + "</b>"
    return text, InlineKeyboardMarkup(rows)


@Client.on_callback_query(filters.regex(r"^adm_list:\d+$") & manage_filter)
async def adm_list_cb(client: Client, query: CallbackQuery):
    pending.pop(query.from_user.id, None)
    await query.answer()
    text, markup = await render_admins(client, int(query.data.split(":")[1]))
    await edit(query, text, reply_markup=markup)


@Client.on_callback_query(filters.regex("^adm_own:") & manage_filter)
async def adm_owner_cb(client: Client, query: CallbackQuery):
    await query.answer("An Owner Can't Be Removed.", show_alert=True)


@Client.on_callback_query(filters.regex("^adm_add$") & manage_filter)
async def adm_add_cb(client: Client, query: CallbackQuery):
    pending[query.from_user.id] = {"action": "adm_add", "temp": {}}
    await query.answer()
    await edit(
        query,
        "<b>➕ Add Administrator\n\n"
        "👉🏻 Send The ID Or Username Of The User. The User Must Have Already Started The Bot.\n\n"
        "💡 Or Forward Any Message From That User.</b>",
        reply_markup=InlineKeyboardMarkup([menu_back("adm_list:0")]),
    )


@Client.on_callback_query(filters.regex(r"^adm_del:\d+$") & manage_filter)
async def adm_del_cb(client: Client, query: CallbackQuery):
    """Asks before removing."""
    uid = int(query.data.split(":")[1])
    await query.answer()
    name = await _name(client, uid)
    await edit(
        query,
        f"<b>⚠️ Remove</b> {_mention(uid, name)} [<code>{uid}</code>] <b>From Administrators?</b>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes, Remove", callback_data=f"adm_dely:{uid}")],
            [InlineKeyboardButton("⬅ Back", callback_data="adm_list:0")],
        ]),
    )


@Client.on_callback_query(filters.regex(r"^adm_dely:\d+$") & manage_filter)
async def adm_del_yes_cb(client: Client, query: CallbackQuery):
    uid = int(query.data.split(":")[1])
    if is_owner(uid):
        return await query.answer("An Owner Can't Be Removed.", show_alert=True)
    await remove_admin(uid)
    log_event(client, "➖ Administrator Removed", uid=uid, by=query.from_user)
    await query.answer("Removed.")
    text, markup = await render_admins(client, 0)
    await edit(query, text, reply_markup=markup)


@Client.on_callback_query(filters.regex(r"^adm_") & settings_filter)
async def adm_no_permission(client: Client, query: CallbackQuery):
    """Last in line: an admin without the "Manage admins" right ends up here."""
    await query.answer("Only Admins With The Manage Admins Right Can Do This.", show_alert=True)


# ============================================================
# BANNED USERS
# ============================================================

BN_TEXT = "<b>🚷 Banned Users !!\n\nIn This Menu You Can Manage Banned Users.</b>"


def bn_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚷 Ban A User", callback_data="bn_ban"),
         InlineKeyboardButton("✅ Unban A User", callback_data="bn_unban")],
        [InlineKeyboardButton("📃 List Of Banned Users", callback_data="bl_list:0")],
        menu_back("um_menu"),
    ])


@Client.on_callback_query(filters.regex("^bn_menu$") & settings_filter)
async def bn_menu_cb(client: Client, query: CallbackQuery):
    pending.pop(query.from_user.id, None)
    await query.answer()
    await edit(query, BN_TEXT, reply_markup=bn_markup())


@Client.on_callback_query(filters.regex("^bn_(ban|unban)$") & settings_filter)
async def bn_ask_cb(client: Client, query: CallbackQuery):
    ban = query.data == "bn_ban"
    pending[query.from_user.id] = {"action": "ban_add" if ban else "unban_add", "temp": {}}
    await query.answer()
    if ban:
        text = ("<b>🚷 Ban A User\n\n"
                "👉🏻 Send The ID Or Username Of The User To Be Banned. The User Must Have Already Started The Bot.\n\n"
                "💡 Or Use /ban As A Reply.</b>")
    else:
        text = ("<b>✅ Unban A User\n\n"
                "👉🏻 Send The ID Or Username Of The User To Be Unbanned. The User Must Have Already Started The Bot.\n\n"
                "💡 Or Use /unban As A Reply.</b>")
    await edit(query, text, reply_markup=InlineKeyboardMarkup([menu_back("bn_menu")]))


async def render_banned(client, page: int = 0, fetch: bool = False):
    """-> (text, markup, ids whose name is still unknown). fetch=False never waits for Telegram (instant)."""
    ids = await get_banned_users()
    if not ids:
        return ("<b>📃 List Of Banned Users !!\n\nNo Banned Users Yet.</b>",
                InlineKeyboardMarkup([menu_back("bn_menu")]), [])
    pages = max(1, -(-len(ids) // PAGE))
    page = min(max(page, 0), pages - 1)
    chunk = ids[page * PAGE:(page + 1) * PAGE]
    names = await asyncio.gather(*[_name(client, uid, fetch=fetch) for uid in chunk])
    lines = []
    for i, (uid, name) in enumerate(zip(chunk, names), page * PAGE + 1):
        lines.append(f"{num_emoji(i)} {_mention(uid, name)} <code>{uid}</code>")
    text = f"<b>📃 List Of Banned Users !!\n\n" + "\n".join(lines) + "</b>"
    missing = [u for u, n in zip(chunk, names) if u not in _names and u not in _missed]
    return text, InlineKeyboardMarkup(_nav("bl_list", page, len(ids), "bn_menu")), missing


async def _fill_names(client, query, page, missing):
    """Runs after the list is already on screen: asks Telegram for the missing names, then updates the list."""
    try:
        await asyncio.wait_for(asyncio.gather(*[_name(client, u) for u in missing]), 8)
    except Exception:
        return
    if any(u in _names for u in missing):
        try:
            text, markup, _ = await render_banned(client, page)
            await edit(query, text, reply_markup=markup)
        except Exception:
            pass


@Client.on_callback_query(filters.regex(r"^bl_list:\d+$") & settings_filter)
async def bl_list_cb(client: Client, query: CallbackQuery):
    pending.pop(query.from_user.id, None)
    await query.answer()
    page = int(query.data.split(":")[1])
    text, markup, missing = await render_banned(client, page)
    await edit(query, text, reply_markup=markup)   # shown at once
    if missing:
        asyncio.create_task(_fill_names(client, query, page, missing))


# ============================================================
# TYPED INPUT (called from the pending-input handler of Plugins/settings.py)
# ============================================================

_ID_RE = re.compile(r"\d{5,16}")
_USER_RE = re.compile(r"@?([A-Za-z][A-Za-z0-9_]{3,31})")


async def _resolve(client, message, lookup: bool):
    """-> (ids, unknown tokens). Accepts ids, @usernames of users who started the bot, and a forwarded message."""
    ids, bad = [], []
    fwd = getattr(message, "forward_from", None)
    if fwd is not None:
        ids.append(fwd.id)
    for token in (message.text or "").split()[:50]:
        token = token.strip(",;")
        if _ID_RE.fullmatch(token):
            ids.append(int(token))
            continue
        m = _USER_RE.fullmatch(token)
        found = None
        if m:
            doc = await find_user_by_username(m.group(1))
            if doc:
                found = doc["user_id"]
            elif lookup:
                try:
                    found = (await client.get_users("@" + m.group(1))).id
                except Exception:
                    found = None
        if found:
            ids.append(found)
        else:
            bad.append(token)
    return list(dict.fromkeys(ids)), bad


def _not_found_text(bad):
    shown = ", ".join(f"<code>{html.escape(b)}</code>" for b in bad[:3]) or "<code>-</code>"
    return (f"<b>❌ Couldn't Find {shown}.\n\n"
            "Send A Numeric ID Or The @username Of Someone Who Started The Bot.</b>")


async def handle_input(client, message, action: str):
    admin = message.from_user
    back = {"adm_add": "adm_list:0"}.get(action, "bn_menu")
    ids, bad = await _resolve(client, message, lookup=(action == "adm_add"))
    if not ids:
        return await send_panel(client, message, _not_found_text(bad),
                                reply_markup=InlineKeyboardMarkup([menu_back(back)]))
    pending.pop(admin.id, None)

    if action == "adm_add":
        uid = ids[0]
        if await is_admin(uid):
            result = "📋 That User Is Already An Administrator."
        else:
            await add_admin(uid)
            name = await _name(client, uid)
            log_event(client, "➕ Administrator Added", uid=uid, by=admin)
            result = f"✅ {_mention(uid, name)} Is Now An Administrator."
        text, markup = await render_admins(client, 0)
        return await send_panel(client, message, f"<b>{result}</b>\n\n{text}", reply_markup=markup)

    if action == "ban_add":
        protected = set(OWNERS) | set(await get_admins())
        skipped = [i for i in ids if i in protected]
        todo = [i for i in ids if i not in protected]
        new = await ban_users(todo) if todo else 0
        if todo:
            log_text(client, _ids_log("🚫 BANNED", admin, set(todo)))
        lines = []
        if len(ids) == 1:
            lines.append("✅🚷 The User Has Been Banned." if new else
                         "⚠️ An Admin Can't Be Banned." if skipped else "📋 The User Is Already Banned.")
        else:
            if new:
                lines.append(f"✅🚷 {new} Banned.")
            if len(todo) - new:
                lines.append(f"📋 {len(todo) - new} Already Banned.")
            if skipped:
                lines.append(f"⚠️ {len(skipped)} Admin Can't Be Banned.")
    else:   # unban_add
        removed = await unban_users(ids)
        log_text(client, _ids_log("✅ UNBANNED", admin, set(ids)))
        if len(ids) == 1:
            lines = ["✅ The User Has Been Unbanned." if removed else "📋 The User Is Already Unbanned."]
        else:
            lines = [f"✅ {removed} Unbanned."]
            if len(ids) - removed:
                lines.append(f"📋 {len(ids) - removed} Already Unbanned.")
    if bad:
        lines.append(f"❌ Not Found: {len(bad)}")
    lines.append(f"👥 Total Banned: {await banned_users_count()}")
    await send_panel(client, message, "<b>" + "\n".join(lines) + "</b>", reply_markup=bn_markup())
