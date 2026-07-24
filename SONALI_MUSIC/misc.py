import socket
import time

import heroku3
from pyrogram import filters

import config
from SONALI_MUSIC.core.mongo import mongodb

from .logging import LOGGER

from pyrogram.filters import Filter

class CustomSudoers(Filter):
    def __init__(self):
        super().__init__()
        self.user_ids = set()

    def add(self, user_id):
        self.user_ids.add(user_id)

    def remove(self, user_id):
        self.user_ids.discard(user_id)

    def copy(self):
        return self.user_ids.copy()

    def __contains__(self, user_id):
        from SONALI_MUSIC import current_client, _main_app
        try:
            active_client = current_client.get()
        except LookupError:
            active_client = _main_app

        if active_client and hasattr(active_client, "me") and active_client.me and active_client.me.id != _main_app.id:
            if hasattr(active_client, "owner_id") and user_id == active_client.owner_id:
                return True

        return user_id in self.user_ids or user_id == config.OWNER_ID

    async def __call__(self, client, update):
        if not update.from_user:
            return False
        user_id = update.from_user.id
        return user_id in self

SUDOERS = CustomSudoers()

HAPP = None
_boot_ = time.time()


def is_heroku():
    return "heroku" in socket.getfqdn()


XCB = [
    "/",
    "@",
    ".",
    "com",
    ":",
    "git",
    "heroku",
    "push",
    str(config.HEROKU_API_KEY),
    "https",
    str(config.HEROKU_APP_NAME),
    "HEAD",
    "master",
]


def dbb():
    global db
    db = {}
    LOGGER(__name__).info(f"𝗗𝗔𝗧𝗔𝗕𝗔𝗦𝗘 𝗟𝗢𝗔𝗗𝗘𝗗 𝗕𝗢𝗦𝗦")


async def sudo():
    global SUDOERS
    SUDOERS.add(config.OWNER_ID)
    sudoersdb = mongodb.sudoers
    sudoers = await sudoersdb.find_one({"sudo": "sudo"})
    sudoers = [] if not sudoers else sudoers["sudoers"]
    if config.OWNER_ID not in sudoers:
        sudoers.append(config.OWNER_ID)
        await sudoersdb.update_one(
            {"sudo": "sudo"},
            {"$set": {"sudoers": sudoers}},
            upsert=True,
        )
    if sudoers:
        for user_id in sudoers:
            SUDOERS.add(user_id)
    LOGGER(__name__).info(f"𝗦𝗨𝗗𝗢 𝗨𝗦𝗘𝗥 𝗗𝗢𝗡𝗘 𝗕𝗢𝗦𝗦")


def heroku():
    global HAPP
    if is_heroku():
        if config.HEROKU_API_KEY and config.HEROKU_APP_NAME:
            try:
                Heroku = heroku3.from_key(config.HEROKU_API_KEY)
                HAPP = Heroku.app(config.HEROKU_APP_NAME)
                LOGGER(__name__).info(f"𝗛𝗘𝗥𝗢𝗞𝗨 𝗔𝗣𝗣 𝗡𝗔𝗠𝗘 𝗟𝗢𝗔𝗗𝗘𝗗 || 𝗗𝗢𝗡𝗘")
            except BaseException:
                LOGGER(__name__).warning(
                    f"𝗬𝗢𝗨 𝗛𝗔𝗩𝗘 𝗡𝗢𝗧 𝗙𝗜𝗟𝗟𝗘𝗗 𝗛𝗘𝗥𝗢𝗞𝗨 𝗔𝗣𝗜 𝗞𝗘𝗬 𝗔𝗡𝗗 𝗛𝗘𝗥𝗢𝗞𝗨 𝗔𝗣𝗣 𝗡𝗔𝗠𝗘 𝗖𝗢𝗥𝗥𝗘𝗖𝗧"
)
