import asyncio
import time
from datetime import datetime
import psutil
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.errors import FloodWait, RPCError, Unauthorized
from pytgcalls import PyTgCalls, types
from SONALI_MUSIC import app, _main_app, wrap_callback, LOGGER
from SONALI_MUSIC.core.mongo import mongodb
from SONALI_MUSIC.misc import _boot_
import config
from config import BANNED_USERS

# Database Setup
clone_db = mongodb.clones
clone_users_db = mongodb.clone_users
clone_settings_db = mongodb.clone_settings

# Active Instances Cache
cloned_bots = {}          # { bot_id: Client }
cloned_assistants = {}    # { bot_id: Client }
cloned_calls = {}         # { bot_id: PyTgCalls }

# State Machine for interactive flows
# { user_id: { "step": str, ... } }
clone_states = {}

# --- HELPER FUNCTIONS ---

async def get_clone_settings():
    settings = await clone_settings_db.find_one({"id": "global"})
    if not settings:
        default_settings = {
            "id": "global",
            "approval_required": True,
            "default_clone_limit": 3
        }
        await clone_settings_db.insert_one(default_settings)
        return default_settings
    return settings


async def get_clone_user(user_id: int):
    user = await clone_users_db.find_one({"user_id": user_id})
    if not user:
        settings = await get_clone_settings()
        # Main owner is always approved
        status = "approved" if not settings.get("approval_required", True) or user_id == config.OWNER_ID else "pending"
        limit = 99 if user_id == config.OWNER_ID else settings.get("default_clone_limit", 3)
        doc = {
            "user_id": user_id,
            "status": status,
            "clone_limit": limit,
            "created_at": time.time()
        }
        await clone_users_db.insert_one(doc)
        return doc
    return user


async def add_clone_log(bot_id: int, message: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"
    await clone_db.update_one(
        {"bot_id": bot_id},
        {"$push": {"logs": {"$each": [log_entry], "$slice": -30}}}
    )


async def validate_assistant_session(session_string: str) -> dict:
    temp_client = Client(
        name="temp_ass_val",
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        session_string=session_string,
        in_memory=True
    )
    try:
        await temp_client.start()
        me = await temp_client.get_me()
        user_id = me.id
        username = me.username or ""
        first_name = me.first_name or ""
        last_name = me.last_name or ""
        name = f"{first_name} {last_name}".strip()
        await temp_client.stop()
        return {
            "valid": True,
            "user_id": user_id,
            "username": username,
            "name": name,
            "error": None
        }
    except Exception as e:
        try:
            await temp_client.stop()
        except:
            pass
        return {
            "valid": False,
            "user_id": None,
            "username": None,
            "name": None,
            "error": str(e)
        }


async def validate_bot_token(bot_token: str) -> dict:
    temp_client = Client(
        name="temp_bot_val",
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        bot_token=bot_token,
        in_memory=True
    )
    try:
        await temp_client.start()
        me = await temp_client.get_me()
        bot_id = me.id
        username = me.username or ""
        first_name = me.first_name or ""
        last_name = me.last_name or ""
        name = f"{first_name} {last_name}".strip()
        await temp_client.stop()
        return {
            "valid": True,
            "bot_id": bot_id,
            "username": username,
            "name": name,
            "error": None
        }
    except Exception as e:
        try:
            await temp_client.stop()
        except:
            pass
        return {
            "valid": False,
            "bot_id": None,
            "username": None,
            "name": None,
            "error": str(e)
        }


async def update_clone_settings(key: str, value):
    await clone_settings_db.update_one(
        {"id": "global"},
        {"$set": {key: value}},
        upsert=True
    )


# --- CORE INSTANCE LIFECYCLE ---

async def start_clone(bot_token: str, owner_id: int, session_string: str = None) -> tuple:
    try:
        bot_id = int(bot_token.split(":")[0])

        # If already running, return cached instance
        if bot_id in cloned_bots:
            return cloned_bots[bot_id], None

        client = Client(
            name=f"cloned_bot_{bot_id}",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            bot_token=bot_token,
            in_memory=True
        )
        await client.start()

        # Set critical attributes
        client.id = client.me.id
        client.name = f"{client.me.first_name} {client.me.last_name or ''}".strip()
        client.username = client.me.username
        client.mention = client.me.mention
        client.owner_id = owner_id

        # Copy all dispatcher handlers from main client
        for group, handlers in _main_app.dispatcher.groups.items():
            for handler in handlers:
                client.add_handler(handler, group)

        cloned_bots[client.id] = client

        ass_error = None
        if session_string:
            try:
                ass_client = Client(
                    name=f"cloned_ass_{bot_id}",
                    api_id=config.API_ID,
                    api_hash=config.API_HASH,
                    session_string=session_string,
                    no_updates=True,
                    in_memory=True
                )
                await ass_client.start()
                ass_client.id = ass_client.me.id
                ass_client.name = ass_client.me.mention
                ass_client.username = ass_client.me.username

                cloned_assistants[client.id] = ass_client

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
                        LOGGER(__name__).error(f"Error in cloned_call on_update: {e}")
                    finally:
                        current_client.reset(token)

                cloned_calls[client.id] = cloned_call
                await add_clone_log(bot_id, "Bot and Assistant successfully started.")
            except Exception as ex:
                ass_error = str(ex)
                await add_clone_log(bot_id, f"Failed starting Assistant: {ass_error}")

        return client, ass_error
    except Exception as e:
        LOGGER(__name__).error(f"Error starting cloned bot with token {bot_token[:10]}...: {e}")
        return None, str(e)


async def stop_clone_instance(bot_id: int):
    # Stop client
    if bot_id in cloned_bots:
        try:
            await cloned_bots[bot_id].stop()
        except Exception as e:
            LOGGER(__name__).warning(f"Error stopping clone client {bot_id}: {e}")
        cloned_bots.pop(bot_id, None)

    # Stop calls
    if bot_id in cloned_calls:
        try:
            await cloned_calls[bot_id].stop()
        except Exception as e:
            LOGGER(__name__).warning(f"Error stopping clone calls {bot_id}: {e}")
        cloned_calls.pop(bot_id, None)

    # Stop assistant
    if bot_id in cloned_assistants:
        try:
            await cloned_assistants[bot_id].stop()
        except Exception as e:
            LOGGER(__name__).warning(f"Error stopping clone assistant {bot_id}: {e}")
        cloned_assistants.pop(bot_id, None)


# --- SLASH COMMANDS (USER & OWNER) ---

@app.on_message(filters.command(["clone"]) & filters.private & ~BANNED_USERS)
async def clone_command(client, message: Message):
    user_id = message.from_user.id
    user_data = await get_clone_user(user_id)

    if user_data.get("status") == "suspended":
        return await message.reply_text("<b>❌ Access Denied! Your cloning privilege has been suspended by the Owner.</b>")

    if user_data.get("status") == "pending":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📨 Request Approval", callback_data="clone_req_approval")],
            [InlineKeyboardButton("💬 Get Support", url=config.SUPPORT_CHAT)]
        ])
        return await message.reply_text(
            "<b>🤖 CLONING SYSTEM APPROVAL REQUIRED</b>\n\n"
            "To prevent abuse, new users require administrative approval before deploying bot clones.\n\n"
            "<i>Click the button below to send an approval request to the Owner!</i>",
            reply_markup=keyboard
        )

    # Check limits
    clones_count = await clone_db.count_documents({"owner_id": user_id})
    limit = user_data.get("clone_limit", 3)
    if clones_count >= limit:
        return await message.reply_text(
            f"<b>⚠️ LIMIT REACHED</b>\n\n"
            f"You have already deployed <b>{clones_count}/{limit}</b> bot clones. "
            f"Please delete an existing clone to create a new one, or contact support for higher limits."
        )

    # Initiate cloning wizard
    clone_states[user_id] = {"step": "waiting_for_session"}
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Setup", callback_data="clone_cancel")]])
    await message.reply_text(
        "<b>✨ <u>CLONING SETUP WIZARD</u> - STEP 1/3</b>\n\n"
        "To get started, please send your <b>Assistant Pyrogram Session String</b>.\n\n"
        "💡 <i>Why is this mandatory? This allows your clone's dedicated assistant to join video chats and stream music on your behalf.</i>\n\n"
        "Send `/cancel` or click below to cancel.",
        reply_markup=keyboard
    )


