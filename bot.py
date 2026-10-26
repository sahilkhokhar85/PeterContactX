import os
import html
from pyrogram import Client
from aiohttp import web
from config import API_ID, API_HASH, BOT_TOKEN, LOG_CHANNEL
from logchannel import log_now
from database import ensure_indexes, delete_user
import userlookup

routes = web.RouteTableDef()

@routes.get("/", allow_head=True)
async def root_route(request):
    return web.Response(text="<h3 align='center'><b>Bot is Alive</b></h3>", content_type='text/html')

async def web_server():
    app = web.Application(client_max_size=30_000_000)
    app.add_routes(routes)
    return app

class Bot(Client):
    def __init__(self):
        super().__init__(
            "contact_bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            plugins=dict(root="Plugins"),
            workers=200,
            sleep_threshold=15
        )

    async def start(self, *args, **kwargs):
        app = web.AppRunner(await web_server())
        await app.setup()
        try:
            await web.TCPSite(app, "0.0.0.0", int(os.getenv("PORT", 8080))).start()
            print("Web server started.")
        except Exception as e:
            print(f"Web server error: {e}")
            
        await super().start(*args, **kwargs)

        await ensure_indexes()   # message-map TTL, ban list, join requests (safe to run on every start)

        if LOG_CHANNEL:
            try:   # also lets the bot resolve the channel before the first log entry
                chat = await self.get_chat(LOG_CHANNEL)
                print(f"Log channel: {chat.title}")
            except Exception as e:
                print(f"Log channel error ({type(e).__name__}): make the bot an admin of LOG_CHANNEL. {e}")

        me = await self.get_me()
        print(f"Bot Started as {me.first_name}")
        try:   # the bot must never be in its own user list (it was added once by mistake)
            await delete_user(me.id)
        except Exception:
            pass
        
        await userlookup.start()   # optional account (SESSION_STRING) that finds names of users the bot never saw

        # start notice goes to the log channel only (nothing is sent to the owner's chat with the bot)
        await log_now(self, f"<b>🤖 {html.escape(me.first_name)} Started !!</b>")

    async def stop(self, *args):
        await log_now(self, "<b>🛑 Bot Stopped</b>")
        await userlookup.stop()
        await super().stop()
        print("Bot stopped.")

if __name__ == "__main__":
    Bot().run()
