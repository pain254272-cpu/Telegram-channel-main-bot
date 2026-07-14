import subprocess
import sys
import threading

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
    filters,
    ContextTypes,
)
from telegram.error import TelegramError

# Logging Setup (Error Logging)
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

# --- Firebase REST API Helper Functions ---
def fb_set(path, data):
    try:
        url = f"{FIREBASE_URL}/{path}.json"
        response = requests.put(url, json=data)
        return response.json()
    except Exception as e:
        logging.error(f"Firebase Set Error: {e}")
        return None

def fb_get(path):
    try:
        url = f"{FIREBASE_URL}/{path}.json"
        response = requests.get(url)
        return response.json()
    except Exception as e:
        logging.error(f"Firebase Get Error: {e}")
        return None

def fb_delete(path):
    try:
        url = f"{FIREBASE_URL}/{path}.json"
        response = requests.delete(url)
        return response.status_code == 200
    except Exception as e:
        logging.error(f"Firebase Delete Error: {e}")
        return False

# --- Helper Function (Forcesub Check) ---
async def check_forcesub(app: Application, user_id: int):
    target_channels = fb_get('settings/channels')
    if not target_channels:
        return True, []

    not_joined = []
    for ch_id, ch_data in target_channels.items():
        try:
            ch_url = ch_data if isinstance(ch_data, str) else ch_data.get('url', '')
            member = await app.bot.get_chat_member(
                chat_id=int(ch_id) if ch_id.replace('-','').isdigit() or ch_id.startswith('-') else ch_id, 
                user_id=user_id
            )
            if member.status in ['left', 'kicked']:
                not_joined.append((ch_id, ch_data))
        except Exception:
            not_joined.append((ch_id, ch_data))

    return len(not_joined) == 0, not_joined


# --- Command Handlers ---

# /start Command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    
    is_banned = fb_get(f'ban_list/{user.id}')
    if is_banned:
        await update.message.reply_text("❌ Sorry, you have been banned from using this bot.")
        return

    is_mt = fb_get('settings/maintenance') or False
    if is_mt and user.id != ADMIN_ID:
        await update.message.reply_text("🚧 **The bot is currently under maintenance.**\nPlease try again later.")
        return

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

    if user.id == ADMIN_ID and not args:
        admin_menu = (
            "🛠 **Welcome Admin! Here are your commands:**\n\n"
            "📊 /stats - Show bot statistics and user count.\n"
            "👥 /userlist - Display list of all bot users.\n"
            "✉ /send <user_id> <message> - Send message to a specific user.\n"
            "📢 /broadcast - Reply to any post with this command to broadcast it.\n"
            "➕ /addchannel <id> <button_name> <link> - Add a forcesub channel.\n"
            "➖ /remchannel <id> - Remove forcesub channel.\n"
            "🗑 /deletelink <code> - Delete a link from the database.\n"
            "🚫 /ban <id> - Ban a user from the bot.\n"
            "🟢 /unban <id> - Unban a banned user.\n"
            "🚧 /maintenance - Toggle maintenance mode on/off.\n"
            "📥 /backup - Download complete database backup.\n\n"
            "🌐 **Dashboard URL:** `http://localhost:5000`\n"
            "💡 **To generate links:** Send any channel post link directly to the bot."
        )
        await update.message.reply_text(admin_menu, parse_mode="Markdown")
        return

    if args:
        link_key = args[0]
        is_joined, channels = await check_forcesub(context.application, user.id)
        
        if not is_joined:
            keyboard = []
            for ch_id, ch_data in channels:
                if isinstance(ch_data, str):
                    btn_name = "Join Channel 📢"
                    ch_url = ch_data
                else:
                    btn_name = ch_data.get('name', 'Join Channel 📢')
                    ch_url = ch_data.get('url', '')
                
                keyboard.append([InlineKeyboardButton(text=btn_name, url=ch_url)])
            
            bot_user = await context.bot.get_me()
            retry_url = f"https://t.me/{bot_user.username}?start={link_key}"
            keyboard.append([InlineKeyboardButton(text="🔄 I have joined (Get File)", url=retry_url)])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            alert_text = "❌ **To get the file, you must join our channels first!**\n\n👇 Click the buttons below to join, then click the link again."
            await update.message.reply_text(alert_text, parse_mode="Markdown", reply_markup=reply_markup)
            return

        link_data = fb_get(f'links/{link_key}')
        if link_data:
            try:
                c_id = int(link_data['chat_id']) if str(link_data['chat_id']).replace('-','').isdigit() or str(link_data['chat_id']).startswith('-') else link_data['chat_id']
                await context.bot.copy_message(
                    chat_id=user.id,
                    from_chat_id=c_id,
                    message_id=link_data['message_id'],
                )
            except Exception:
                await update.message.reply_text("⚠ Failed to send the file. Make sure the bot is an admin in that channel.")
        else:
            await update.message.reply_text("⚠ File link not found or has been deleted from the database.")
    else:
        await update.message.reply_text("👋 Welcome! I am your File Sharing Bot. To get files, please access through a valid link.")