@app.on_message(filters.command(["myclone", "manage"]) & filters.private & ~BANNED_USERS)
async def manage_command(client, message: Message):
    user_id = message.from_user.id
    user_data = await get_clone_user(user_id)
    if user_data.get("status") == "suspended":
        return await message.reply_text("<b>❌ Access Denied! Your cloning privilege has been suspended by the Owner.</b>")

    clones = []
    async for c in clone_db.find({"owner_id": user_id}):
        clones.append(c)

    if not clones:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🤖 Deploy New Clone", callback_data="clone_wizard_start")]
        ])
        return await message.reply_text(
            "<b>🛠️ CLONE MANAGEMENT PANEL</b>\n\n"
            "You don't have any active bot clones yet! Deploy one in seconds by clicking below.",
            reply_markup=keyboard
        )

    text = f"<b>✨ <u>YOUR CLONED BOTS ({len(clones)}/{user_data.get('clone_limit', 3)})</u></b>\n\n" \
           f"Select a clone from the list below to manage its settings, view real-time logs, or change link configs:"

    buttons = []
    for c in clones:
        username = c.get("bot_username", "Unknown")
        bot_id = c.get("bot_id")
        status_emoji = "✅" if c.get("status") == "active" else "❌"
        buttons.append([InlineKeyboardButton(f"🤖 @{username} {status_emoji}", callback_data=f"clone_manage_{bot_id}")])

    buttons.append([InlineKeyboardButton("➕ Deploy New Clone", callback_data="clone_wizard_start")])
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))


# --- STATE INPUT HANDLER FOR PRIVATE CHATS ---

@app.on_message(filters.private & ~BANNED_USERS, group=-1)
async def state_handler(client, message: Message):
    user_id = message.from_user.id
    state = clone_states.get(user_id)
    if not state:
        return

    text = message.text.strip() if message.text else None
    if not text:
        return

    # Allow users to escape the flow
    if text.startswith("/start"):
        clone_states.pop(user_id, None)
        return

    if text.lower() == "/cancel" or text == "❌ Cancel Setup":
        clone_states.pop(user_id, None)
        return await message.reply_text("<b>❌ Setup wizard has been cancelled successfully.</b>")

    # Stop message from reaching other handlers
    message.stop_propagation()

    step = state["step"]

    if step == "waiting_for_session":
        # Validate assistant session string
        mystic = await message.reply_text("<b>🔍 Validating Assistant session... Please wait.</b>")
        val_res = await validate_assistant_session(text)
        if not val_res["valid"]:
            await mystic.delete()
            return await message.reply_text(
                f"<b>❌ INVALID SESSION STRING</b>\n\n"
                f"<b>Error Details:</b> <code>{val_res['error']}</code>\n\n"
                f"Please ensure the session string is valid and try sending it again, or send `/cancel`."
            )

        state["session_string"] = text
        state["assistant_username"] = val_res["username"]
        state["assistant_id"] = val_res["user_id"]
        state["step"] = "waiting_for_token"

        await mystic.delete()
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Setup", callback_data="clone_cancel")]])
        await message.reply_text(
            f"<b>✅ ASSISTANT CONNECTED!</b>\n\n"
            f"<b>Assistant Name:</b> {val_res['name']}\n"
            f"<b>Assistant Username:</b> @{val_res['username'] or 'None'}\n"
            f"<b>Assistant ID:</b> <code>{val_res['user_id']}</code>\n\n"
            f"<b>✨ SETUP WIZARD - STEP 2/3</b>\n"
            f"Now, please send your <b>Bot Token</b> which you generated from @BotFather.",
            reply_markup=keyboard
        )

    elif step == "waiting_for_token":
        bot_token = text
        if ":" not in bot_token:
            return await message.reply_text("<b>⚠️ Invalid token format. Please send a valid Bot Token from @BotFather.</b>")

        if bot_token == config.BOT_TOKEN:
            return await message.reply_text("<b>⚠️ You cannot clone the main bot! Please use a unique token.</b>")

        # Check duplication
        existing = await clone_db.find_one({"bot_token": bot_token})
        if existing:
            return await message.reply_text("<b>⚠️ This bot token is already cloned by another user! Try a different one.</b>")

        mystic = await message.reply_text("<b>🔍 Validating Bot Token... Please wait.</b>")
        val_res = await validate_bot_token(bot_token)
        if not val_res["valid"]:
            await mystic.delete()
            return await message.reply_text(
                f"<b>❌ INVALID BOT TOKEN</b>\n\n"
                f"<b>Error Details:</b> <code>{val_res['error']}</code>\n\n"
                f"Please verify with @BotFather and send the token again."
            )

        state["bot_token"] = bot_token
        state["bot_id"] = val_res["bot_id"]
        state["bot_username"] = val_res["username"]
        state["bot_name"] = val_res["name"]
        state["step"] = "waiting_for_group"

        await mystic.delete()
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭️ Skip / Setup Later", callback_data="clone_skip_group")],
            [InlineKeyboardButton("❌ Cancel Setup", callback_data="clone_cancel")]
        ])
        await message.reply_text(
            f"<b>✅ BOT VERIFIED!</b>\n\n"
            f"<b>Bot Name:</b> {val_res['name']}\n"
            f"<b>Bot Username:</b> @{val_res['username']}\n\n"
            f"<b>✨ SETUP WIZARD - STEP 3/3</b>\n"
            f"Please send your target <b>Group Chat ID</b> (e.g., `-100123456789`) where this music bot should operate by default.\n"
            f"<i>Or click below to skip this and bind groups/channels later.</i>",
            reply_markup=keyboard
        )

    elif step == "waiting_for_group":
        try:
            group_id = int(text)
        except ValueError:
            return await message.reply_text("<b>⚠️ Invalid Group ID format. Please send an integer ID or send `/cancel`.</b>")

        state["group_id"] = group_id
        state["step"] = "waiting_for_channel"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭️ Skip / Setup Later", callback_data="clone_skip_channel")],
            [InlineKeyboardButton("❌ Cancel Setup", callback_data="clone_cancel")]
        ])
        await message.reply_text(
            f"<b>✅ Group ID set to:</b> <code>{group_id}</code>\n\n"
            f"Now, optionally send your <b>Channel ID</b> for linked plays, or click skip to finalize deployment.",
            reply_markup=keyboard
        )

    elif step == "waiting_for_channel":
        try:
            channel_id = int(text)
        except ValueError:
            return await message.reply_text("<b>⚠️ Invalid Channel ID format. Please send an integer ID or send `/cancel`.</b>")

        state["channel_id"] = channel_id

        # Complete wizard
        await finalize_clone_setup(user_id, state, message)

    elif step == "updating_token":
        bot_id = state["bot_id"]
        mystic = await message.reply_text("<b>🔄 Validating and updating bot token...</b>")
        val_res = await validate_bot_token(text)
        if not val_res["valid"]:
            await mystic.delete()
            return await message.reply_text(f"<b>❌ Error updating token:</b> <code>{val_res['error']}</code>\n\nTry again.")

        await clone_db.update_one(
            {"bot_id": bot_id},
            {"$set": {"bot_token": text, "bot_username": val_res["username"], "updated_at": time.time()}}
        )
        clone_states.pop(user_id, None)
        await mystic.delete()

        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Back to Control Panel", callback_data=f"clone_manage_{bot_id}")]])
        await message.reply_text("<b>🎉 BOT TOKEN UPDATED SUCCESSFULLY!</b>\n\nPlease restart your clone bot to apply modifications.", reply_markup=keyboard)

    elif step == "updating_assistant":
        bot_id = state["bot_id"]
        mystic = await message.reply_text("<b>🔄 Validating and updating assistant session...</b>")
        val_res = await validate_assistant_session(text)
        if not val_res["valid"]:
            await mystic.delete()
            return await message.reply_text(f"<b>❌ Error updating assistant:</b> <code>{val_res['error']}</code>\n\nTry again.")

        await clone_db.update_one(
            {"bot_id": bot_id},
            {"$set": {
                "session_string": text,
                "assistant_username": val_res["username"],
                "assistant_id": val_res["user_id"],
                "updated_at": time.time()
            }}
        )
        clone_states.pop(user_id, None)
        await mystic.delete()

        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Back to Control Panel", callback_data=f"clone_manage_{bot_id}")]])
        await message.reply_text("<b>🎉 ASSISTANT SESSION UPDATED SUCCESSFULLY!</b>\n\nPlease restart your clone bot to apply modifications.", reply_markup=keyboard)

    elif step == "updating_group":
        bot_id = state["bot_id"]
        try:
            group_id = int(text)
        except ValueError:
            return await message.reply_text("<b>⚠️ Invalid Group ID format. Please send an integer ID.</b>")

        await clone_db.update_one({"bot_id": bot_id}, {"$set": {"group_id": group_id, "updated_at": time.time()}})
        clone_states.pop(user_id, None)
        await add_clone_log(bot_id, f"Linked Group Chat changed to {group_id}")

        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Back to Control Panel", callback_data=f"clone_manage_{bot_id}")]])
        await message.reply_text("<b>🎉 LINKED GROUP UPDATED!</b>", reply_markup=keyboard)

    elif step == "updating_channel":
        bot_id = state["bot_id"]
        try:
            channel_id = int(text)
        except ValueError:
            return await message.reply_text("<b>⚠️ Invalid Channel ID format. Please send an integer ID.</b>")

        await clone_db.update_one({"bot_id": bot_id}, {"$set": {"channel_id": channel_id, "updated_at": time.time()}})
        clone_states.pop(user_id, None)
        await add_clone_log(bot_id, f"Linked Channel changed to {channel_id}")

        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Back to Control Panel", callback_data=f"clone_manage_{bot_id}")]])
        await message.reply_text("<b>🎉 LINKED CHANNEL UPDATED!</b>", reply_markup=keyboard)

    elif step == "transferring_clone":
        bot_id = state["bot_id"]
        try:
            target_user_id = int(text)
        except ValueError:
            return await message.reply_text("<b>⚠️ Invalid User ID format.</b>")

        target_user = await get_clone_user(target_user_id)
        if target_user.get("status") == "suspended":
            return await message.reply_text("<b>❌ Error: The target user is suspended!</b>")

        await clone_db.update_one({"bot_id": bot_id}, {"$set": {"owner_id": target_user_id, "updated_at": time.time()}})
        clone_states.pop(user_id, None)
        await add_clone_log(bot_id, f"Ownership transferred to user ID {target_user_id}")

        await message.reply_text(f"<b>🎉 SUCCESS!</b>\n\nCloned bot ownership has been transferred to user ID <code>{target_user_id}</code>.")

    elif step == "renaming_clone":
        bot_id = state["bot_id"]
        await clone_db.update_one({"bot_id": bot_id}, {"$set": {"custom_name": text, "updated_at": time.time()}})
        clone_states.pop(user_id, None)
        await add_clone_log(bot_id, f"Clone renamed to: {text}")

        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Back to Control Panel", callback_data=f"clone_manage_{bot_id}")]])
        await message.reply_text("<b>🎉 CLONED BOT DISPLAY NAME UPDATED!</b>", reply_markup=keyboard)


