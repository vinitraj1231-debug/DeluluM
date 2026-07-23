import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.errors import FloodWait
from pytgcalls import PyTgCalls, types
from SONALI_MUSIC import app, _main_app, wrap_callback
from SONALI_MUSIC.core.mongo import mongodb
import config
from config import BANNED_USERS

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

    # check if trying to clone main bot
    if bot_token == config.BOT_TOKEN:
        return await message.reply_text("<b>» You cannot clone the main bot! Please use a different bot token.</b>")

    # check if user has already cloned 3 bots
    clones_count = await clone_db.count_documents({"owner_id": user_id})
    if clones_count >= 3:
        return await message.reply_text("<b>» You have already reached the maximum limit of 3 cloned bots! Please unclone one of them first.</b>")

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
    # check if user has cloned bots
    clones = []
    async for c in clone_db.find({"owner_id": user_id}):
        clones.append(c)

    if not clones:
        return await message.reply_text("<b>» You haven't cloned any bot yet!</b>")

    args = message.text.split()
    if len(args) < 2:
        # Ask to specify
        txt = "<b>» Please specify the username of the cloned bot to unclone.</b>\n\n<b>Your clones:</b>\n"
        for idx, c in enumerate(clones, 1):
            txt += f"{idx}. @{c['username']}\n"
        txt += "\n<b>Usage:</b> <code>/unclone @botusername</code>"
        return await message.reply_text(txt)

    target_username = args[1].strip().replace("@", "")

    # find match
    clone = next((c for c in clones if c["username"].lower() == target_username.lower()), None)
    if not clone:
        return await message.reply_text(f"<b>» No cloned bot found with username @{target_username}!</b>")

    bot_token = clone["bot_token"]
    bot_id = int(bot_token.split(":")[0])

    # delete from database
    await clone_db.delete_one({"bot_token": bot_token})

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

    await message.reply_text(f"<b>» Successfully uncloned and stopped @{target_username}! It and its assistant are now stopped and deleted.</b>")


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
        # Show only user's clones
        clones_list = []
        async for c in clone_db.find({"owner_id": user_id}):
            clones_list.append(c)
        if not clones_list:
            return await message.reply_text("<b>» You haven't cloned any bot yet! Use the '🤖 CLONE BOT' button or <code>/clone Bot_Token Session_String</code> to clone.</b>")

        text = f"<b>» Your Cloned Bots ({len(clones_list)}/3):</b>\n\n"
        for idx, c in enumerate(clones_list, 1):
            text += f"{idx}. @{c.get('username', 'Unknown')} - Status: Running ✅\n"
        await message.reply_text(text)


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


# --- INTERACTIVE CLONING WIZARD & MANAGEMENT ---

clone_states = {}


@app.on_callback_query(filters.regex("clone_bot_btn") & ~BANNED_USERS)
async def clone_bot_btn_cb(client, CallbackQuery: CallbackQuery):
    user_id = CallbackQuery.from_user.id
    clones_count = await clone_db.count_documents({"owner_id": user_id})
    if clones_count >= 3:
        return await CallbackQuery.answer("⚠️ You have already cloned 3 bots (the maximum limit)!", show_alert=True)

    clone_states[user_id] = {"step": "waiting_for_token"}

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_clone_wizard")]
    ])

    await CallbackQuery.edit_message_text(
        "<b>🤖 CLONING WIZARD STARTED</b>\n\n"
        "Please send your **Bot Token** which you can get from @BotFather.\n\n"
        "Format example: `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`\n\n"
        "To abort at any time, click Cancel below or send `/cancel` in private chat.",
        reply_markup=keyboard
    )


