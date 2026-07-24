import os
import shutil
import time
import psutil
import asyncio
from pyrogram import filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

import config
from SONALI_MUSIC import app
from SONALI_MUSIC.misc import SUDOERS, _boot_
from SONALI_MUSIC.utils.database import (
    active,
    activevideo,
    is_maintenance,
    maintenance_on,
    maintenance_off,
)
from SONALI_MUSIC.plugins.sudo.clone import clone_db, cloned_bots
from config import BANNED_USERS


def get_sys_stats():
    uptime = time.time() - _boot_
    days, remainder = divmod(int(uptime), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{days}d {hours}h {minutes}m {seconds}s" if days > 0 else f"{hours}h {minutes}m {seconds}s"

    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    disk = psutil.disk_usage('/').percent
    return uptime_str, cpu, ram, disk


@app.on_message(filters.command(["adminpanel", "panel", "controlpanel"]) & SUDOERS)
async def adminpanel_cmd(client, message: Message):
    uptime_str, cpu, ram, disk = get_sys_stats()
    # In this bot, is_maintenance() is False if Maintenance mode is ON/Enabled.
    maint_status = "Enabled ❌" if await is_maintenance() is False else "Disabled ✅"

    total_clones = await clone_db.count_documents({})
    active_audio = len(active)
    active_video = len(activevideo)

    text = (
        f"<b>⚙️ <u>👑 BOT ELITE CONTROL PANEL 👑</u></b>\n\n"
        f"<b>📊 SYSTEM METRICS:</b>\n"
        f"• <b>Uptime:</b> <code>{uptime_str}</code>\n"
        f"• <b>CPU Usage:</b> <code>{cpu}%</code>\n"
        f"• <b>RAM Usage:</b> <code>{ram}%</code>\n"
        f"• <b>Disk Usage:</b> <code>{disk}%</code>\n\n"
        f"<b>🎵 STREAM METRICS:</b>\n"
        f"• <b>Active Audio Chats:</b> <code>{active_audio}</code>\n"
        f"• <b>Active Video Chats:</b> <code>{active_video}</code>\n\n"
        f"<b>🤖 CLONING METRICS:</b>\n"
        f"• <b>Total Cloned Bots:</b> <code>{total_clones}</code>\n"
        f"• <b>Active Running Clones:</b> <code>{len(cloned_bots)}</code>\n\n"
        f"<b>🛠️ CONFIGURATIONS:</b>\n"
        f"• <b>Maintenance Mode:</b> <code>{maint_status}</code>\n\n"
        f"<i>Select any control button below to manage the bot:</i>"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛠️ Toggle Maintenance", callback_data="ap_toggle_maint"),
            InlineKeyboardButton("🧹 Clear Cache / Temp", callback_data="ap_clear_cache")
        ],
        [
            InlineKeyboardButton("📊 System Stats", callback_data="ap_sys_stats"),
            InlineKeyboardButton("🎵 Active Streams", callback_data="ap_active_streams")
        ],
        [
            InlineKeyboardButton("📁 Get Bot Logs", callback_data="ap_get_logs"),
            InlineKeyboardButton("🤖 List Clones", callback_data="ap_list_clones")
        ],
        [
            InlineKeyboardButton("❌ Close Panel", callback_data="ap_close_panel")
        ]
    ])

    await message.reply_text(text, reply_markup=keyboard)