async def finalize_clone_setup(user_id: int, state: dict, message_or_cb):
    bot_token = state["bot_token"]
    bot_id = state["bot_id"]
    bot_username = state["bot_username"]
    session_string = state["session_string"]
    assistant_username = state["assistant_username"]
    assistant_id = state["assistant_id"]
    group_id = state.get("group_id")
    channel_id = state.get("channel_id")

    # Save to MongoDB
    doc = {
        "owner_id": user_id,
        "bot_id": bot_id,
        "bot_token": bot_token,
        "bot_username": bot_username,
        "username": bot_username,
        "custom_name": state["bot_name"],
        "name": state["bot_name"],
        "session_string": session_string,
        "assistant_username": assistant_username,
        "assistant_id": assistant_id,
        "group_id": group_id,
        "channel_id": channel_id,
        "status": "active",
        "premium_expiry": "lifetime",
        "created_at": time.time(),
        "updated_at": time.time(),
        "logs": []
    }
    await clone_db.update_one({"bot_id": bot_id}, {"$set": doc}, upsert=True)
    await add_clone_log(bot_id, "Clone configured and initialized.")

    clone_states.pop(user_id, None)

    # Start instance
    mystic_text = "<b>⚙️ STARTING YOUR INSTANCE...</b>\nThis will load commands and spin up PyTgCalls."
    if isinstance(message_or_cb, CallbackQuery):
        await message_or_cb.message.edit_text(mystic_text)
        msg = message_or_cb.message
    else:
        msg = await message_or_cb.reply_text(mystic_text)

    cloned_bot, ass_err = await start_clone(bot_token, user_id, session_string)

    success_text = (
        f"<b>🚀 <u>CLONE DEPLOYED SUCCESSFULLY!</u></b>\n\n"
        f"<b>Bot Name:</b> {state['bot_name']}\n"
        f"<b>Bot Username:</b> @{bot_username}\n"
        f"<b>Assistant Username:</b> @{assistant_username or 'None'}\n"
    )
    if ass_err:
        success_text += f"\n<b>⚠️ WARNING:</b> Assistant stream failed to start: <code>{ass_err}</code>\n<i>Verify that your assistant account is not locked or has active restrictions.</i>"
    else:
        success_text += f"\n<b>✅ STATUS:</b> Running and listening for streams."

    success_text += f"\n\n👉 <b>Add @{bot_username} to your group to begin playing music!</b>"

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🛠️ Manage Bot", callback_data=f"clone_manage_{bot_id}")]])
    await msg.edit_text(success_text, reply_markup=keyboard)


# --- CALLBACK QUERIES HANDLER ---

