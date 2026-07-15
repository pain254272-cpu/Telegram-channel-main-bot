import subprocess
import sys
import threading
import asyncio

# --- Auto-Installer Part ---
required_libraries = {
    "telegram": "python-telegram-bot",
    "requests": "requests",
    "apscheduler": "APScheduler",
    "psutil": "psutil",
    "flask": "Flask"
}

for module_name, pip_name in required_libraries.items():
    try:
        __import__(module_name)
    except ImportError:
        print(f"📦 Library '{pip_name}' not found. Installing automatically...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name])
            print(f"✅ '{pip_name}' has been successfully installed.")
        except Exception as e:
            print(f"❌ Failed to install '{pip_name}': {e}")
            sys.exit(1)

# --- Main Bot Code Starts ---
import logging
import secrets
import requests
import json
import psutil
from io import BytesIO
from datetime import datetime
from flask import Flask, render_template_string, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatJoinRequestHandler,  # প্রাইভেট চ্যানেলের জয়েন রিকোয়েস্ট ট্র্যাকিংয়ের জন্য যুক্ত করা হয়েছে
    filters,
    ContextTypes,
)
from telegram.error import TelegramError, BadRequest

# Logging Setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot_errors.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# --- Configuration ---
BOT_TOKEN = "7926174608:AAH9pLyUHwLwCGsYj9xdXwEJYMwUxHTPfK0"
ADMIN_ID = 6499194100  # Your Telegram User ID
FIREBASE_URL = "https://telegram-5a96b-default-rtdb.firebaseio.com"

# --- SMART LOCAL CACHE SYSTEM (For Lightning Fast Response) ---
BOT_CACHE = {
    "ban_list": {},
    "maintenance": False,
    "channels": {},
    "last_sync": None,
    "pending_joins": {}  # পেন্ডিং জয়েন রিকোয়েস্ট ট্র্যাকিংয়ের জন্য ক্যাশ
}

def sync_firebase_cache():
    """মেমরিতে ফায়ারবেসের ডাটা ক্যাশ করার ফাংশন যা বটকে সুপার ফাস্ট করবে"""
    try:
        # মেইন সেটিংস এবং ডাটা একসাথে তুলে আনা হচ্ছে (Network Call কমানোর জন্য)
        url = f"{FIREBASE_URL}/.json"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            db_data = response.json() or {}
            BOT_CACHE["ban_list"] = db_data.get("ban_list", {}) or {}
            
            settings = db_data.get("settings", {}) or {}
            BOT_CACHE["maintenance"] = settings.get("maintenance", False)
            BOT_CACHE["channels"] = settings.get("channels", {}) or {}
            BOT_CACHE["last_sync"] = datetime.now()
            logging.info("⚡ Firebase Cache Successfully Synced Contextually!")
    except Exception as e:
        logging.error(f"Error syncing firebase cache: {e}")

async def auto_sync_cache_job(context: ContextTypes.DEFAULT_TYPE):
    # ব্যাকগ্রাউন্ড থ্রেডে ফায়ারবেস সিঙ্ক চালানো হচ্ছে যাতে বট থমকে না যায়
    threading.Thread(target=sync_firebase_cache, daemon=True).start()

# --- Firebase REST API Helper Functions ---
def fb_set(path, data):
    try:
        url = f"{FIREBASE_URL}/{path}.json"
        response = requests.put(url, json=data, timeout=5)
        # ডাটা সেট করার সাথে সাথে ক্যাশ সিঙ্ক করে নেওয়া ভালো
        threading.Thread(target=sync_firebase_cache, daemon=True).start()
        return response.json()
    except Exception as e:
        logging.error(f"Firebase Set Error: {e}")
        return None

def fb_get(path):
    # ডাটা যদি ক্যাশ পাথে থাকে তবে মেমরি থেকে সরাসরি রিটার্ন করবে (Super Fast!)
    if path == 'settings/maintenance':
        return BOT_CACHE["maintenance"]
    if path == 'settings/channels':
        return BOT_CACHE["channels"]
    if path.startswith('ban_list/'):
        uid = path.split('/')[-1]
        return BOT_CACHE["ban_list"].get(uid, None)
        
    try:
        url = f"{FIREBASE_URL}/{path}.json"
        response = requests.get(url, timeout=5)
        return response.json()
    except Exception as e:
        logging.error(f"Firebase Get Error: {e}")
        return None

