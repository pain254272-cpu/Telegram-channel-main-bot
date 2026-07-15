import logging
import json
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# লগিং কনফিগারেশন
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ডাটাবেজ ফাইল (চ্যানেল তালিকা সংরক্ষণের জন্য JSON ফাইল)
DB_FILE = "channels_db.json"

# ডাটাবেজ থেকে চ্যানেল তালিকা লোড করার ফাংশন
def load_channels():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"ডাটাবেজ ফাইল লোড করতে সমস্যা: {e}")
            return {}
    return {}

# ডাটাবেজে চ্যানেল তালিকা সংরক্ষণ করার ফাংশন
def save_channels(channels):
    try:
        with open(DB_FILE, "w") as f:
            json.dump(channels, f, indent=4)
    except Exception as e:
        logger.error(f"ডাটাবেজ ফাইল সংরক্ষণ করতে সমস্যা: {e}")

# /start কমান্ড হ্যান্ডলার
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "হ্যালো! আমি প্রস্তুত।\n"
        "নতুন চ্যানেল যুক্ত করতে: `/addchannel <চ্যানেল_আইডি_অথবা_ইউজারনেম>`\n"
        "চ্যানেল বাদ দিতে: `/remchannel <চ্যানেল_আইডি_অথবা_ইউজারনেম>`\n"
        "সব চ্যানেলের তালিকা দেখতে: `/listchannels`",
        parse_mode="Markdown"
    )

# /addchannel কমান্ড হ্যান্ডলার (চ্যানেল যোগ করার জন্য)
async def add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("দয়া করে চ্যানেল আইডি বা ইউজারনেম দিন। উদাহরণ: `/addchannel -100123456789` বা `/addchannel @mychannel`")
        return

    channel_input = context.args[0].strip()
    channels = load_channels()

    if channel_input in channels:
        await update.message.reply_text("এই চ্যানেলটি আগেই যুক্ত করা হয়েছে!")
    else:
        # প্রাইভেট চ্যানেলের আইডি বা ইউজারনেম সেভ করা হচ্ছে
        channels[channel_input] = True  
        save_channels(channels)
        await update.message.reply_text(f"সাফল্যের সাথে চ্যানেলটি যুক্ত করা হয়েছে: `{channel_input}`", parse_mode="Markdown")

# /remchannel কমান্ড হ্যান্ডলার (প্রাইভেট বা পাবলিক চ্যানেল রিমুভ করার জন্য)
async def remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("দয়া করে যে চ্যানেলটি বাদ দিতে চান তার আইডি বা ইউজারনেম দিন। উদাহরণ: `/remchannel -100123456789`")
        return

    channel_input = context.args[0].strip()
    channels = load_channels()

    # ডাটাবেজে চ্যানেলটি আছে কিনা পরীক্ষা করে মুছে ফেলা
    if channel_input in channels:
        del channels[channel_input]
        save_channels(channels)
        await update.message.reply_text(f"সাফল্যের সাথে চ্যানেলটি বাদ দেওয়া হয়েছে: `{channel_input}`", parse_mode="Markdown")
    else:
        # যদি ইউজার আইডি বা ইউজারনেম হুবহু ম্যাচ না করে, তবে খোঁজার আরেকটি চেষ্টা করা
        found = False
        for key in list(channels.keys()):
            if channel_input in key or key in channel_input:
                del channels[key]
                save_channels(channels)
                found = True
                await update.message.reply_text(f"সাফল্যের সাথে চ্যানেলটি বাদ দেওয়া হয়েছে: `{key}`", parse_mode="Markdown")
                break
        
        if not found:
            await update.message.reply_text("দুঃখিত, এই চ্যানেলটি তালিকায় খুঁজে পাওয়া যায়নি। আইডি ঠিক আছে কিনা চেক করুন।")

# /listchannels কমান্ড হ্যান্ডলার (সব চ্যানেলের তালিকা দেখার জন্য)
async def list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channels = load_channels()
    if not channels:
        await update.message.reply_text("কোনো চ্যানেল যুক্ত করা নেই।")
        return

    channel_list = "\n".join([f"- `{ch}`" for ch in channels.keys()])
    await update.message.reply_text(f"যুক্ত থাকা চ্যানেলসমূহের তালিকা:\n{channel_list}", parse_mode="Markdown")

def main():
    # আপনার বটের সঠিক API টোকেনটি এখানে বসান
    BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"

    # অ্যাপ্লিকেশন তৈরি
    application = Application.builder().token(BOT_TOKEN).build()

    # কমান্ড হ্যান্ডলারসমূহ যুক্ত করা
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addchannel", add_channel))
    application.add_handler(CommandHandler("remchannel", remove_channel))
    application.add_handler(CommandHandler("listchannels", list_channels))

    # বট চালু করা
    print("বটটি সফলভাবে চালু হয়েছে...")
    application.run_polling()

if __name__ == "__main__":
    main()