@app.on_callback_query(filters.regex("^(clone_|manage_clones_btn)") & ~BANNED_USERS)
@wrap_callback
async def clone_callbacks(client, CallbackQuery: CallbackQuery):
    user_id = CallbackQuery.from_user.id
    data = CallbackQuery.data

    # Main Wizard entry callback
    if data in ["clone_wizard_start", "clone_bot_btn"]:
        user_data = await get_clone_user(user_id)
        if user_data.get("status") == "suspended":
            return await CallbackQuery.answer("❌ Your cloning access has been suspended!", show_alert=True)

        # Check limits
        clones_count = await clone_db.count_documents({"owner_id": user_id})
        limit = user_data.get("clone_limit", 3)
        if clones_count >= limit:
            return await CallbackQuery.answer(f"⚠️ Limit reached ({clones_count}/{limit})! Delete a clone first.", show_alert=True)

        clone_states[user_id] = {"step": "waiting_for_session"}
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Setup", callback_data="clone_cancel")]])
        await CallbackQuery.edit_message_text(
            "<b>✨ <u>CLONING SETUP WIZARD</u> - STEP 1/3</b>\n\n"
            "Please send your **Assistant Pyrogram Session String**.\n\n"
            "💬 <i>This string session is required so your clone's assistant can connect and play stream audio in group voice calls.</i>",
            reply_markup=keyboard
        )

    elif data == "manage_clones_btn":
        user_data = await get_clone_user(user_id)
        if user_data.get("status") == "suspended":
            return await CallbackQuery.answer("❌ Your cloning access has been suspended!", show_alert=True)

        clones = []
        async for c in clone_db.find({"owner_id": user_id}):
            clones.append(c)

        if not clones:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🤖 Deploy New Clone", callback_data="clone_wizard_start")],
                [InlineKeyboardButton("⌯ Back to Menu ⌯", callback_data="settingsback_helper")]
            ])
            return await CallbackQuery.edit_message_text(
                "<b>🛠️ CLONE MANAGEMENT PANEL</b>\n\n"
                "You don't have any active bot clones yet! Deploy one in seconds by clicking below.",
                reply_markup=keyboard
            )

        text = f"<b>✨ <u>YOUR CLONED BOTS ({len(clones)}/{user_data.get('clone_limit', 3)})</u></b>\n\n" \
               f"Select a clone from the list below to manage its settings, view real-time logs, or change link configs:"

        buttons = []
        for c in clones:
            username = c.get("bot_username", "Unknown")
            bot_id = c.get("bot_id")
            status_emoji = "✅" if c.get("status") == "active" else "❌"
            buttons.append([InlineKeyboardButton(f"🤖 @{username} {status_emoji}", callback_data=f"clone_manage_{bot_id}")])

        buttons.append([InlineKeyboardButton("➕ Deploy New Clone", callback_data="clone_wizard_start")])
        buttons.append([InlineKeyboardButton("⌯ Back to Menu ⌯", callback_data="settingsback_helper")])
        await CallbackQuery.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))

    elif data == "settingsback_helper":
        # Return to main start menu
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

    elif data == "clone_req_approval":
        # Send notification to main owner
        try:
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"clone_owner_approve_{user_id}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"clone_owner_reject_{user_id}")
                ]
            ])
            await client.send_message(
                chat_id=config.OWNER_ID,
                text=f"<b>📨 NEW CLONE REQUEST</b>\n\n"
                     f"• <b>User:</b> {CallbackQuery.from_user.mention}\n"
                     f"• <b>ID:</b> <code>{user_id}</code>\n"
                     f"• <b>Username:</b> @{CallbackQuery.from_user.username or 'None'}\n\n"
                     f"Please select an option below:",
                reply_markup=keyboard
            )
            await CallbackQuery.edit_message_text(
                "<b>📨 APPROVAL REQUEST SENT!</b>\n\n"
                "Your request has been dispatched to the Bot Owner. Please wait for confirmation.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Get Support", url=config.SUPPORT_CHAT)]])
            )
        except Exception as e:
            await CallbackQuery.answer(f"❌ Failed to notify Owner: {e}", show_alert=True)

    elif data == "clone_cancel":
        clone_states.pop(user_id, None)
        await CallbackQuery.answer("❌ Action cancelled.", show_alert=True)
        # Return to clones list
        return await manage_clones_from_cb(CallbackQuery, user_id)

    elif data == "clone_skip_group":
        state = clone_states.get(user_id)
        if not state:
            return await CallbackQuery.answer("❌ Active state expired. Restart setup.", show_alert=True)
        state["group_id"] = None
        state["step"] = "waiting_for_channel"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭️ Skip / Setup Later", callback_data="clone_skip_channel")],
            [InlineKeyboardButton("❌ Cancel Setup", callback_data="clone_cancel")]
        ])
        await CallbackQuery.edit_message_text(
            "<b>✅ Group Linkage Skipped.</b>\n\nNow, optionally send your <b>Channel ID</b> for linked streams:",
            reply_markup=keyboard
        )

    elif data == "clone_skip_channel":
        state = clone_states.get(user_id)
        if not state:
            return await CallbackQuery.answer("❌ Active state expired.", show_alert=True)
        state["channel_id"] = None
        await finalize_clone_setup(user_id, state, CallbackQuery)

    elif data.startswith("clone_manage_"):
        bot_id = int(data.split("_")[2])
        clone = await clone_db.find_one({"bot_id": bot_id})
        if not clone:
            return await CallbackQuery.answer("❌ Clone not found in database!", show_alert=True)

        # Access check: Only owner or main admin
        if clone["owner_id"] != user_id and user_id != config.OWNER_ID:
            return await CallbackQuery.answer("🔒 Permission Denied! This clone is not yours.", show_alert=True)

        is_running = bot_id in cloned_bots
        status_text = "Running ✅" if is_running else "Stopped ❌"
        if clone.get("status") == "suspended":
            status_text = "Suspended ⚠️"

        custom_name = clone.get("custom_name", "Unknown Clone")
        text = (
            f"<b>⚙️ <u>MANAGE CLONE: {custom_name}</u></b>\n\n"
            f"• <b>Bot Username:</b> @{clone.get('bot_username')}\n"
            f"• <b>Assistant Username:</b> @{clone.get('assistant_username') or 'None'}\n"
            f"• <b>Linked Group ID:</b> <code>{clone.get('group_id') or 'Not Linked'}</code>\n"
            f"• <b>Linked Channel ID:</b> <code>{clone.get('channel_id') or 'Not Linked'}</code>\n"
            f"• <b>Instance Status:</b> <code>{status_text}</code>\n"
            f"• <b>Premium Validity:</b> <code>{clone.get('premium_expiry', 'Lifetime')}</code>\n"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("▶️ Start" if not is_running else "⏸️ Stop", callback_data=f"clone_action_toggle_{bot_id}"),
                InlineKeyboardButton("🔄 Restart", callback_data=f"clone_action_restart_{bot_id}")
            ],
            [
                InlineKeyboardButton("🔗 Change Group", callback_data=f"clone_action_setgroup_{bot_id}"),
                InlineKeyboardButton("📢 Change Channel", callback_data=f"clone_action_setchannel_{bot_id}")
            ],
            [
                InlineKeyboardButton("🔐 Update Token", callback_data=f"clone_action_updatetoken_{bot_id}"),
                InlineKeyboardButton("👤 Update Assistant", callback_data=f"clone_action_updateass_{bot_id}")
            ],
            [
                InlineKeyboardButton("📁 Logs", callback_data=f"clone_action_logs_{bot_id}"),
                InlineKeyboardButton("👑 Transfer", callback_data=f"clone_action_transfer_{bot_id}")
            ],
            [
                InlineKeyboardButton("📝 Rename", callback_data=f"clone_action_rename_{bot_id}"),
                InlineKeyboardButton("🧹 Sync", callback_data=f"clone_action_sync_{bot_id}")
            ],
            [
                InlineKeyboardButton("❌ Delete Clone Bot", callback_data=f"clone_action_delete_{bot_id}")
            ],
            [
                InlineKeyboardButton("⌯ Back to Clones ⌯", callback_data="manage_clones_btn")
            ]
        ])
        await CallbackQuery.edit_message_text(text, reply_markup=keyboard)

    elif data.startswith("clone_action_"):
        parts = data.split("_")
        action = parts[2]
        bot_id = int(parts[3])

        clone = await clone_db.find_one({"bot_id": bot_id})
        if not clone:
            return await CallbackQuery.answer("❌ Clone records not found!", show_alert=True)

        if clone["owner_id"] != user_id and user_id != config.OWNER_ID:
            return await CallbackQuery.answer("🔒 Permission Denied!", show_alert=True)

        if action == "toggle":
            is_running = bot_id in cloned_bots
            if is_running:
                await stop_clone_instance(bot_id)
                await clone_db.update_one({"bot_id": bot_id}, {"$set": {"status": "stopped"}})
                await add_clone_log(bot_id, "Clone instance manually stopped.")
                await CallbackQuery.answer("⏸️ Clone stopped successfully!", show_alert=True)
            else:
                if clone.get("status") == "suspended":
                    return await CallbackQuery.answer("⚠️ This clone is suspended by the admin and cannot be started.", show_alert=True)

                await CallbackQuery.answer("⚡ Initializing startup...")
                cb_bot, err = await start_clone(clone["bot_token"], clone["owner_id"], clone["session_string"])
                if cb_bot:
                    await clone_db.update_one({"bot_id": bot_id}, {"$set": {"status": "active"}})
                    await CallbackQuery.answer("✅ Clone bot is now online and active!", show_alert=True)
                else:
                    await CallbackQuery.answer(f"❌ Failed to start: {err}", show_alert=True)

            # Refresh management panel
            return await refresh_management_panel(CallbackQuery, bot_id)

        elif action == "restart":
            await CallbackQuery.answer("⚡ Restarting your clone instance...")
            await stop_clone_instance(bot_id)
            cb_bot, err = await start_clone(clone["bot_token"], clone["owner_id"], clone["session_string"])
            if cb_bot:
                await clone_db.update_one({"bot_id": bot_id}, {"$set": {"status": "active"}})
                await add_clone_log(bot_id, "Clone instance manually restarted.")
                await CallbackQuery.answer("✅ Restart complete! Instance is fully operational.", show_alert=True)
            else:
                await CallbackQuery.answer(f"❌ Restart failed: {err}", show_alert=True)

            return await refresh_management_panel(CallbackQuery, bot_id)

        elif action == "logs":
            logs = clone.get("logs", [])
            if not logs:
                logs_text = "<i>No actions logged yet for this clone.</i>"
            else:
                logs_text = "\n".join(logs[-15:])

            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Back", callback_data=f"clone_manage_{bot_id}")]])
            await CallbackQuery.edit_message_text(
                f"<b>📋 REAL-TIME ACTION LOGS: @{clone['bot_username']}</b>\n\n"
                f"<pre>{logs_text}</pre>",
                reply_markup=keyboard
            )

        elif action == "sync":
            # Force re-bind and handlers registration
            await CallbackQuery.answer("🔄 Synchronizing handlers with main app...", show_alert=True)
            if bot_id in cloned_bots:
                cli = cloned_bots[bot_id]
                cli.dispatcher.groups.clear()
                for group, handlers in _main_app.dispatcher.groups.items():
                    for handler in handlers:
                        cli.add_handler(handler, group)
                await add_clone_log(bot_id, "Handlers synchronized successfully.")
                await CallbackQuery.answer("✅ Synchronization completed successfully!", show_alert=True)
            else:
                await CallbackQuery.answer("⚠️ Bot is offline. Start the bot first to synchronize handlers.", show_alert=True)

        elif action == "updatetoken":
            clone_states[user_id] = {"step": "updating_token", "bot_id": bot_id}
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"clone_manage_{bot_id}")]])
            await CallbackQuery.edit_message_text(
                "<b>🔐 UPDATE BOT TOKEN</b>\n\n"
                "Please send the new bot token from @BotFather:",
                reply_markup=keyboard
            )

        elif action == "updateass":
            clone_states[user_id] = {"step": "updating_assistant", "bot_id": bot_id}
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"clone_manage_{bot_id}")]])
            await CallbackQuery.edit_message_text(
                "<b>👤 UPDATE ASSISTANT SESSION STRING</b>\n\n"
                "Please send the new Pyrogram string session string for the assistant:",
                reply_markup=keyboard
            )

        elif action == "setgroup":
            clone_states[user_id] = {"step": "updating_group", "bot_id": bot_id}
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"clone_manage_{bot_id}")]])
            await CallbackQuery.edit_message_text(
                "<b>🔗 LINK GROUP CHAT</b>\n\n"
                "Please send the numerical <b>Group Chat ID</b> (e.g., `-100123456789`):",
                reply_markup=keyboard
            )

        elif action == "setchannel":
            clone_states[user_id] = {"step": "updating_channel", "bot_id": bot_id}
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"clone_manage_{bot_id}")]])
            await CallbackQuery.edit_message_text(
                "<b>📢 LINK PLAYBACK CHANNEL</b>\n\n"
                "Please send the numerical <b>Channel ID</b>:",
                reply_markup=keyboard
            )

        elif action == "rename":
            clone_states[user_id] = {"step": "renaming_clone", "bot_id": bot_id}
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"clone_manage_{bot_id}")]])
            await CallbackQuery.edit_message_text(
                "<b>📝 RENAME CLONE BOT</b>\n\n"
                "Please send the new nickname or custom display name for your clone:",
                reply_markup=keyboard
            )

        elif action == "transfer":
            clone_states[user_id] = {"step": "transferring_clone", "bot_id": bot_id}
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"clone_manage_{bot_id}")]])
            await CallbackQuery.edit_message_text(
                "<b>👑 TRANSFER OWNERSHIP</b>\n\n"
                "Please send the numerical <b>Telegram User ID</b> of the new owner:",
                reply_markup=keyboard
            )

        elif action == "delete":
            # Direct double-check keyboard
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("⚠️ Confirm Permanent Delete", callback_data=f"clone_action_confirmdelete_{bot_id}"),
                    InlineKeyboardButton("❌ Cancel", callback_data=f"clone_manage_{bot_id}")
                ]
            ])
            await CallbackQuery.edit_message_text(
                "<b>💥 CONFIRM DESTRUCTION</b>\n\n"
                "Are you absolutely sure you want to permanently delete this clone?\n\n"
                "<i>This will stop the bot, disconnect the assistant, clear memory database, and remove all customized links. This action cannot be undone.</i>",
                reply_markup=keyboard
            )

        elif action == "confirmdelete":
            await stop_clone_instance(bot_id)
            await clone_db.delete_one({"bot_id": bot_id})
            await CallbackQuery.answer("💥 Clone permanently deleted!", show_alert=True)
            # Return to main clones manager list
            return await manage_clones_from_cb(CallbackQuery, user_id)

    # Owner-only administrative callbacks
    elif data.startswith("clone_owner_"):
        if user_id != config.OWNER_ID:
            return await CallbackQuery.answer("🔒 Permission Denied! Owner Only.", show_alert=True)

        parts = data.split("_")
        action = parts[2]
        target_id = int(parts[3])

        if action == "approve":
            await clone_users_db.update_one({"user_id": target_id}, {"$set": {"status": "approved"}})
            await CallbackQuery.answer("✅ User approved for cloning!", show_alert=True)
            await CallbackQuery.message.edit_text(f"<b>✅ APPROVED USER</b>\n\nUser ID <code>{target_id}</code> is now permitted to create bot clones.")
            try:
                await client.send_message(
                    chat_id=target_id,
                    text="<b>🎉 CONGRATULATIONS!</b>\n\n"
                         "Your request to deploy bot clones has been **Approved** by the Admin!\n"
                         "Use `/clone` in private chat to start your configuration wizard."
                )
            except:
                pass

        elif action == "reject":
            await clone_users_db.update_one({"user_id": target_id}, {"$set": {"status": "rejected"}})
            await CallbackQuery.answer("❌ User rejected.", show_alert=True)
            await CallbackQuery.message.edit_text(f"<b>❌ REJECTED USER</b>\n\nUser ID <code>{target_id}</code> request has been rejected.")
            try:
                await client.send_message(
                    chat_id=target_id,
                    text="<b>❌ REQUEST REJECTED</b>\n\n"
                         "Your request to deploy bot clones was rejected by the Owner."
                )
            except:
                pass

    # Administrative Dashboard Callbacks
    elif data.startswith("clone_admin_"):
        if user_id != config.OWNER_ID:
            return await CallbackQuery.answer("🔒 Administrative function only!", show_alert=True)

        action = data.split("_")[2]

        if action == "list":
            sub_action = data.split("_")[3]
            if sub_action == "clones":
                clones_list = []
                async for c in clone_db.find():
                    clones_list.append(c)

                if not clones_list:
                    return await CallbackQuery.answer("No clones registered.", show_alert=True)

                text = "<b>🤖 REGISTERED CLONE INSTANCES:</b>\n\n"
                buttons = []
                for c in clones_list:
                    bot_id = c["bot_id"]
                    is_running = bot_id in cloned_bots
                    status_emoji = "✅" if is_running else "❌"
                    text += f"• @{c['bot_username']} ({status_emoji}) - Owner ID: <code>{c['owner_id']}</code>\n"
                    buttons.append([InlineKeyboardButton(f"🤖 @{c['bot_username']} Details", callback_data=f"clone_manage_{bot_id}")])

                buttons.append([InlineKeyboardButton("🏠 Back to Admin Panel", callback_data="clone_admin_panel_back")])
                await CallbackQuery.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))

            elif sub_action == "pending":
                pending_users = []
                async for u in clone_users_db.find({"status": "pending"}):
                    pending_users.append(u)

                if not pending_users:
                    return await CallbackQuery.answer("No pending approval requests.", show_alert=True)

                text = "<b>📨 PENDING USER APPROVALS:</b>\n\n"
                buttons = []
                for u in pending_users[:10]: # Limit to first 10
                    tgt = u["user_id"]
                    text += f"• User ID: <code>{tgt}</code>\n"
                    buttons.append([
                        InlineKeyboardButton(f"✅ Approve {tgt}", callback_data=f"clone_owner_approve_{tgt}"),
                        InlineKeyboardButton(f"❌ Reject", callback_data=f"clone_owner_reject_{tgt}")
                    ])

                buttons.append([InlineKeyboardButton("🏠 Back to Admin Panel", callback_data="clone_admin_panel_back")])
                await CallbackQuery.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))

        elif action == "toggle":
            settings = await get_clone_settings()
            new_val = not settings.get("approval_required", True)
            await update_clone_settings("approval_required", new_val)
            await CallbackQuery.answer(f"✅ Approval requirement toggled to {new_val}!", show_alert=True)
            return await show_admin_panel_cb(CallbackQuery)

        elif action == "sys":
            uptime = time.time() - _boot_
            days, remainder = divmod(int(uptime), 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime_str = f"{days}d {hours}h {minutes}m {seconds}s" if days > 0 else f"{hours}h {minutes}m {seconds}s"

            cpu = psutil.cpu_percent()
            ram = psutil.virtual_memory().percent
            disk = psutil.disk_usage('/').percent

            metrics_text = (
                f"📈 Real-Time Server Performance Metrics:\n\n"
                f"• System Uptime: {uptime_str}\n"
                f"• CPU Load: {cpu}%\n"
                f"• RAM Load: {ram}%\n"
                f"• Disk Load: {disk}%"
            )
            await CallbackQuery.answer(metrics_text, show_alert=True)

        elif action == "sync":
            await CallbackQuery.answer("🔄 Syncing handlers...")
            success = 0
            for bot_id, cli in list(cloned_bots.items()):
                try:
                    cli.dispatcher.groups.clear()
                    for group, handlers in _main_app.dispatcher.groups.items():
                        for handler in handlers:
                            cli.add_handler(handler, group)
                    success += 1
                except:
                    pass
            await CallbackQuery.answer(f"✅ Successfully synchronized {success} running clones!", show_alert=True)

        elif action == "restart":
            await CallbackQuery.answer("⚡ Restarting all clones...")
            success = 0
            async for c in clone_db.find({"status": "active"}):
                bot_id = c["bot_id"]
                try:
                    await stop_clone_instance(bot_id)
                    cb_bot, _ = await start_clone(c["bot_token"], c["owner_id"], c["session_string"])
                    if cb_bot:
                        success += 1
                except:
                    pass
            await CallbackQuery.answer(f"✅ Successfully restarted {success} active clones!", show_alert=True)

        elif action == "panel" and data.endswith("_back"):
            return await show_admin_panel_cb(CallbackQuery)


async def show_admin_panel_cb(CallbackQuery: CallbackQuery):
    total_clones = await clone_db.count_documents({})
    active_clones = len(cloned_bots)
    pending_users = await clone_users_db.count_documents({"status": "pending"})
    suspended_users = await clone_users_db.count_documents({"status": "suspended"})

    settings = await get_clone_settings()
    app_req = "Enabled ✅" if settings.get("approval_required", True) else "Disabled ❌"
    def_limit = settings.get("default_clone_limit", 3)

    text = (
        f"<b>👑 <u>CLONES SYSTEM OWNER DASHBOARD</u></b>\n\n"
        f"• <b>Total Saved Clones:</b> {total_clones}\n"
        f"• <b>Active Running Clones:</b> {active_clones}\n"
        f"• <b>Pending Approvals:</b> {pending_users}\n"
        f"• <b>Suspended Users:</b> {suspended_users}\n\n"
        f"<b>🛠️ SYSTEM SETTINGS:</b>\n"
        f"• <b>Approval Required:</b> {app_req}\n"
        f"• <b>Default Clone Limit:</b> <code>{def_limit}</code>\n\n"
        f"<i>Select an administrative function below to manage your clone ecosystem:</i>"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🤖 Registered Clones", callback_data="clone_admin_list_clones"),
            InlineKeyboardButton("📨 Pending Approvals", callback_data="clone_admin_list_pending")
        ],
        [
            InlineKeyboardButton("⚙️ Toggle Approval", callback_data="clone_admin_toggle_appreq"),
            InlineKeyboardButton("📈 System Metrics", callback_data="clone_admin_sys_stats")
        ],
        [
            InlineKeyboardButton("📢 Global Sync", callback_data="clone_admin_sync_all"),
            InlineKeyboardButton("🔄 Restart Clones", callback_data="clone_admin_restart_all")
        ],
        [
            InlineKeyboardButton("❌ Close Panel", callback_data="clone_cancel")
        ]
    ])
    try:
        await CallbackQuery.edit_message_text(text, reply_markup=keyboard)
    except Exception:
        pass


