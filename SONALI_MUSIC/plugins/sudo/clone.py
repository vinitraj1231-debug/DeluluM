import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait
from pytgcalls import PyTgCalls, types
from SONALI_MUSIC import app, _main_app, wrap_callback
from SONALI_MUSIC.core.mongo import mongodb
import config

clone_db = mongodb.clones
cloned_bots = {}  # { bot_id: Client }
cloned_assistants = {}  # { bot_id: Client }
cloned_calls = {}  # { bot_id: PyTgCalls }

async def start_clone(bot_token: str, owner_id: int, session_string: str = None):
    try:
        # Avoid creating duplicate clients
        bot_id = int(bot_token.split(":")[0])
        if bot_id in cloned_bots:
            return cloned_bots[bot_id]

        client = Client(
            name=f"cloned_bot_{bot_id}",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            bot_token=bot_token,
            in_memory=True
        )
        await client.start()

        # Copy handlers from main app
        for group, handlers in _main_app.dispatcher.groups.items():
            for handler in handlers:
                client.add_handler(handler, group)

        cloned_bots[client.me.id] = client

        # Now start assistant and PyTgCalls if session_string is provided
        if session_string:
            try:
                ass_client = Client(
                    name=f"cloned_ass_{bot_id}",
                    api_id=config.API_ID,
                    api_hash=config.API_HASH,
                    session_string=session_string,
                    no_updates=True,
                )
                await ass_client.start()
                ass_client.id = ass_client.me.id
                ass_client.name = ass_client.me.mention
                ass_client.username = ass_client.me.username

                cloned_assistants[client.me.id] = ass_client

                cloned_call = PyTgCalls(ass_client, cache_duration=100)
                await cloned_call.start()

                from SONALI_MUSIC.core.call import Sona
                @cloned_call.on_update()
                async def _cloned_update_handler(_, update, _cloned_call=cloned_call):
                    from SONALI_MUSIC import current_client
                    token = current_client.set(client)
                    try:
                        if isinstance(update, types.StreamEnded):
                            if update.stream_type == types.StreamEnded.Type.AUDIO:
                                await Sona.change_stream(_cloned_call, update.chat_id)
                        elif isinstance(update, types.ChatUpdate):
                            if update.status in [
                                types.ChatUpdate.Status.KICKED,
                                types.ChatUpdate.Status.LEFT_GROUP,
                                types.ChatUpdate.Status.CLOSED_VOICE_CHAT,
                            ]:
                                await Sona.stop_stream(update.chat_id)
                    except Exception as e:
                        print(f"Error in cloned_call on_update handler: {e}")
                    finally:
                        current_client.reset(token)

                cloned_calls[client.me.id] = cloned_call
                print(f"Assistant & PyTgCalls started for cloned bot @{client.me.username}")
            except Exception as ex:
                print(f"Error starting assistant/PyTgCalls for cloned bot @{client.me.username}: {ex}")

        return client
    except Exception as e:
        print(f"Error starting cloned bot with token {bot_token}: {e}")
        return None


@app.on_message(filters.command(["clone"]) & filters.private)
async def clone_bot(client, message: Message):
    args = message.text.split(None, 2)
    if len(args) < 2:
        return await message.reply_text(
            "<b>» Please provide at least your Bot Token to clone. Optional: Assistant Session String</b>\n\n"
            "<b>Usage:</b> <code>/clone [Bot Token] [Assistant Session String (Optional)]</code>\n\n"
            "<b>Example:</b>\n"
            "<code>/clone 123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ BQBy3abc_def_ghi_jkl...</code>\n\n"
            "<b>Example (Without Assistant):</b>\n"
            "<code>/clone 123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ</code>"
        )

    bot_token = args[1].strip()
    session_string = args[2].strip() if len(args) >= 3 else None
    user_id = message.from_user.id

    # check format
    if ":" not in bot_token:
        return await message.reply_text("<b>» Invalid Bot Token format! Please get a valid token from @BotFather.</b>")

    # check if user already cloned a bot
    existing_clone = await clone_db.find_one({"owner_id": user_id})
    if existing_clone:
        return await message.reply_text(f"<b>» You have already cloned a bot: @{existing_clone.get('username')}. Please use <code>/unclone</code> first if you want to clone another one!</b>")

    # check if token already cloned
    existing_token = await clone_db.find_one({"bot_token": bot_token})
    if existing_token:
        return await message.reply_text("<b>» This bot token has already been cloned by someone else!</b>")

    mystic = await message.reply_text("<b>» Cloning your bot... Please wait.</b>")

    try:
        # try starting the clone
        cloned_bot = await start_clone(bot_token, user_id, session_string)
        if not cloned_bot:
            return await mystic.edit_text("<b>» Failed to clone bot! Please make sure the bot token and session string are valid and not already running elsewhere.</b>")

        # save to database
        await clone_db.update_one(
            {"bot_token": bot_token},
            {
                "$set": {
                    "owner_id": user_id,
                    "username": cloned_bot.me.username,
                    "name": cloned_bot.me.first_name,
                    "session_string": session_string or ""
                }
            },
            upsert=True
        )

        await mystic.edit_text(
            f"<b>» Successfully Cloned! 🎉</b>\n\n"
            f"<b>Bot Name:</b> {cloned_bot.me.first_name}\n"
            f"<b>Bot Username:</b> @{cloned_bot.me.username}\n\n"
            f"You can now use all music commands on @{cloned_bot.me.username}!"
        )
    except Exception as e:
        await mystic.edit_text(f"<b>» Error during cloning:</b> <code>{str(e)}</code>")


