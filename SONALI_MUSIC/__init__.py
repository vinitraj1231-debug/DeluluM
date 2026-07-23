from SONALI_MUSIC.core.ffmpeg import ensure_ffmpeg
ensure_ffmpeg()

from SONALI_MUSIC.core.bot import Sona
from SONALI_MUSIC.core.dir import dirr
from SONALI_MUSIC.core.git import git
from SONALI_MUSIC.core.userbot import Userbot
from SONALI_MUSIC.misc import dbb, heroku

from SafoneAPI import SafoneAPI
from .logging import LOGGER

dirr()
git()
dbb()
heroku()

import contextvars
from functools import wraps
import inspect
from pyrogram import Client
from pyrogram.errors import MessageNotModified

current_client = contextvars.ContextVar("current_client")
cloned_chats = {}  # Map: chat_id -> Client (to remember which clone ran the chat)

class ClientProxy(Client):
    def __init__(self):
        pass

    @property
    def __class__(self):
        try:
            return current_client.get().__class__
        except LookupError:
            return _main_app.__class__

    def __getattribute__(self, name):
        if name in ("__class__", "__doc__", "__dict__", "__module__", "_main_app", "current_client"):
            return super().__getattribute__(name)
        try:
            cli = current_client.get()
        except LookupError:
            cli = _main_app
        return getattr(cli, name)

    def __setattr__(self, name, value):
        try:
            cli = current_client.get()
        except LookupError:
            cli = _main_app
        return setattr(cli, name, value)

    def __str__(self):
        try:
            return str(current_client.get())
        except LookupError:
            return str(_main_app)

    def __repr__(self):
        try:
            return repr(current_client.get())
        except LookupError:
            return repr(_main_app)


def wrap_callback(callback):
    if not callback:
        return callback
    if inspect.iscoroutinefunction(callback):
        @wraps(callback)
        async def async_wrapper(client, *args, **kwargs):
            try:
                chat_id = None
                if args and hasattr(args[0], "chat") and args[0].chat:
                    chat_id = args[0].chat.id
                elif args and hasattr(args[0], "message") and args[0].message and args[0].message.chat:
                    chat_id = args[0].message.chat.id
                elif hasattr(client, "chat") and client.chat:
                    chat_id = client.chat.id

                if chat_id:
                    cloned_chats[chat_id] = client
            except:
                pass

            token = current_client.set(client)
            try:
                return await callback(client, *args, **kwargs)
            except MessageNotModified:
                pass
            finally:
                current_client.reset(token)
        return async_wrapper
    else:
        @wraps(callback)
        def sync_wrapper(client, *args, **kwargs):
            try:
                chat_id = None
                if args and hasattr(args[0], "chat") and args[0].chat:
                    chat_id = args[0].chat.id
                elif args and hasattr(args[0], "message") and args[0].message and args[0].message.chat:
                    chat_id = args[0].message.chat.id

                if chat_id:
                    cloned_chats[chat_id] = client
            except:
                pass

            token = current_client.set(client)
            try:
                return callback(client, *args, **kwargs)
            except MessageNotModified:
                pass
            finally:
                current_client.reset(token)
        return sync_wrapper


def wrap_all_handlers():
    for group in _main_app.dispatcher.groups.values():
        for handler in group:
            if hasattr(handler, "callback") and handler.callback:
                handler.callback = wrap_callback(handler.callback)


_main_app = Sona()
app = ClientProxy()
api = SafoneAPI()
userbot = Userbot()


from .platforms import *

Apple = AppleAPI()
Carbon = CarbonAPI()
SoundCloud = SoundAPI()
Spotify = SpotifyAPI()
Resso = RessoAPI()
Telegram = TeleAPI()
YouTube = YouTubeAPI()