# --- VIEW LOGS / SYNC TRANSITIONS ---

async def manage_clones_from_cb(CallbackQuery: CallbackQuery, user_id: int):
    user_data = await get_clone_user(user_id)
    clones = []
    async for c in clone_db.find({"owner_id": user_id}):
        clones.append(c)

    if not clones:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🤖 Deploy New Clone", callback_data="clone_wizard_start")],
            [InlineKeyboardButton("⌯ Back to Menu ⌯", callback_data="settingsback_helper")]
        ])
        return await CallbackQuery.edit_message_text(
            "<b>🛠️ CLONE MANAGEMENT PANEL</b>\n\n"
            "You don't have any active bot clones yet! Deploy one in seconds by clicking below.",
            reply_markup=keyboard
        )

    text = f"<b>✨ <u>YOUR CLONED BOTS ({len(clones)}/{user_data.get('clone_limit', 3)})</u></b>\n\n" \
           f"Select a clone from the list below to manage its settings, view real-time logs, or change link configs:"

    buttons = []
    for c in clones:
        username = c.get("bot_username", "Unknown")
        bot_id = c.get("bot_id")
        status_emoji = "✅" if c.get("status") == "active" else "❌"
        buttons.append([InlineKeyboardButton(f"🤖 @{username} {status_emoji}", callback_data=f"clone_manage_{bot_id}")])

    buttons.append([InlineKeyboardButton("➕ Deploy New Clone", callback_data="clone_wizard_start")])
    buttons.append([InlineKeyboardButton("⌯ Back to Menu ⌯", callback_data="settingsback_helper")])
    try:
        await CallbackQuery.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    except Exception:
        pass