@app.on_message(filters.command(["unclone"]) & filters.private)
async def unclone_bot(client, message: Message):
    user_id = message.from_user.id
    # check if user has a cloned bot
    clone = await clone_db.find_one({"owner_id": user_id})
    if not clone:
        return await message.reply_text("<b>» You haven't cloned any bot yet!</b>")

    bot_token = clone["bot_token"]
    bot_id = int(bot_token.split(":")[0])

    # delete from database
    await clone_db.delete_one({"owner_id": user_id})

    # stop the client
    if bot_id in cloned_bots:
        try:
            await cloned_bots[bot_id].stop()
            cloned_bots.pop(bot_id)
        except Exception as e:
            print(f"Error stopping clone bot: {e}")

    # stop assistant and call
    if bot_id in cloned_calls:
        try:
            await cloned_calls[bot_id].stop()
            cloned_calls.pop(bot_id)
        except Exception as e:
            print(f"Error stopping clone PyTgCalls: {e}")

    if bot_id in cloned_assistants:
        try:
            await cloned_assistants[bot_id].stop()
            cloned_assistants.pop(bot_id)
        except Exception as e:
            print(f"Error stopping clone assistant: {e}")

    await message.reply_text("<b>» Successfully uncloned your bot! It and its assistant are now stopped and deleted.</b>")


@app.on_message(filters.command(["cloned", "clones"]) & filters.private)
async def list_clones(client, message: Message):
    user_id = message.from_user.id
    if user_id == config.OWNER_ID:
        # Show all clones
        clones_list = []
        async for c in clone_db.find():
            clones_list.append(c)
        if not clones_list:
            return await message.reply_text("<b>» No cloned bots are currently running!</b>")

        text = f"<b>» Total Cloned Bots:</b> {len(clones_list)}\n\n"
        for i, c in enumerate(clones_list, 1):
            text += f"{i}. @{c.get('username', 'Unknown')} (Owner ID: <code>{c['owner_id']}</code>)\n"
        await message.reply_text(text)
    else:
        # Show only user's clone
        clone = await clone_db.find_one({"owner_id": user_id})
        if not clone:
            return await message.reply_text("<b>» You haven't cloned any bot yet! Use <code>/clone Bot_Token Session_String</code> to clone.</b>")

        await message.reply_text(f"<b>» Your cloned bot:</b> @{clone.get('username', 'Unknown')}\n<b>Status:</b> Running ✅")


@app.on_message(filters.command(["broadcastclones"]) & filters.user(config.OWNER_ID))
async def broadcast_clones(client, message: Message):
    if not message.reply_to_message and len(message.command) < 2:
        return await message.reply_text(
            "<b>» Please reply to a message or provide text to broadcast.</b>\n\n"
            "<b>Usage:</b>\n"
            "• Reply to a message with <code>/broadcastclones</code>\n"
            "• Or use <code>/broadcastclones [text]</code>"
        )

    mystic = await message.reply_text("<b>» Broadcasting through all cloned bots... Please wait.</b>")

    total_clones = len(cloned_bots)
    if total_clones == 0:
        return await mystic.edit_text("<b>» No cloned bots are currently running!</b>")

    sent_chats = 0
    failed_chats = 0
    successful_bots = 0

    is_reply = bool(message.reply_to_message)
    reply_markup = message.reply_to_message.reply_markup if is_reply else None
    from_chat_id = message.chat.id if is_reply else None
    message_id = message.reply_to_message.id if is_reply else None
    text = message.text.split(None, 1)[1] if not is_reply else None

    from SONALI_MUSIC.utils.database import get_served_chats, get_served_users
    chats = [int(chat["chat_id"]) for chat in await get_served_chats()]
    users = [int(user["user_id"]) for user in await get_served_users()]
    all_targets = chats + users

    for bot_id, bot_client in list(cloned_bots.items()):
        try:
            bot_sent = 0
            for chat_id in all_targets:
                try:
                    if is_reply:
                        await bot_client.copy_message(
                            chat_id=chat_id,
                            from_chat_id=from_chat_id,
                            message_id=message_id,
                            reply_markup=reply_markup
                        )
                    else:
                        await bot_client.send_message(
                            chat_id=chat_id,
                            text=text
                        )
                    bot_sent += 1
                    sent_chats += 1
                    await asyncio.sleep(0.05)
                except FloodWait as fw:
                    await asyncio.sleep(fw.value)
                    try:
                        if is_reply:
                            await bot_client.copy_message(
                                chat_id=chat_id,
                                from_chat_id=from_chat_id,
                                message_id=message_id,
                                reply_markup=reply_markup
                            )
                        else:
                            await bot_client.send_message(
                                chat_id=chat_id,
                                text=text
                            )
                        bot_sent += 1
                        sent_chats += 1
                    except:
                        failed_chats += 1
                except Exception:
                    failed_chats += 1

            if bot_sent > 0:
                successful_bots += 1

        except Exception as e:
            print(f"Error broadcasting with clone {bot_id}: {e}")

    await mystic.edit_text(
        f"<b>» Cloned Bots Broadcast Completed! 🎉</b>\n\n"
        f"<b>Total Cloned Bots:</b> {total_clones}\n"
        f"<b>Successful Bots:</b> {successful_bots}\n"
        f"<b>Sent Messages:</b> {sent_chats} chats\n"
        f"<b>Failed Messages:</b> {failed_chats} chats"
    )