def fb_delete(path):
    try:
        url = f"{FIREBASE_URL}/{path}.json"
        response = requests.delete(url, timeout=5)
        threading.Thread(target=sync_firebase_cache, daemon=True).start()
        return response.status_code == 200
    except Exception as e:
        logging.error(f"Firebase Delete Error: {e}")
        return False

# --- Chat Join Request Handler Function ---
async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ইউজার যখনই প্রাইভেট চ্যানেলে জয়েন রিকোয়েস্ট পাঠাবে, এই ফাংশনটি তার আইডি ক্যাশ করে রাখবে"""
    request = update.chat_join_request
    user_id = request.from_user.id
    chat_id = request.chat.id
    
    user_id_str = str(user_id)
    if user_id_str not in BOT_CACHE["pending_joins"]:
        BOT_CACHE["pending_joins"][user_id_str] = []
        
    if chat_id not in BOT_CACHE["pending_joins"][user_id_str]:
        BOT_CACHE["pending_joins"][user_id_str].append(chat_id)
        logging.info(f"➕ User {user_id} sent a Join Request to {chat_id}. Pending state cached.")

# --- Helper Function (Forcesub Check & Link Extractor) ---
async def check_forcesub(app: Application, user_id: int):
    target_channels = BOT_CACHE["channels"]
    if not target_channels:
        return True, []

    not_joined = []
    # যদি ফায়ারবেস ডাটাবেস ভুল ফরম্যাটে থাকে তবে এটিকে ডিকশনারিতে রূপান্তর করবে
    if not isinstance(target_channels, dict):
        return True, []

    for ch_id, ch_data in target_channels.items():
        if not ch_data:
            continue
        try:
            raw_id = str(ch_id).strip()
            if raw_id.startswith('-'):
                if not raw_id.startswith('-100') and len(raw_id) > 5 and raw_id[1:].isdigit():
                    final_id = int(raw_id.replace('-', '-100'))
                else:
                    final_id = int(raw_id) if raw_id.replace('-','').isdigit() else raw_id
            elif raw_id.isdigit():
                final_id = int(f"-100{raw_id}")
            else:
                final_id = raw_id

            # ১. প্রথমে চেক করবো ইউজার এই চ্যানেলে জয়েন রিকোয়েস্ট (Pending) পাঠিয়ে রেখেছে কি না
            user_id_str = str(user_id)
            user_pendings = BOT_CACHE["pending_joins"].get(user_id_str, [])
            if final_id in user_pendings or str(final_id) in [str(x) for x in user_pendings]:
                continue  # পেন্ডিং থাকলে সরাসরি পাস (ফাইল পাবে)

            # ২. পেন্ডিং না থাকলে সাধারণ মেম্বারশিপ চেক
            member = await app.bot.get_chat_member(chat_id=final_id, user_id=user_id)
            if member.status in ['left', 'kicked', 'member_left']:
                not_joined.append((ch_id, ch_data))
        except BadRequest as e:
            # যদি টেলিগ্রাম কোনো কারণে মেম্বার খুঁজে না পায় (প্রাইভেট চ্যানেলের ক্ষেত্রে হতে পারে)
            # তখনও ক্যাশ থেকে পেন্ডিং রিকোয়েস্ট পুনঃপরীক্ষা করা হবে
            user_id_str = str(user_id)
            user_pendings = BOT_CACHE["pending_joins"].get(user_id_str, [])
            if final_id in user_pendings or str(final_id) in [str(x) for x in user_pendings]:
                continue
                
            logging.info(f"User {user_id} not in chat {ch_id}: {e}")
            not_joined.append((ch_id, ch_data))
        except Exception as e:
            logging.error(f"Error checking channel {ch_id}: {e}")
            not_joined.append((ch_id, ch_data))

    return len(not_joined) == 0, not_joined


# --- Send File Function ---
async def send_file_to_user(bot, user_id, link_key):
    link_data = fb_get(f'links/{link_key}')
    if link_data:
        try:
            raw_chat_id = str(link_data['chat_id']).strip()
            if raw_chat_id.startswith('-'):
                if not raw_chat_id.startswith('-100') and len(raw_chat_id) > 5 and raw_chat_id[1:].isdigit():
                    c_id = int(raw_chat_id.replace('-', '-100'))
                else:
                    c_id = int(raw_chat_id) if raw_chat_id.replace('-','').isdigit() else raw_chat_id
            elif raw_chat_id.isdigit():
                c_id = int(f"-100{raw_chat_id}")
            else:
                c_id = raw_chat_id

            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=c_id,
                message_id=int(link_data['message_id']),
            )
            return True, "✅ File sent successfully!"
        except Exception as e:
            logging.error(f"File Send Error: {e}")
            return False, f"⚠ Failed to send the file. Make sure the bot is an admin in the source channel.\nError details: {e}"
    else:
        return False, "⚠ File link not found or has been deleted from the database."


# --- Command Handlers ---

# /start Command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    
    # মেমরি ক্যাশ থেকে চেক (মিলি-সেকেন্ডে রেসপন্স হবে)
    is_banned = BOT_CACHE["ban_list"].get(str(user.id), None)
    if is_banned:
        await update.message.reply_text("❌ Sorry, you have been banned from using this bot.")
        return

    is_mt = BOT_CACHE["maintenance"]
    if is_mt and user.id != ADMIN_ID:
        await update.message.reply_text("🚧 **The bot is currently under maintenance.**\nPlease try again later.")
        return

    # User DB Save & Admin Notification (Non-blocking async process)
    existing_user = fb_get(f'users/{user.id}')
    if not existing_user:
        fb_set(f'users/{user.id}', {'username': user.username, 'first_name': user.first_name, 'joined_at': str(datetime.now())})
        notification_text = (
            f"👤 **New User Started the Bot!**\n\n"
            f"📛 Name: {user.first_name}\n"
            f"🆔 ID: `{user.id}`\n"
            f"🔗 Username: @{user.username if user.username else 'None'}"
        )
        try:
            photos = await context.bot.get_user_profile_photos(user_id=user.id, limit=1)
            if photos.total_count > 0:
                await context.bot.send_photo(chat_id=ADMIN_ID, photo=photos.photos[0][0].file_id, caption=notification_text, parse_mode="Markdown")
            else:
                await context.bot.send_message(chat_id=ADMIN_ID, text=notification_text, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Notification Error: {e}")

    # ১. যদি স্টার্ট লিংকে আর্গুমেন্ট থাকে
    if args:
        link_key = args[0]
        
        # অ্যাডমিন হলে সরাসরি ফাইল পাবে
        if user.id == ADMIN_ID:
            success, msg = await send_file_to_user(context.bot, user.id, link_key)
            if not success:
                await update.message.reply_text(msg)
            return

        # ফোর্সব চ্যানেল বাটন চেকিং লজিক (নিখুঁত বাটন জেনারেশন)
        is_joined, channels = await check_forcesub(context.application, user.id)
        if not is_joined:
            keyboard = []
            for ch_id, ch_data in channels:
                btn_name = "Join Channel/Group 📢"
                ch_url = ""
                
                if isinstance(ch_data, dict):
                    btn_name = ch_data.get('name', 'Join Channel/Group 📢')
                    ch_url = ch_data.get('url', '')
                elif isinstance(ch_data, str):
                    ch_url = ch_data
                
                if ch_url:
                    keyboard.append([InlineKeyboardButton(text=btn_name, url=ch_url)])
            
            keyboard.append([InlineKeyboardButton(text="🔄 I have joined (Verify)", callback_data=f"verify_{link_key}")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            alert_text = "❌ **To get the file, you must join our channels/groups first!**\n\n👇 Click the buttons below to join, then click the verify button."
            await update.message.reply_text(alert_text, parse_mode="Markdown", reply_markup=reply_markup)
            return

        # মেম্বার জয়েন থাকলে ফাইল দিয়ে দিবে
        success, msg = await send_file_to_user(context.bot, user.id, link_key)
        if not success:
            await update.message.reply_text(msg)
        return

    # ২. অ্যাডমিন মেনু
    if user.id == ADMIN_ID:
        admin_menu = (
            "🛠 **Welcome Admin! Here are your commands:**\n\n"
            "📊 /stats - Show bot statistics.\n"
            "👥 /userlist - Display list of all users.\n"
            "✉ /send <user_id> <message> - Message a user.\n"
            "📢 /broadcast - Reply to broadcast a post.\n"
            "➕ /addchannel <id> <button_name> <link> - Add channel/group.\n"
            "➖ /remchannel <id> - Remove channel/group.\n"
            "📋 /channellist - View active channels/groups.\n"
            "🗑 /deletelink <code> - Delete a sharing link.\n"
            "🚫 /ban <id> - Ban a user.\n"
            "🟢 /unban <id> - Unban a user.\n"
            "🚧 /maintenance - Toggle maintenance mode.\n"
            "📥 /backup - Database backup.\n\n"
            "💡 **To generate links:** Send any channel post link directly to the bot."
        )
        await update.message.reply_text(admin_menu, parse_mode="Markdown")
        return

    # ৩. সাধারণ মেম্বার
    await update.message.reply_text("👋 Welcome! I am your File Sharing Bot. To get files, please access through a valid link.")


# --- Callback Query Handler (Instant Verification) ---
async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    
    if data.startswith("verify_"):
        link_key = data.split("_")[1]
        is_joined, channels = await check_forcesub(context.application, user_id)
        
        if is_joined:
            await query.answer(text="✅ Verification successful! Sending file...", show_alert=False)
            try:
                await query.message.delete()
            except Exception:
                pass
            
            success, msg = await send_file_to_user(context.bot, user_id, link_key)
            if not success:
                await context.bot.send_message(chat_id=user_id, text=msg)
        else:
            await query.answer(text="❌ You haven't joined all channels/groups yet! Please join and click Verify again.", show_alert=True)


# Message Handler
async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = update.effective_user
    text = (update.message.text or "").strip()

    if user_id != ADMIN_ID:
        if BOT_CACHE["ban_list"].get(str(user_id), None): return
        if BOT_CACHE["maintenance"]: return

    if user_id == ADMIN_ID and update.message.reply_to_message:
        reply_msg = update.message.reply_to_message
        search_in = reply_msg.text or reply_msg.caption or ""
        if "🆔 ID:" in search_in:
            try:
                target_user_id = int(search_in.split("🆔 ID:")[1].split("\n")[0].strip().replace('`',''))
                await context.bot.copy_message(
                    chat_id=target_user_id,
                    from_chat_id=update.message.chat_id,
                    message_id=update.message.message_id
                )
                await update.message.reply_text("✅ Your reply has been sent to the user.")
                return
            except Exception as e:
                await update.message.reply_text(f"❌ Could not send message: {e}")
                return

    if user_id == ADMIN_ID and ("t.me/" in text or "telegram.me/" in text or "telegram.dog/" in text):
        clean_text = text.replace("https://", "").replace("http://", "")
        parts = clean_text.split('/')
        try:
            msg_id = int(parts[-1])
            channel_ref = parts[-2]

            if channel_ref == "c" or channel_ref.isdigit():
                chat_id = f"-100{parts[-3]}"
            else:
                chat_id = f"@{channel_ref}"

            link_key = secrets.token_urlsafe(8)
            fb_set(f'links/{link_key}', {'chat_id': chat_id, 'message_id': msg_id})

            bot_user = await context.bot.get_me()
            share_link = f"https://t.me/{bot_user.username}?start={link_key}"

            await update.message.reply_text(
                f"✅ **Link generated successfully!**\n\n🔗 Sharing Link:\n`{share_link}`",
                parse_mode="Markdown",
            )
            return
        except Exception as e:
            logging.error(f"Link Gen Error: {e}")
            await update.message.reply_text("⚠ Failed to process the link. Please send a valid public or private channel message link.")
            return

    if user_id != ADMIN_ID:
        header_text = (
            f"📩 **New Message from User:**\n\n"
            f"👤 Name: {user.first_name}\n"
            f"🆔 ID: `{user.id}`\n"
            f"🗣 Username: @{user.username if user.username else 'None'}\n\n"
            f"👇 **(Reply directly to this message to answer)**"
        )
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=header_text, parse_mode="Markdown")
            await context.bot.copy_message(
                chat_id=ADMIN_ID,
                from_chat_id=update.message.chat_id,
                message_id=update.message.message_id
            )
        except Exception as e:
            logging.error(f"Forward Error: {e}")

        await update.message.reply_text(
            "🤖 **Your message has been sent to the Admin! Please wait for a reply.**"
        )


# Stats Command
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    all_users = fb_get('users') or {}
    all_links = fb_get('links') or {}
    all_channels = BOT_CACHE["channels"]
    ban_users = BOT_CACHE["ban_list"]
    
    stats_text = (
        f"📊 **Current Bot Statistics:**\n\n"
        f"👥 Total Users: `{len(all_users)}` users\n"
        f"🚫 Banned Users: `{len(ban_users)}` users\n"
        f"🔗 Total Generated Links: `{len(all_links)}` links\n"
        f"📢 Active Forcesub Channels/Groups: `{len(all_channels)}` chats"
    )
    await update.message.reply_text(stats_text, parse_mode="Markdown")


# User List Command
async def user_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    all_users = fb_get('users') or {}
    
    if not all_users:
        await update.message.reply_text("👥 No users found in the database.")
        return

    list_text = "👥 **All Bot Users List:**\n\n"
    count = 1
    for u_id, u_info in all_users.items():
        name = u_info.get('first_name', 'Unknown')
        username = f"@{u_info.get('username')}" if u_info.get('username') else "None"
        list_text += f"{count}. 📛 Name: {name} | 🆔 ID: `{u_id}` | 🗣 Username: {username}\n"
        count += 1
        
        if len(list_text) > 3500:
            await update.message.reply_text(list_text, parse_mode="Markdown")
            list_text = ""
            
    if list_text:
        await update.message.reply_text(list_text, parse_mode="Markdown")


# Channel & Group List Command
async def channel_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    all_channels = BOT_CACHE["channels"]
    
    if not all_channels:
        await update.message.reply_text("📢 **No forcesub channels or groups are currently added.**", parse_mode="Markdown")
        return

    list_text = "📋 **Active Forcesub Channels & Groups List:**\n\n"
    count = 1
    for ch_id, ch_data in all_channels.items():
        btn_name = "Join Channel/Group 📢"
        url = "No Link"
        if isinstance(ch_data, dict):
            btn_name = ch_data.get('name', 'Join Channel/Group 📢')
            url = ch_data.get('url', 'No Link')
        elif isinstance(ch_data, str):
            url = ch_data
            
        list_text += (
            f"🔹 **Target {count}**\n"
            f"📛 Name: {btn_name}\n"
            f"🆔 ID: `{ch_id}`\n"
            f"🔗 Link: {url}\n"
            f"❌ **To Remove:** `/remchannel {ch_id}`\n\n"
        )
        count += 1
        
    await update.message.reply_text(list_text, parse_mode="Markdown")


# Send Message Command
async def send_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        target_id = int(context.args[0])
        msg_text = " ".join(context.args[1:])
        
        if not msg_text:
            await update.message.reply_text("⚠ Message body cannot be empty.")
            return

        await context.bot.send_message(chat_id=target_id, text=msg_text)
        await update.message.reply_text(f"✅ Message successfully sent to user `{target_id}`.", parse_mode="Markdown")
    except (IndexError, ValueError):
        await update.message.reply_text("⚠ Usage format: `/send <user_id> <your_message>`", parse_mode="Markdown")
    except TelegramError as e:
        await update.message.reply_text(f"❌ Could not send message! Error: {e}")


# Broadcast Command
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not update.message.reply_to_message:
        await update.message.reply_text("📢 Reply to the message you want to broadcast with `/broadcast`.")
        return

    msg_to_send = update.message.reply_to_message
    all_users = fb_get('users')

    if not_users := not all_users:
        await update.message.reply_text("No users found.")
        return

    success, failed = 0, 0
    total = len(all_users)
    status_msg = await update.message.reply_text(f"📢 Starting broadcast... (Total Users: {total})")

    count = 0
    for u_id in all_users.keys():
        try:
            await context.bot.copy_message(chat_id=int(u_id), from_chat_id=update.message.chat_id, message_id=msg_to_send.message_id)
            success += 1
        except Exception:
            failed += 1
            
        count += 1
        if count % 25 == 0:
            try:
                await status_msg.edit_text(f"📢 Broadcasting...\n\n✅ Success: `{success}`\n❌ Failed: `{failed}`\n📊 Total: `{count}/{total}`")
            except Exception: pass

    await status_msg.edit_text(f"✅ **Broadcast Completed!**\n\n🚀 Success: `{success}` | ❌ Failed: `{failed}`")


# Add Target Command
async def add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        ch_id = context.args[0]
        ch_url = context.args[-1]
        btn_name = " ".join(context.args[1:-1]) or "Join Channel/Group 📢"
            
        raw_id = str(ch_id).strip()
        if raw_id.startswith('-') and not raw_id.startswith('-100') and raw_id[1:].isdigit():
            ch_id = raw_id.replace('-', '-100')
        
        channel_data = {"name": btn_name, "url": ch_url}
        fb_set(f'settings/channels/{ch_id}', channel_data)
        await update.message.reply_text(f"✅ Target ID `{ch_id}` added successfully.", parse_mode="Markdown")
    except IndexError:
        await update.message.reply_text("⚠ **Usage:** `/addchannel <id> <button_name> <link>`", parse_mode="Markdown")


# Remove Target Command
async def remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        ch_id = str(context.args[0]).strip()
        raw_id = ch_id
        if raw_id.startswith('-') and not raw_id.startswith('-100') and raw_id[1:].isdigit():
            raw_id = raw_id.replace('-', '-100')

        deleted = fb_delete(f'settings/channels/{ch_id}')
        if not deleted and raw_id != ch_id:
            deleted = fb_delete(f'settings/channels/{raw_id}')
            
        if deleted:
            await update.message.reply_text(f"✅ Target ID `{ch_id}` successfully removed.", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Target not found in database.")
    except IndexError:
        await update.message.reply_text("⚠ Usage format: `/remchannel <target_id>`")


# Delete Generated Link Command
async def delete_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        link_key = context.args[0]
        if fb_delete(f'links/{link_key}'):
            await update.message.reply_text("🗑 Link successfully deleted.")
        else:
            await update.message.reply_text("❌ No link found with this code.")
    except IndexError:
        await update.message.reply_text("⚠ Usage format: `/deletelink <link_code>`")


# Ban Command
async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        target_id = context.args[0]
        fb_set(f'ban_list/{target_id}', True)
        await update.message.reply_text(f"🚫 User `{target_id}` banned.", parse_mode="Markdown")
    except IndexError:
        await update.message.reply_text("⚠ Usage format: `/ban <user_id>`")


# Unban Command
async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        target_id = context.args[0]
        if fb_delete(f'ban_list/{target_id}'):
            await update.message.reply_text(f"🟢 User `{target_id}` unbanned.", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ User not found in ban list.")
    except IndexError:
        await update.message.reply_text("⚠ Usage format: `/unban <user_id>`")


# Maintenance Command
async def toggle_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    current_status = fb_get('settings/maintenance') or False
    new_status = not current_status
    fb_set('settings/maintenance', new_status)
    status_str = "🟢 Active (ON)" if new_status else "🔴 Inactive (OFF)"
    await update.message.reply_text(f"🚧 Maintenance Mode is currently: **{status_str}**", parse_mode="Markdown")


# Backup Command
async def backup_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("⏳ Generating backup file...")
    all_data = fb_get('')
    bio = BytesIO(json.dumps(all_data, indent=4).encode('utf-8'))
    bio.name = f"backup_{datetime.now().strftime('%Y-%m-%d')}.json"
    await context.bot.send_document(chat_id=ADMIN_ID, document=bio, caption="📥 Backup completed.")


# Daily Auto-Backup Job
async def auto_backup_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        all_data = fb_get('')
        bio = BytesIO(json.dumps(all_data, indent=4).encode('utf-8'))
        bio.name = f"auto_backup_{datetime.now().strftime('%Y-%m-%d')}.json"
        await context.bot.send_document(chat_id=ADMIN_ID, document=bio, caption="🤖 **Automatic Daily Backup!**")
    except Exception as e:
        logging.error(f"Auto Backup Job Error: {e}")


# ================= Flask Dashboard Section =================
flask_app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bot Server Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background-color: #121212; color: #e0e0e0; font-family: 'Segoe UI', Georgia, serif; }
        .card { background-color: #1e1e1e; border: none; border-radius: 12px; }
        .text-custom-info { color: #0dcaf0; }
        .progress { background-color: #333; height: 20px; }
    </style>
    <script>
        async function fetchStats() {
            try {
                let response = await fetch('/api/stats');
                let data = await response.json();
                document.getElementById('cpu-percent').innerText = data.cpu + '%';
                document.getElementById('ram-used').innerText = data.ram_used + ' GB / ' + data.ram_total + ' GB (' + data.ram_percent + '%)';
                document.getElementById('disk-used').innerText = data.disk_used + ' GB / ' + data.disk_total + ' GB (' + data.disk_percent + '%)';
                document.getElementById('total-users').innerText = data.total_users;
                document.getElementById('total-links').innerText = data.total_links;
            } catch (error) {}
        }
        setInterval(fetchStats, 2000);
        window.onload = fetchStats;
    </script>
</head>
<body>
    <div class="container py-5">
        <h1 class="text-center mb-4 text-custom-info">📊 Bot System Dashboard</h1>
        <div class="row g-4">
            <div class="col-md-4"><div class="card p-4"><h3>💻 CPU</h3><h2 id="cpu-percent" class="text-success">--%</h2></div></div>
            <div class="col-md-4"><div class="card p-4"><h3>🧠 RAM</h3><h5 id="ram-used" class="text-warning">-- GB</h5></div></div>
            <div class="col-md-4"><div class="card p-4"><h3>💾 Storage</h3><h5 id="disk-used" class="text-info">-- GB</h5></div></div>
        </div>
        <div class="row g-4 mt-4">
            <div class="col-md-6"><div class="card p-4 text-center"><h4>👥 Active Users</h4><h1 id="total-users">0</h1></div></div>
            <div class="col-md-6"><div class="card p-4 text-center"><h4>🔗 Total Links</h4><h1 id="total-links">0</h1></div></div>
        </div>
    </div>
</body>
</html>
"""