async def refresh_management_panel(CallbackQuery: CallbackQuery, bot_id: int):
    clone = await clone_db.find_one({"bot_id": bot_id})
    if not clone:
        return

    is_running = bot_id in cloned_bots
    status_text = "Running ✅" if is_running else "Stopped ❌"
    if clone.get("status") == "suspended":
        status_text = "Suspended ⚠️"

    custom_name = clone.get("custom_name", "Unknown Clone")
    text = (
        f"<b>⚙️ <u>MANAGE CLONE: {custom_name}</u></b>\n\n"
        f"• <b>Bot Username:</b> @{clone.get('bot_username')}\n"
        f"• <b>Assistant Username:</b> @{clone.get('assistant_username') or 'None'}\n"
        f"• <b>Linked Group ID:</b> <code>{clone.get('group_id') or 'Not Linked'}</code>\n"
        f"• <b>Linked Channel ID:</b> <code>{clone.get('channel_id') or 'Not Linked'}</code>\n"
        f"• <b>Instance Status:</b> <code>{status_text}</code>\n"
        f"• <b>Premium Validity:</b> <code>{clone.get('premium_expiry', 'Lifetime')}</code>\n"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("▶️ Start" if not is_running else "⏸️ Stop", callback_data=f"clone_action_toggle_{bot_id}"),
            InlineKeyboardButton("🔄 Restart", callback_data=f"clone_action_restart_{bot_id}")
        ],
        [
            InlineKeyboardButton("🔗 Change Group", callback_data=f"clone_action_setgroup_{bot_id}"),
            InlineKeyboardButton("📢 Change Channel", callback_data=f"clone_action_setchannel_{bot_id}")
        ],
        [
            InlineKeyboardButton("🔐 Update Token", callback_data=f"clone_action_updatetoken_{bot_id}"),
            InlineKeyboardButton("👤 Update Assistant", callback_data=f"clone_action_updateass_{bot_id}")
        ],
        [
            InlineKeyboardButton("📁 Logs", callback_data=f"clone_action_logs_{bot_id}"),
            InlineKeyboardButton("👑 Transfer", callback_data=f"clone_action_transfer_{bot_id}")
        ],
        [
            InlineKeyboardButton("📝 Rename", callback_data=f"clone_action_rename_{bot_id}"),
            InlineKeyboardButton("🧹 Sync", callback_data=f"clone_action_sync_{bot_id}")
        ],
        [
            InlineKeyboardButton("❌ Delete Clone Bot", callback_data=f"clone_action_delete_{bot_id}")
        ],
        [
            InlineKeyboardButton("⌯ Back to Clones ⌯", callback_data="manage_clones_btn")
        ]
    ])
    try:
        await CallbackQuery.edit_message_text(text, reply_markup=keyboard)
    except Exception:
        pass


