import os
from pyrogram import Client
from aiohttp import web
from config import API_ID, API_HASH, BOT_TOKEN, ADMIN
from database import ensure_indexes

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

        me = await self.get_me()
        print(f"Bot Started as {me.first_name}")
        
        if ADMIN:
            try:
                await self.send_message(ADMIN, f"**🤖 {me.first_name} Contact Bot is started...**")
            except Exception as e:
                print(f"Error sending message to admin: {e}")

    async def stop(self, *args):
        await super().stop()
        print("Bot stopped.")

if __name__ == "__main__":
    Bot().run()