@app.on_callback_query(filters.regex("^ap_") & ~BANNED_USERS)
async def adminpanel_callbacks(client, callback_query: CallbackQuery):
    if callback_query.from_user.id not in SUDOERS:
        return await callback_query.answer("⚠️ This panel is only for Sudoers / Owner!", show_alert=True)

    data = callback_query.data

    if data == "ap_toggle_maint":
        # Toggle Maintenance:
        # If is_maintenance() is False, it is currently ENABLED. Toggling means calling maintenance_off().
        # If is_maintenance() is True, it is currently DISABLED. Toggling means calling maintenance_on().
        if await is_maintenance() is False:
            await maintenance_off()
            maint_status = "Disabled ✅"
        else:
            await maintenance_on()
            maint_status = "Enabled ❌"

        await callback_query.answer(f"✅ Maintenance mode is now {maint_status}!", show_alert=True)
        # Update UI
        uptime_str, cpu, ram, disk = get_sys_stats()
        total_clones = await clone_db.count_documents({})
        text = (
            f"<b>⚙️ <u>👑 BOT ELITE CONTROL PANEL 👑</u></b>\n\n"
            f"<b>📊 SYSTEM METRICS:</b>\n"
            f"• <b>Uptime:</b> <code>{uptime_str}</code>\n"
            f"• <b>CPU Usage:</b> <code>{cpu}%</code>\n"
            f"• <b>RAM Usage:</b> <code>{ram}%</code>\n"
            f"• <b>Disk Usage:</b> <code>{disk}%</code>\n\n"
            f"<b>🎵 STREAM METRICS:</b>\n"
            f"• <b>Active Audio Chats:</b> <code>{len(active)}</code>\n"
            f"• <b>Active Video Chats:</b> <code>{len(activevideo)}</code>\n\n"
            f"<b>🤖 CLONING METRICS:</b>\n"
            f"• <b>Total Cloned Bots:</b> <code>{total_clones}</code>\n"
            f"• <b>Active Running Clones:</b> <code>{len(cloned_bots)}</code>\n\n"
            f"<b>🛠️ CONFIGURATIONS:</b>\n"
            f"• <b>Maintenance Mode:</b> <code>{maint_status}</code>\n\n"
            f"<i>Select any control button below to manage the bot:</i>"
        )
        try:
            await callback_query.edit_message_text(text, reply_markup=callback_query.message.reply_markup)
        except Exception:
            pass

    elif data == "ap_clear_cache":
        # Clear files in downloads/ and cache/
        cleaned_dirs = ["downloads", "cache", "raw_files"]
        count_deleted = 0
        for d in cleaned_dirs:
            if os.path.exists(d):
                for f in os.listdir(d):
                    fp = os.path.join(d, f)
                    try:
                        if os.path.isfile(fp) or os.path.islink(fp):
                            os.unlink(fp)
                            count_deleted += 1
                        elif os.path.isdir(fp):
                            shutil.rmtree(fp)
                            count_deleted += 1
                    except Exception as e:
                        print(f"Error clearing {fp}: {e}")

        await callback_query.answer(f"🧹 Successfully cleared {count_deleted} cached items from {', '.join(cleaned_dirs)}!", show_alert=True)

    elif data == "ap_sys_stats":
        uptime_str, cpu, ram, disk = get_sys_stats()
        stats_text = (
            f"📈 LIVE SYSTEM STATISTICS:\n\n"
            f"• CPU Load: {cpu}%\n"
            f"• Virtual Memory: {ram}%\n"
            f"• Disk Storage: {disk}%\n"
            f"• Server Uptime: {uptime_str}"
        )
        await callback_query.answer(stats_text, show_alert=True)

    elif data == "ap_active_streams":
        active_audio = len(active)
        active_video = len(activevideo)
        streams_text = (
            f"🎵 ACTIVE MEDIA STREAMS:\n\n"
            f"• Audio Streaming: {active_audio} group chats\n"
            f"• Video Streaming: {active_video} group chats\n\n"
            f"Total active: {active_audio + active_video}"
        )
        await callback_query.answer(streams_text, show_alert=True)

    elif data == "ap_get_logs":
        await callback_query.answer("📤 Sending latest log file to your private chat...", show_alert=True)
        try:
            await client.send_document(
                chat_id=callback_query.from_user.id,
                document="log.txt",
                caption="📄 Latest System log file."
            )
        except Exception as e:
            try:
                await callback_query.message.reply_text(f"❌ Failed to send log.txt: {e}")
            except:
                pass

    elif data == "ap_list_clones":
        total_clones = await clone_db.count_documents({})
        clones_text = f"🤖 CLONING STATUS:\n\n• Total Saved Clones: {total_clones}\n• Currently Running: {len(cloned_bots)}"
        await callback_query.answer(clones_text, show_alert=True)

    elif data == "ap_close_panel":
        try:
            await callback_query.message.delete()
        except Exception:
            await callback_query.answer("❌ Message already deleted!")