# --- CLONE OWNER SLASH COMMANDS EXTRAS ---

@app.on_message(filters.command(["connectassistant", "assistant"]) & filters.private & ~BANNED_USERS)
async def connect_assistant_cmd(client, message: Message):
    user_id = message.from_user.id
    user_data = await get_clone_user(user_id)
    if user_data.get("status") == "suspended":
        return await message.reply_text("<b>❌ Access Denied! Your cloning privilege has been suspended by the Owner.</b>")

    clones = []
    async for c in clone_db.find({"owner_id": user_id}):
        clones.append(c)

    if not clones:
        return await message.reply_text(
            "<b>💡 No active clones found!</b>\n\nUse `/clone` to configure your assistant and deploy a new bot clone."
        )

    # Directly trigger updating the first clone or show list
    bot_id = clones[0]["bot_id"]
    clone_states[user_id] = {"step": "updating_assistant", "bot_id": bot_id}
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"clone_manage_{bot_id}")]])
    await message.reply_text(
        f"<b>👤 CONNECT / UPDATE ASSISTANT</b>\n\n"
        f"Updating assistant session for cloned bot: @{clones[0]['bot_username']}\n\n"
        f"Please send the new Pyrogram string session string for the assistant:",
        reply_markup=keyboard
    )


@app.on_message(filters.command(["setgroup"]) & filters.private & ~BANNED_USERS)
async def set_group_cmd(client, message: Message):
    user_id = message.from_user.id
    clones = []
    async for c in clone_db.find({"owner_id": user_id}):
        clones.append(c)

    if not clones:
        return await message.reply_text("<b>💡 No active clones found!</b> Use `/clone` first.")

    bot_id = clones[0]["bot_id"]
    clone_states[user_id] = {"step": "updating_group", "bot_id": bot_id}
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"clone_manage_{bot_id}")]])
    await message.reply_text(
        f"<b>🔗 LINK GROUP CHAT</b>\n"
        f"Modifying linked group for @{clones[0]['bot_username']}\n\n"
        f"Please send the numerical <b>Group Chat ID</b> (e.g., `-100123456789`):",
        reply_markup=keyboard
    )


