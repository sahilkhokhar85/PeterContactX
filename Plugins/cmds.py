import os
import sys
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from database import admin_filter, add_user, is_banned
from Plugins.contact import is_subscribed, send_force_sub_prompt, send_start_message

@Client.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    mention_user = message.from_user.mention

    if await is_banned(user_id):
        return

    if not await is_subscribed(client, user_id):
        return await send_force_sub_prompt(client, message)

    await add_user(user_id, message.from_user.first_name or "", message.from_user.username or "")
    await send_start_message(client, message.chat.id, mention_user)

@Client.on_message(filters.command("restart") & filters.private & admin_filter)
async def restart_bot(client: Client, message: Message):
    steve = await message.reply_text("**🔄 Restarting bot...**")
    await asyncio.sleep(3)
    await steve.edit("**✅ Bot restarted successfully**")
    os.execl(sys.executable, sys.executable, *sys.argv)