@flask_app.route('/')
def index(): return render_template_string(HTML_TEMPLATE)

@flask_app.route('/api/stats')
def api_stats():
    all_users = fb_get('users') or {}
    all_links = fb_get('links') or {}
    return jsonify({
        'cpu': psutil.cpu_percent(),
        'ram_total': round(psutil.virtual_memory().total / (1024**3), 2),
        'ram_used': round(psutil.virtual_memory().used / (1024**3), 2),
        'ram_percent': psutil.virtual_memory().percent,
        'disk_total': round(psutil.disk_usage('/').total / (1024**3), 2),
        'disk_used': round(psutil.disk_usage('/').used / (1024**3), 2),
        'disk_percent': psutil.disk_usage('/').percent,
        'total_users': len(all_users), 'total_links': len(all_links)
    })

def run_flask(): flask_app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

# --- Main Function ---
def main():
    # প্রথমবার চালুর সময় ফায়ারবেস থেকে ডাটা ক্যাশে লোড করে নেওয়া হচ্ছে
    sync_firebase_cache()
    
    web_thread = threading.Thread(target=run_flask, daemon=True)
    web_thread.start()
    
    app = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("userlist", user_list))
    app.add_handler(CommandHandler("send", send_to_user))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("addchannel", add_channel))
    app.add_handler(CommandHandler("remchannel", remove_channel))
    app.add_handler(CommandHandler("channellist", channel_list))
    app.add_handler(CommandHandler("deletelink", delete_link))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("unban", unban_user))
    app.add_handler(CommandHandler("maintenance", toggle_maintenance))
    app.add_handler(CommandHandler("backup", backup_db))
    
    # 🚨 প্রাইভেট চ্যানেলে পাঠানো রিকোয়েস্ট ক্যাশ করার নতুন হ্যান্ডলার
    app.add_handler(ChatJoinRequestHandler(handle_join_request))
    
    app.add_handler(CallbackQueryHandler(verify_callback))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_all_messages))

    job_queue = app.job_queue
    if job_queue:
        # প্রতি ৬০ সেকেন্ড পর পর ব্যাকগ্রাউন্ডে ফায়ারবেস ক্যাশ আপডেট করবে
        job_queue.run_repeating(auto_sync_cache_job, interval=60, first=5)
        job_queue.run_repeating(auto_backup_job, interval=86400, first=10)

    print("⚡ Bot started with Lightning Fast Local Cache and Fixed Forcesub Layout...")
    app.run_polling()

if __name__ == '__main__':
    main()