@app.on_message(filters.command(["setchannel"]) & filters.private & ~BANNED_USERS)
async def set_channel_cmd(client, message: Message):
    user_id = message.from_user.id
    clones = []
    async for c in clone_db.find({"owner_id": user_id}):
        clones.append(c)

    if not clones:
        return await message.reply_text("<b>💡 No active clones found!</b> Use `/clone` first.")

    bot_id = clones[0]["bot_id"]
    clone_states[user_id] = {"step": "updating_channel", "bot_id": bot_id}
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"clone_manage_{bot_id}")]])
    await message.reply_text(
        f"<b>📢 LINK PLAYBACK CHANNEL</b>\n"
        f"Modifying linked channel for @{clones[0]['bot_username']}\n\n"
        f"Please send the numerical <b>Channel ID</b>:",
        reply_markup=keyboard
    )


@app.on_message(filters.command(["status"]) & filters.private & ~BANNED_USERS)
async def status_clones_cmd(client, message: Message):
    user_id = message.from_user.id
    clones = []
    async for c in clone_db.find({"owner_id": user_id}):
        clones.append(c)

    if not clones:
        return await message.reply_text("<b>💡 You do not have any deployed clone bots.</b> Use `/clone` to make one!")

    text = "<b>📊 YOUR CLONED BOTS STATUS:</b>\n\n"
    for c in clones:
        bot_id = c["bot_id"]
        is_running = bot_id in cloned_bots
        status = "Running ✅" if is_running else "Stopped ❌"
        text += f"• @{c['bot_username']} - <b>{status}</b>\n" \
                f"  Linked Group: <code>{c.get('group_id') or 'None'}</code>\n" \
                f"  Linked Channel: <code>{c.get('channel_id') or 'None'}</code>\n\n"

    await message.reply_text(text)


@app.on_message(filters.command(["restart"]) & filters.private & ~BANNED_USERS)
async def restart_user_clone_cmd(client, message: Message):
    user_id = message.from_user.id
    clones = []
    async for c in clone_db.find({"owner_id": user_id}):
        clones.append(c)

    if not clones:
        return await message.reply_text("<b>💡 No active clones found!</b>")

    bot_id = clones[0]["bot_id"]
    mystic = await message.reply_text(f"<b>🔄 Restarting @{clones[0]['bot_username']}...</b>")
    await stop_clone_instance(bot_id)
    cb_bot, err = await start_clone(clones[0]["bot_token"], user_id, clones[0]["session_string"])
    if cb_bot:
        await clone_db.update_one({"bot_id": bot_id}, {"$set": {"status": "active"}})
        await add_clone_log(bot_id, "Clone instance manually restarted via slash command.")
        await mystic.edit_text(f"<b>✅ @{clones[0]['bot_username']} successfully restarted and running!</b>")
    else:
        await mystic.edit_text(f"<b>❌ Restart failed:</b> <code>{err}</code>")


@app.on_message(filters.command(["deleteclone"]) & filters.private & ~BANNED_USERS)
async def delete_user_clone_cmd(client, message: Message):
    user_id = message.from_user.id
    clones = []
    async for c in clone_db.find({"owner_id": user_id}):
        clones.append(c)

    if not clones:
        return await message.reply_text("<b>💡 No active clones found!</b>")

    bot_id = clones[0]["bot_id"]
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚠️ Permanently Delete", callback_data=f"clone_action_confirmdelete_{bot_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data="clone_cancel")
        ]
    ])
    await message.reply_text(
        f"<b>💥 DELETE CLONE BOT: @{clones[0]['bot_username']}</b>\n\n"
        f"Are you absolutely sure you want to permanently delete this clone?\n"
        f"<i>This stops the bot & assistant, and clears linked groups/channels.</i>",
        reply_markup=keyboard
    )


@app.on_message(filters.command(["help"]) & filters.private & ~BANNED_USERS)
async def help_clone_cmd(client, message: Message):
    help_text = (
        "<b>🤖 <u>CLONE SYSTEM USER HELP</u></b>\n\n"
        "You can manage your cloned music bots either via inline buttons from `/manage` or using slash commands:\n\n"
        "<b>Commands:</b>\n"
        "• `/clone` - Open setup wizard to deploy a clone.\n"
        "• `/manage` - Open full interactive clone control dashboard.\n"
        "• `/status` - Check the real-time status of your clones.\n"
        "• `/restart` - Restart your cloned bot.\n"
        "• `/assistant` - Quick update/reconnect assistant string session.\n"
        "• `/setgroup` - Quickly link group chat.\n"
        "• `/setchannel` - Quickly link playback channel.\n"
        "• `/deleteclone` - Completely delete and stop your cloned bot.\n"
        "• `/support` - Get support from the development community.\n"
    )
    await message.reply_text(help_text)


@app.on_message(filters.command(["support"]) & filters.private & ~BANNED_USERS)
async def support_clone_cmd(client, message: Message):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Support Chat", url=config.SUPPORT_CHAT)],
        [InlineKeyboardButton("📢 Support Channel", url=config.SUPPORT_CHANNEL)]
    ])
    await message.reply_text(
        "<b>💬 CLONE SYSTEM SUPPORT</b>\n\n"
        "If you are facing any configuration errors or voice chat streaming issues with your clone, join our support community below!",
        reply_markup=keyboard
    )


# --- OWNER DASHBOARD START COMMAND ---

@app.on_message(filters.command(["clonepanel", "clonespanel"]) & filters.user(config.OWNER_ID))
async def clones_panel_cmd(client, message: Message):
    total_clones = await clone_db.count_documents({})
    active_clones = len(cloned_bots)
    pending_users = await clone_users_db.count_documents({"status": "pending"})
    suspended_users = await clone_users_db.count_documents({"status": "suspended"})

    settings = await get_clone_settings()
    app_req = "Enabled ✅" if settings.get("approval_required", True) else "Disabled ❌"
    def_limit = settings.get("default_clone_limit", 3)

    text = (
        f"<b>👑 <u>CLONES SYSTEM OWNER DASHBOARD</u></b>\n\n"
        f"• <b>Total Saved Clones:</b> {total_clones}\n"
        f"• <b>Active Running Clones:</b> {active_clones}\n"
        f"• <b>Pending Approvals:</b> {pending_users}\n"
        f"• <b>Suspended Users:</b> {suspended_users}\n\n"
        f"<b>🛠️ SYSTEM SETTINGS:</b>\n"
        f"• <b>Approval Required:</b> {app_req}\n"
        f"• <b>Default Clone Limit:</b> <code>{def_limit}</code>\n\n"
        f"<i>Select an administrative function below to manage your clone ecosystem:</i>"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🤖 Registered Clones", callback_data="clone_admin_list_clones"),
            InlineKeyboardButton("📨 Pending Approvals", callback_data="clone_admin_list_pending")
        ],
        [
            InlineKeyboardButton("⚙️ Toggle Approval", callback_data="clone_admin_toggle_appreq"),
            InlineKeyboardButton("📈 System Metrics", callback_data="clone_admin_sys_stats")
        ],
        [
            InlineKeyboardButton("📢 Global Sync", callback_data="clone_admin_sync_all"),
            InlineKeyboardButton("🔄 Restart Clones", callback_data="clone_admin_restart_all")
        ],
        [
            InlineKeyboardButton("❌ Close Panel", callback_data="clone_cancel")
        ]
    ])

    await message.reply_text(text, reply_markup=keyboard)
