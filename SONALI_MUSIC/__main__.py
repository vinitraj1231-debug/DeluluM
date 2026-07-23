import asyncio
import importlib

from pyrogram import idle
from pytgcalls.exceptions import NoActiveGroupCall

import config
from SONALI_MUSIC import LOGGER, app, userbot
from SONALI_MUSIC.core.call import Sona
from SONALI_MUSIC.misc import sudo
from SONALI_MUSIC.plugins import ALL_MODULES
from SONALI_MUSIC.utils.database import get_banned_users, get_gbanned
from config import BANNED_USERS


async def init():
    if (
        not config.STRING1
        and not config.STRING2
        and not config.STRING3
        and not config.STRING4
        and not config.STRING5
    ):
        LOGGER(__name__).error("𝐒𝐭𝐫𝐢𝐧𝐠 𝐒𝐞𝐬𝐬𝐢𝐨𝐧 𝐍𝐨𝐭 𝐅𝐢𝐥𝐥𝐞𝐝, 𝐏𝐥𝐞𝐚𝐬𝐞 𝐅𝐢𝐥𝐥 𝐀 𝐏𝐲𝐫𝐨𝐠𝐫𝐚𝐦 𝐒𝐞𝐬𝐬𝐢𝐨𝐧")
        exit()
    await sudo()
    try:
        users = await get_gbanned()
        for user_id in users:
            BANNED_USERS.add(user_id)
        users = await get_banned_users()
        for user_id in users:
            BANNED_USERS.add(user_id)
    except:
        pass
    await app.start()
    for all_module in ALL_MODULES:
        importlib.import_module("SONALI_MUSIC.plugins" + all_module)
    LOGGER("SONALI_MUSIC.plugins").info("𝐀𝐥𝐥 𝐅𝐞𝐚𝐭𝐮𝐫𝐞𝐬 𝐋𝐨𝐚𝐝𝐞𝐝 𝐁𝐚𝐛𝐲🥳...")

    # Wrap all registered callbacks to set current_client context dynamically
    from SONALI_MUSIC import wrap_all_handlers
    wrap_all_handlers()

    # Start all cloned bots from MongoDB
    from SONALI_MUSIC.plugins.sudo.clone import start_clone, clone_db
    async def restart_clones():
        try:
            async for clone in clone_db.find():
                bot_token = clone["bot_token"]
                owner_id = clone["owner_id"]
                LOGGER(__name__).info(f"Restarting cloned bot: @{clone.get('username')}")
                await start_clone(bot_token, owner_id)
        except Exception as e:
            LOGGER(__name__).error(f"Error restarting cloned bots: {e}")

    await restart_clones()

    await userbot.start()
    await Sona.start()
    try:
        await Sona.stream_call("https://te.legra.ph/file/29f784eb49d230ab62e9e.mp4")
    except NoActiveGroupCall:
        LOGGER("SONALI_MUSIC").error(
            "𝗣𝗹𝗭 𝗦𝗧𝗔𝗥𝗧 𝗬𝗢𝗨Ｒ 𝗟𝗢𝗚 𝗚𝗥𝗢𝗨𝗣 𝗩𝗢𝗜𝗖𝗘𝗖𝗛𝗔𝗧\🇨𝗛𝗔𝗡𝗡𝗘𝗟\n\n𝗧🇭𝗨𝗡𝗗𝗘𝗥 𝗕𝗢𝗧 𝗦𝗧𝗢𝗣........"
        )
        exit()
    except:
        pass
    await Sona.decorators()
    LOGGER("SONALI_MUSIC").info(
        "╔═════ஜ۩۞۩ஜ════╗\n  ☠︎︎𝗠𝗔𝗗𝗘 𝗕𝗬 RAJ☠︎︎\n╚═════ஜ۩۞۩ஜ════╝"
    )
    await idle()

    # Stop all cloned bots
    from SONALI_MUSIC.plugins.sudo.clone import cloned_bots
    for bot_id, client in list(cloned_bots.items()):
        try:
            await client.stop()
        except:
            pass

    await app.stop()
    await userbot.stop()
    LOGGER("SONALI_MUSIC").info("𝗦𝗧𝗢𝗣 RAJ 𝗠𝗨𝗦𝗜𝗖 𝗕𝗢𝗧..")


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(init())