# Handle all incoming messages
async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = update.effective_user
    text = update.message.text or ""

    if user_id != ADMIN_ID:
        if fb_get(f'ban_list/{user_id}'): return
        if fb_get('settings/maintenance') or False: return

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

    if user_id == ADMIN_ID and "t.me/" in text:
        parts = text.split('/')
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
        except Exception:
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
    all_channels = fb_get('settings/channels') or {}
    ban_users = fb_get('ban_list') or {}
    
    stats_text = (
        f"📊 **Current Bot Statistics:**\n\n"
        f"👥 Total Users: `{len(all_users)}` users\n"
        f"🚫 Banned Users: `{len(ban_users)}` users\n"
        f"🔗 Total Generated Links: `{len(all_links)}` links\n"
        f"📢 Active Forcesub Channels: `{len(all_channels)}` channels"
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


# Send Message to User Command
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
        await update.message.reply_text("⚠ Usage format: `/send <user_id> <your_message>`\n\n**Example:** `/send 6499194100 Hello there!`", parse_mode="Markdown")
    except TelegramError as e:
        await update.message.reply_text(f"❌ Could not send message! The user might have blocked the bot.\nError: {e}")


# Broadcast Command
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return

    if not update.message.reply_to_message:
        await update.message.reply_text("📢 Reply to the message you want to broadcast with `/broadcast`.")
        return

    msg_to_send = update.message.reply_to_message
    all_users = fb_get('users')

    if not all_users:
        await update.message.reply_text("No users found.")
        return

    success = 0
    failed = 0
    total = len(all_users)
    
    status_msg = await update.message.reply_text(f"📢 Starting broadcast... (Total Users: {total})")

    count = 0
    for u_id in all_users.keys():
        try:
            await context.bot.copy_message(
                chat_id=int(u_id),
                from_chat_id=update.message.chat_id,
                message_id=msg_to_send.message_id,
            )
            success += 1
        except TelegramError:
            failed += 1
        except Exception:
            failed += 1
            
        count += 1
        if count % 20 == 0:
            try:
                await status_msg.edit_text(f"📢 Broadcasting in progress...\n\n✅ Success: `{success}`\n❌ Failed: `{failed}`\n📊 Total: `{count}/{total}`")
            except Exception:
                pass

    await status_msg.edit_text(f"✅ **Broadcast Completed!**\n\n🚀 Successfully sent to: `{success}` users.\n❌ Failed: `{failed}` users.")


# Add Forcesub Channel Command
async def add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        ch_id = context.args[0]
        btn_name = context.args[1].replace('_', ' ')
        ch_url = context.args[2]
        
        channel_data = {
            "name": btn_name,
            "url": ch_url
        }
        
        fb_set(f'settings/channels/{ch_id}', channel_data)
        await update.message.reply_text(f"✅ Channel `{ch_id}` successfully added with button name **'{btn_name}'**.", parse_mode="Markdown")
    except IndexError:
        await update.message.reply_text(
            "⚠ **Usage format:**\n`/addchannel <channel_id> <button_name> <link>`\n\n"
            "**Example:**\n`/addchannel -100123456789 Join_Our_Channel https://t.me/file81626`\n"
            "*(Tip: Use underscores `_` to include spaces in button names)*", 
            parse_mode="Markdown"
        )


# Remove Forcesub Channel Command
async def remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        ch_id = context.args[0]
        if fb_delete(f'settings/channels/{ch_id}'):
            await update.message.reply_text(f"✅ Channel `{ch_id}` successfully removed.", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Channel not found in database.")
    except IndexError:
        await update.message.reply_text("⚠ Usage format: `/remchannel <channel_id>`")


# Delete Generated Link Command
async def delete_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        link_key = context.args[0]
        if fb_delete(f'links/{link_key}'):
            await update.message.reply_text("🗑 Link successfully deleted from database.")
        else:
            await update.message.reply_text("❌ No link found with this code.")
    except IndexError:
        await update.message.reply_text("⚠ Usage format: `/deletelink <link_code>`")


# Ban User Command
async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        target_id = context.args[0]
        fb_set(f'ban_list/{target_id}', True)
        await update.message.reply_text(f"🚫 User `{target_id}` has been successfully banned.", parse_mode="Markdown")
    except IndexError:
        await update.message.reply_text("⚠ Usage format: `/ban <user_id>`")


# Unban User Command
async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        target_id = context.args[0]
        if fb_delete(f'ban_list/{target_id}'):
            await update.message.reply_text(f"🟢 User `{target_id}` has been successfully unbanned.", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ This user is not in the ban list.")
    except IndexError:
        await update.message.reply_text("⚠ Usage format: `/unban <user_id>`")


# Toggle Maintenance Mode Command
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
    await update.message.reply_text("⏳ Generating database backup file...")
    all_data = fb_get('')
    bio = BytesIO(json.dumps(all_data, indent=4).encode('utf-8'))
    bio.name = f"backup_{datetime.now().strftime('%Y-%m-%d')}.json"
    await context.bot.send_document(chat_id=ADMIN_ID, document=bio, caption="📥 Bot database backup completed.")


# Daily Auto-Backup Job
async def auto_backup_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        all_data = fb_get('')
        bio = BytesIO(json.dumps(all_data, indent=4).encode('utf-8'))
        bio.name = f"auto_backup_{datetime.now().strftime('%Y-%m-%d')}.json"
        await context.bot.send_document(chat_id=ADMIN_ID, document=bio, caption="🤖 **Automatic Daily Database Backup!**")
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
        body { background-color: #121212; color: #e0e0e0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        .card { background-color: #1e1e1e; border: none; border-radius: 12px; box-shadow: 0 4px 10px rgba(0,0,0,0.3); }
        .text-custom-info { color: #0dcaf0; }
        .progress { background-color: #333; height: 20px; border-radius: 10px; }
        .progress-bar { font-weight: bold; border-radius: 10px; }
    </style>
    <script>
        async function fetchStats() {
            try {
                let response = await fetch('/api/stats');
                let data = await response.json();
                
                document.getElementById('cpu-percent').innerText = data.cpu + '%';
                document.getElementById('cpu-bar').style.width = data.cpu + '%';
                document.getElementById('cpu-bar').className = `progress-bar ${data.cpu > 80 ? 'bg-danger' : data.cpu > 50 ? 'bg-warning' : 'bg-success'}`;
                
                document.getElementById('ram-used').innerText = data.ram_used + ' GB / ' + data.ram_total + ' GB (' + data.ram_percent + '%)';
                document.getElementById('ram-bar').style.width = data.ram_percent + '%';
                document.getElementById('ram-bar').className = `progress-bar ${data.ram_percent > 80 ? 'bg-danger' : 'bg-warning'}`;

                document.getElementById('disk-used').innerText = data.disk_used + ' GB / ' + data.disk_total + ' GB (' + data.disk_percent + '%)';
                document.getElementById('disk-bar').style.width = data.disk_percent + '%';
                document.getElementById('disk-bar').className = `progress-bar bg-info`;
                
                document.getElementById('total-users').innerText = data.total_users;
                document.getElementById('total-links').innerText = data.total_links;
            } catch (error) {
                console.error("Error updating statistics:", error);
            }
        }
        setInterval(fetchStats, 2000); // 2 সেকেন্ড পর পর তথ্য আপডেট হবে
        window.onload = fetchStats;
    </script>
</head>
<body>
    <div class="container py-5">
        <h1 class="text-center mb-4 text-custom-info">📊 Bot System Resources Dashboard</h1>
        <hr class="border-secondary mb-5">
        
        <div class="row g-4">
            <div class="col-md-4">
                <div class="card p-4">
                    <h3>💻 CPU Usage</h3>
                    <h2 id="cpu-percent" class="text-success my-3">--%</h2>
                    <div class="progress">
                        <div id="cpu-bar" class="progress-bar bg-success" role="progressbar" style="width: 0%"></div>
                    </div>
                </div>
            </div>

            <div class="col-md-4">
                <div class="card p-4">
                    <h3>🧠 RAM (Memory)</h3>
                    <h5 id="ram-used" class="text-warning my-3">-- GB / -- GB (--%)</h5>
                    <div class="progress">
                        <div id="ram-bar" class="progress-bar bg-warning" role="progressbar" style="width: 0%"></div>
                    </div>
                </div>
            </div>

            <div class="col-md-4">
                <div class="card p-4">
                    <h3>💾 Storage (Disk)</h3>
                    <h5 id="disk-used" class="text-info my-3">-- GB / -- GB (--%)</h5>
                    <div class="progress">
                        <div id="disk-bar" class="progress-bar bg-info" role="progressbar" style="width: 0%"></div>
                    </div>
                </div>
            </div>
        </div>

        <div class="row g-4 mt-4">
            <div class="col-md-6">
                <div class="card p-4 text-center">
                    <h4>👥 Bot Active Users</h4>
                    <h1 id="total-users" class="text-light">0</h1>
                </div>
            </div>
            <div class="col-md-6">
                <div class="card p-4 text-center">
                    <h4>🔗 Total Links Generated</h4>
                    <h1 id="total-links" class="text-light">0</h1>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""

@flask_app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@flask_app.route('/api/stats')
def api_stats():
    # Gather hardware usage data
    cpu = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    
    # Firebase stats for general dashboard context
    all_users = fb_get('users') or {}
    all_links = fb_get('links') or {}

    return jsonify({
        'cpu': cpu,
        'ram_total': round(ram.total / (1024**3), 2),
        'ram_used': round(ram.used / (1024**3), 2),
        'ram_percent': ram.percent,
        'disk_total': round(disk.total / (1024**3), 2),
        'disk_used': round(disk.used / (1024**3), 2),
        'disk_percent': disk.percent,
        'total_users': len(all_users),
        'total_links': len(all_links)
    })

def run_flask():
    # Runs the Flask server on port 5000 (Localhost only for security)
    flask_app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

# =========================================================

# --- Main Function ---
def main():
    # Start the Flask Dashboard in a background thread
    web_thread = threading.Thread(target=run_flask, daemon=True)
    web_thread.start()
    
    app = Application.builder().token(BOT_TOKEN).build()

    # Registering Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("userlist", user_list))
    app.add_handler(CommandHandler("send", send_to_user))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("addchannel", add_channel))
    app.add_handler(CommandHandler("remchannel", remove_channel))
    app.add_handler(CommandHandler("deletelink", delete_link))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("unban", unban_user))
    app.add_handler(CommandHandler("maintenance", toggle_maintenance))
    app.add_handler(CommandHandler("backup", backup_db))
    
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_all_messages))

    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(auto_backup_job, interval=86400, first=10)

    print("🚀 Bot successfully started with English button system...")
    print("🌐 Monitoring website running on http://localhost:5000")
    app.run_polling()

if __name__ == '__main__':
    main()
