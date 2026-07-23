import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from SONALI_MUSIC import app, _main_app, wrap_callback
from SONALI_MUSIC.core.mongo import mongodb
import config

clone_db = mongodb.clones
cloned_bots = {}  # { bot_id: Client }

async def start_clone(bot_token: str, owner_id: int):
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
        return client
    except Exception as e:
        print(f"Error starting cloned bot with token {bot_token}: {e}")
        return None


@app.on_message(filters.command(["clone"]) & filters.private)
async def clone_bot(client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            "<b>» Please provide a bot token to clone.</b>\n\n"
            "<b>Example:</b> <code>/clone 123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ</code>"
        )

    bot_token = message.command[1].strip()
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
        cloned_bot = await start_clone(bot_token, user_id)
        if not cloned_bot:
            return await mystic.edit_text("<b>» Failed to clone bot! Please make sure the bot token is valid and not already running elsewhere.</b>")

        # save to database
        await clone_db.update_one(
            {"bot_token": bot_token},
            {
                "$set": {
                    "owner_id": user_id,
                    "username": cloned_bot.me.username,
                    "name": cloned_bot.me.first_name
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
            print(f"Error stopping clone: {e}")

    await message.reply_text("<b>» Successfully uncloned your bot! It is now stopped and deleted.</b>")


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
            return await message.reply_text("<b>» You haven't cloned any bot yet! Use <code>/clone Bot_Token</code> to clone.</b>")

        await message.reply_text(f"<b>» Your cloned bot:</b> @{clone.get('username', 'Unknown')}\n<b>Status:</b> Running ✅")
