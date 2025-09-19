import os
import re
import time
import tempfile
import threading
import shutil
import sqlite3
import subprocess
from telebot import TeleBot, types
from yt_dlp import YoutubeDL
from moviepy.editor import VideoFileClip
from google.cloud import storage
from PIL import Image
from dotenv import load_dotenv

# ---------------- CONFIG ----------------
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
bot = TeleBot(TOKEN)

user_data = {}
user_data_lock = threading.Lock()

# ---------------- HELPERS ----------------
def safe_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name)

def trim_video_ffmpeg(input_path, output_path, start, end):
    cmd = [
        "ffmpeg", "-y", "-i", input_path, "-ss", str(start), "-to", str(end),
        "-c", "copy", output_path
    ]
    subprocess.run(cmd, check=True)

def generate_thumbnail(video_path, thumb_path, time_pos=1):
    cmd = [
        "ffmpeg", "-y", "-ss", str(time_pos), "-i", video_path,
        "-vframes", "1", "-q:v", "2", thumb_path
    ]
    subprocess.run(cmd, check=True)

# ---------------- MAIN VIDEO PROCESS ----------------
def process_video(chat_id, fmt, trim_times):
    with user_data_lock:
        ud = user_data.get(chat_id)
    if not ud:
        bot.send_message(chat_id, "❌ Session expired.")
        return

    url = ud["url"]
    title = ud["title"]
    temp_dir = tempfile.mkdtemp()
    try:
        outtmpl = os.path.join(temp_dir, f"{title}.%(ext)s")
        ydl_opts = {
            "format": fmt["format_id"],
            "outtmpl": outtmpl,
            "merge_output_format": "mp4",
            "quiet": True,
            "noplaylist": True,
            "socket_timeout": 1200,
            "ratelimit": 10_000_000,
            "noprogress": True
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)

        final_path = file_path
        if trim_times:
            start, end = trim_times
            trimmed = os.path.join(temp_dir, f"trimmed_{title}.mp4")
            trim_video_ffmpeg(file_path, trimmed, start, end)
            final_path = trimmed

        # Thumbnail generate
        thumb_path = os.path.join(temp_dir, f"thumb_{safe_filename(title)}.jpg")
        try:
            generate_thumbnail(final_path, thumb_path)
        except:
            thumb_path = None

        size_mb = os.path.getsize(final_path) / (1024 * 1024)

        if size_mb <= 50:
            # Chhoti file direct bhej
            with open(final_path, "rb") as vf:
                if thumb_path:
                    with open(thumb_path, "rb") as th:
                        bot.send_video(chat_id, vf, caption=f"{title}", thumb=th, supports_streaming=True)
                else:
                    bot.send_video(chat_id, vf, caption=f"{title}", supports_streaming=True)
        else:
            # Badi file → channel par bhej + link do
            with open(final_path, "rb") as vf:
                msg = bot.send_video(CHANNEL_ID, vf, caption=f"{title}", supports_streaming=True)

            bot.send_message(
                chat_id,
                f"⚠️ File too big ({int(size_mb)}MB).\n📺 Download/Watch here:\n"
                f"https://t.me/c/{str(CHANNEL_ID)[4:]}/{msg.message_id}"
            )

    except Exception as e:
        print("process_video error:", e)
        bot.send_message(chat_id, f"❌ Failed: {e}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        with user_data_lock:
            user_data.pop(chat_id, None)

# ---------------- START ----------------
@bot.message_handler(commands=["start"])
def start_handler(message):
    bot.send_message(message.chat.id, "👋 Welcome! Send me a YouTube link to begin.")

# ---------------- LINK HANDLER ----------------
@bot.message_handler(func=lambda m: bool(re.match(r'^https?://', m.text or "")))
def link_handler(message):
    url = message.text.strip()
    chat_id = message.chat.id
    bot.send_message(chat_id, "⏳ Fetching video info...")

    try:
        with YoutubeDL({"quiet": True, "noplaylist": True}) as ydl:
            info = ydl.extract_info(url, download=False)

        formats = [f for f in info["formats"] if f.get("ext") == "mp4" and f.get("filesize")]
        if not formats:
            bot.send_message(chat_id, "❌ No MP4 formats available.")
            return

        title = safe_filename(info.get("title", "video"))
        with user_data_lock:
            user_data[chat_id] = {"url": url, "title": title, "formats": formats}

        kb = types.InlineKeyboardMarkup()
        for i, f in enumerate(formats[:5]):
            size = f["filesize"] / (1024 * 1024)
            kb.add(types.InlineKeyboardButton(
                f"{f['format_note']} - {int(size)}MB", callback_data=f"f{i}"
            ))

        bot.send_message(chat_id, "Select format:", reply_markup=kb)

    except Exception as e:
        bot.send_message(chat_id, f"❌ Error: {e}")

# ---------------- CALLBACK HANDLER ----------------
@bot.callback_query_handler(func=lambda call: call.data.startswith("f"))
def format_selected(call):
    idx = int(call.data[1:])
    chat_id = call.message.chat.id

    with user_data_lock:
        ud = user_data.get(chat_id)
    if not ud:
        bot.send_message(chat_id, "❌ Session expired.")
        return

    fmt = ud["formats"][idx]
    bot.send_message(chat_id, "⏳ Processing your video...")

    t = threading.Thread(target=process_video, args=(chat_id, fmt, None))
    t.start()

# ---------------- RUN ----------------
print("🤖 Bot is running...")
bot.infinity_polling()