@app.on_callback_query(filters.regex("cancel_clone_wizard") & ~BANNED_USERS)
async def cancel_clone_wizard_cb(client, CallbackQuery: CallbackQuery):
    user_id = CallbackQuery.from_user.id
    clone_states.pop(user_id, None)
    await CallbackQuery.answer("❌ Cloning cancelled.", show_alert=True)

    # Go back to start panel
    from SONALI_MUSIC.utils.database import get_lang
    from strings import get_string
    from SONALI_MUSIC.utils.inline import private_panel

    language = await get_lang(CallbackQuery.message.chat.id)
    _ = get_string(language)
    buttons = private_panel(_)
    try:
        await CallbackQuery.edit_message_text(
            _["start_2"].format(CallbackQuery.from_user.mention, app.mention),
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    except Exception:
        pass


@app.on_message(filters.private & ~BANNED_USERS, group=-2)
async def handle_clone_states(client, message: Message):
    user_id = message.from_user.id
    state = clone_states.get(user_id)
    if not state:
        return

    text = message.text.strip() if message.text else None
    if not text:
        return

    # If user wants to start or cancel or any other command
    if text.startswith("/start"):
        clone_states.pop(user_id, None)
        return  # Let standard /start handler process it

    message.stop_propagation()

    if text.lower() == "/cancel":
        clone_states.pop(user_id, None)
        return await message.reply_text("<b>❌ Cloning flow cancelled successfully.</b>")

    step = state["step"]
    if step == "waiting_for_token":
        if ":" not in text:
            return await message.reply_text("<b>⚠️ Invalid Bot Token format! Please send a valid token from @BotFather or /cancel to abort.</b>")

        if text == config.BOT_TOKEN:
            return await message.reply_text("<b>⚠️ You cannot clone the main bot using the main bot's token! Please send another token or /cancel.</b>")

        existing = await clone_db.find_one({"bot_token": text})
        if existing:
            return await message.reply_text("<b>⚠️ This bot token has already been cloned by someone else! Please send another token or /cancel.</b>")

        state["bot_token"] = text
        state["step"] = "waiting_for_session"

        return await message.reply_text(
            "<b>✅ Bot Token Received!</b>\n\n"
            "Now, please send your **Assistant Session String** if you want assistant music streaming features (Optional).\n\n"
            "If you don't have an assistant session or want to skip this step, please send `/skip`.\n\n"
            "To cancel, send /cancel."
        )

    elif step == "waiting_for_session":
        bot_token = state["bot_token"]
        session_string = None if text.lower() == "/skip" else text

        # Clear state
        clone_states.pop(user_id, None)

        mystic = await message.reply_text("<b>» Cloning your bot... Please wait.</b>")

        try:
            cloned_bot = await start_clone(bot_token, user_id, session_string)
            if not cloned_bot:
                return await mystic.edit_text("<b>❌ Failed to clone bot! Please make sure the bot token and session string are valid and not already running elsewhere.</b>")

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
            await mystic.edit_text(f"<b>❌ Error during cloning:</b> <code>{str(e)}</code>")


@app.on_callback_query(filters.regex("manage_clones_btn") & ~BANNED_USERS)
async def manage_clones_cb(client, CallbackQuery: CallbackQuery):
    user_id = CallbackQuery.from_user.id
    clones = []
    async for c in clone_db.find({"owner_id": user_id}):
        clones.append(c)

    if not clones:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⌯ ʙᴧᴄᴋ ⌯", callback_data="settingsback_helper")]
        ])
        try:
            return await CallbackQuery.edit_message_text(
                "<b>» You haven't cloned any bots yet! Click '🤖 CLONE BOT' to clone your first bot.</b>",
                reply_markup=keyboard
            )
        except Exception:
            return

    text = f"<b>🛠️ CLONE MANAGER</b>\n\n" \
           f"You have cloned <b>{len(clones)}/3</b> bots. Select a bot below to manage it:"

    buttons = []
    for c in clones:
        username = c.get("username", "Unknown")
        bot_token = c.get("bot_token", "")
        bot_id = int(bot_token.split(":")[0]) if ":" in bot_token else 0
        buttons.append([InlineKeyboardButton(f"🤖 @{username}", callback_data=f"manage_bot_{bot_id}")])

    buttons.append([InlineKeyboardButton("⌯ ʙᴧᴄᴋ ⌯", callback_data="settingsback_helper")])
    try:
        await CallbackQuery.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    except Exception:
        pass


@app.on_callback_query(filters.regex(r"^manage_bot_(\d+)$") & ~BANNED_USERS)
async def manage_single_bot_cb(client, CallbackQuery: CallbackQuery):
    user_id = CallbackQuery.from_user.id
    bot_id = int(CallbackQuery.matches[0].group(1))

    clone = await clone_db.find_one({"owner_id": user_id, "bot_token": {"$regex": f"^{bot_id}:"}})
    if not clone:
        return await CallbackQuery.answer("❌ Cloned bot not found!", show_alert=True)

    username = clone.get("username", "Unknown")
    name = clone.get("name", "Unknown")

    text = f"<b>🤖 CLONED BOT: @{username}</b>\n\n" \
           f"<b>Name:</b> {name}\n" \
           f"<b>Status:</b> Running ✅\n\n" \
           f"Use the buttons below to manage this cloned bot:"

    buttons = [
        [
            InlineKeyboardButton("❌ Unclone / Delete", callback_data=f"unclone_bot_{bot_id}"),
        ],
        [
            InlineKeyboardButton("⌯ ʙᴧᴄᴋ ⌯", callback_data="manage_clones_btn")
        ]
    ]
    try:
        await CallbackQuery.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    except Exception:
        pass


@app.on_callback_query(filters.regex(r"^unclone_bot_(\d+)$") & ~BANNED_USERS)
async def unclone_bot_callback(client, CallbackQuery: CallbackQuery):
    user_id = CallbackQuery.from_user.id
    bot_id = int(CallbackQuery.matches[0].group(1))

    clone = await clone_db.find_one({"owner_id": user_id, "bot_token": {"$regex": f"^{bot_id}:"}})
    if not clone:
        return await CallbackQuery.answer("❌ Cloned bot not found!", show_alert=True)

    bot_token = clone["bot_token"]

    # Delete from DB
    await clone_db.delete_one({"bot_token": bot_token})

    # Stop the bot, assistant, call
    if bot_id in cloned_bots:
        try:
            await cloned_bots[bot_id].stop()
            cloned_bots.pop(bot_id)
        except Exception as e:
            print(f"Error stopping clone bot: {e}")

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

    await CallbackQuery.answer("✅ Successfully uncloned and stopped this bot!", show_alert=True)

    # Go back to management list
    return await manage_clones_cb(client, CallbackQuery)
