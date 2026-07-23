from pyrogram import filters
from pyrogram.types import Message

from SONALI_MUSIC import app
from SONALI_MUSIC.utils.database import get_loop, set_loop
from SONALI_MUSIC.utils.decorators import AdminRightsCheck
from SONALI_MUSIC.utils.inline import close_markup
from config import BANNED_USERS


@app.on_message(filters.command(["loop", "cloop"]) & filters.group & ~BANNED_USERS)
@AdminRightsCheck
async def admins(cli, message: Message, _, chat_id):
    usage = _["admin_17"]
    if len(message.command) != 2:
        return await message.reply_text(usage)
    state = message.text.split(None, 1)[1].strip()
    if state.isnumeric():
        state = int(state)
        if 1 <= state <= 10:
            got = await get_loop(chat_id)
            if got != 0:
                state = got + state
            if int(state) > 10:
                state = 10
            await set_loop(chat_id, state)
            try:
                from SONALI_MUSIC.utils.database import sync_queue_to_mongo
                await sync_queue_to_mongo(chat_id)
            except Exception as e:
                print(f"Error syncing queue: {e}")
            return await message.reply_text(
                text=_["admin_18"].format(state, message.from_user.mention),
                reply_markup=close_markup(_),
            )
        else:
            return await message.reply_text(_["admin_17"])
    elif state.lower() == "enable":
        await set_loop(chat_id, 10)
        try:
            from SONALI_MUSIC.utils.database import sync_queue_to_mongo
            await sync_queue_to_mongo(chat_id)
        except Exception as e:
            print(f"Error syncing queue: {e}")
        return await message.reply_text(
            text=_["admin_18"].format(state, message.from_user.mention),
            reply_markup=close_markup(_),
        )
    elif state.lower() == "disable":
        await set_loop(chat_id, 0)
        try:
            from SONALI_MUSIC.utils.database import sync_queue_to_mongo
            await sync_queue_to_mongo(chat_id)
        except Exception as e:
            print(f"Error syncing queue: {e}")
        return await message.reply_text(
            _["admin_19"].format(message.from_user.mention),
            reply_markup=close_markup(_),
        )
    else:
        return await message.reply_text(usage)
