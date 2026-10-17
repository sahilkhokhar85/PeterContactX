import asyncio
import html
import re
from math import ceil

from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait
from pyrogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from database import (
    admin_filter, add_indexed_channel, add_content_item, is_indexed_channel,
    clear_content_index
)

# Bots can't call get_chat_history() (Telegram blocks messages.GetHistory for bot
# accounts), but they CAN fetch messages by known ID via get_messages() — so we
# ask the admin for the last message (forward it, or send its t.me link) and
# then walk backwards from that ID in batches.
BATCH_SIZE = 200
HTML = enums.ParseMode.HTML

LINK_REGEX = re.compile(r"(?:https?://)?t\.me/(c/)?([\w\d_]+)/(\d+)")

# pending_index[admin_id] = (chat_id, last_msg_id) — set once a forward/link is
# resolved, consumed when the admin taps "Yes, index it".
pending_index = {}


async def _is_indexed_channel_func(_, __, message):
    if not message.chat:
        return False
    return await is_indexed_channel(message.chat.id)

# Matches only channels the admin has already registered via a previous index run.
indexed_channel_filter = filters.create(_is_indexed_channel_func)


def _extract_text(message: Message) -> str:
    return (message.text or message.caption or "").strip()


def _resolve_target(message: Message):
    """Returns (chat_id, last_message_id) from a forwarded channel post or a
    t.me link, else (None, None)."""
    if message.forward_from_chat:
        return message.forward_from_chat.id, message.forward_from_message_id
    if message.text:
        match = LINK_REGEX.search(message.text)
        if match:
            is_private, chat_part, msg_id = match.groups()
            chat_id = int(f"-100{chat_part}") if is_private else f"@{chat_part}"
            return chat_id, int(msg_id)
    return None, None


@Client.on_message(
    filters.private & admin_filter &
    (filters.forwarded | (filters.text & filters.regex(LINK_REGEX)))
)
async def request_index(client: Client, message: Message):
    chat_id, last_msg_id = _resolve_target(message)
    if not chat_id:
        return  # not a channel forward / not a recognizable t.me link — ignore

    try:
        chat = await client.get_chat(chat_id)
    except Exception as e:
        return await message.reply(
            f"<b>❌ Couldn't Access That Channel:</b>\n<code>{html.escape(str(e))}</code>\n"
            f"<b>Make Sure The Bot Is A Member/Admin There.</b>",
            parse_mode=HTML
        )

    pending_index[message.from_user.id] = (chat.id, last_msg_id)
    await message.reply(
        f"<b>Index This Channel?</b>\n\n"
        f"<b>Chat:</b> <code>{html.escape(str(chat.title or chat.id))}</code>\n"
        f"<b>Up To Message ID:</b> <code>{last_msg_id}</code>\n\n"
        f"<b>Text, Links And Images Will Be Scanned. New Posts Here Will Also "
        f"Auto-Index Going Forward, No Need To Repeat This.</b>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes, Index It", callback_data="do_index")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_index")]
        ]),
        parse_mode=HTML
    )


@Client.on_callback_query(filters.regex("^cancel_index$") & admin_filter)
async def cancel_index_cb(client: Client, query: CallbackQuery):
    pending_index.pop(query.from_user.id, None)
    await query.message.edit_text("<b>Cancelled.</b>", parse_mode=HTML)


@Client.on_callback_query(filters.regex("^do_index$") & admin_filter)
async def do_index_cb(client: Client, query: CallbackQuery):
    target = pending_index.pop(query.from_user.id, None)
    if not target:
        return await query.answer("This request has expired.", show_alert=True)

    chat_id, last_msg_id = target
    await add_indexed_channel(chat_id)
    status = query.message
    await status.edit_text(f"<b>🔍 Indexing Started...</b> <b>(Up To Message</b> <code>{last_msg_id}</code><b>)</b>", parse_mode=HTML)

    scanned = 0
    added = 0
    batches = ceil(last_msg_id / BATCH_SIZE) if last_msg_id else 0

    for batch in range(batches):
        start_id = batch * BATCH_SIZE + 1
        end_id = min(start_id + BATCH_SIZE - 1, last_msg_id)
        ids = list(range(start_id, end_id + 1))

        while True:
            try:
                messages = await client.get_messages(chat_id, ids)
                break
            except FloodWait as e:
                await asyncio.sleep(e.value + 2)
            except Exception:
                messages = []
                break

        if not isinstance(messages, list):
            messages = [messages]

        for msg in messages:
            if not msg or msg.empty:
                continue
            scanned += 1
            if not (msg.text or msg.photo):
                continue
            text = _extract_text(msg)
            if text:
                await add_content_item(chat_id, msg.id, text, has_media=bool(msg.photo))
                added += 1

        if batch % 3 == 0 or batch == batches - 1:
            try:
                await status.edit_text(
                    f"<b>📊 Indexing... Batch {batch + 1}/{batches}</b>\n"
                    f"<b>Scanned:</b> <code>{scanned}</code> <b>• Indexed:</b> <code>{added}</code>",
                    parse_mode=HTML
                )
            except Exception:
                pass

    await status.edit_text(
        f"<b>✅ Indexing Complete</b>\n\n"
        f"<b>• Scanned:</b> <code>{scanned}</code>\n"
        f"<b>• Indexed (Text/Link/Image Posts):</b> <code>{added}</code>\n\n"
        f"<b>New Posts In This Channel Will Now Be Indexed Automatically.</b>",
        parse_mode=HTML
    )


@Client.on_message(filters.channel & indexed_channel_filter & (filters.text | filters.photo))
async def auto_index_new_post(client: Client, message: Message):
    """Keeps the index current — any new post in a registered channel gets added automatically."""
    text = _extract_text(message)
    if text:
        await add_content_item(message.chat.id, message.id, text, has_media=bool(message.photo))


@Client.on_message(filters.command("clearindex") & filters.private & admin_filter)
async def clearindex_cmd(client: Client, message: Message):
    await message.reply(
        "<b>⚠️ This Will Permanently Delete ALL Indexed Content.\n"
        "Registered Channels Stay Registered — New Posts Will Still Auto-Index After This.\n\n"
        "Are You Sure?</b>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes, Clear Everything", callback_data="do_clearindex")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_index")]
        ]),
        parse_mode=HTML
    )


@Client.on_callback_query(filters.regex("^do_clearindex$") & admin_filter)
async def do_clearindex_cb(client: Client, query: CallbackQuery):
    await clear_content_index()
    await query.message.edit_text("<b>🗑️ All Indexed Content Has Been Cleared.</b>", parse_mode=HTML)
