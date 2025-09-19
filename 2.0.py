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
from PIL import Image
from dotenv import load_dotenv

# ---------------- CONFIG ----------------
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
ADMIN_ID = int(os.getenv("ADMIN_ID"))

bot = TeleBot(TOKEN)

# ---------- Database (premium) ----------
conn = sqlite3.connect("users.db", check_same_thread=False)
c = conn.cursor()
c.execute("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, premium INTEGER)")
conn.commit()

def is_premium(user_id):
    c.execute("SELECT premium FROM users WHERE id=?", (user_id,))
    r = c.fetchone()
    return r and r[0] == 1

# ---------- In-memory session data ----------
user_data_lock = threading.Lock()
user_data = {}

# ---------- Utilities ----------
def safe_filename(name: str, max_len=120) -> str:
    if not name:
        return "file"
    name = name.replace("\n", " ").replace("\r", " ")
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    if len(name) > max_len:
        name = name[:max_len]
    return name

def trim_video_ffmpeg(input_path, output_path, start, end):
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-ss", str(start), "-to", str(end),
        "-c", "copy", output_path
    ]
    subprocess.run(cmd, check=True)

# ---------- /start ----------
@bot.message_handler(commands=["start"])
def cmd_start(m):
    bot.send_message(m.chat.id,
        "📥 *Video Downloader Bot*\n\n"
        "Commands:\n"
        "• /audio → Extract audio\n"
        "• /video → Download or Trim video\n"
        "• /upgrade → Become premium\n\n"
        "Free users limited to 480p.",
        parse_mode="Markdown"
    )

# ---------- (audio + premium flows remain same as before) ----------
# ------- keep your full audio flow and upgrade flow here -------

# ---------- process video ----------
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

        # thumbnail generate
        try:
            clip_for_thumb = VideoFileClip(final_path)
            duration = clip_for_thumb.duration
            tthumb = 1 if duration > 1 else 0
            frame = clip_for_thumb.get_frame(tthumb)
            thumb_img = Image.fromarray(frame)
            thumb_path = os.path.join(temp_dir, f"thumb_{safe_filename(title)}.jpg")
            thumb_img.save(thumb_path)
            clip_for_thumb.close()
        except Exception as e:
            print("Thumb create failed:", e)
            thumb_path = None

        size_mb = os.path.getsize(final_path) / (1024 * 1024)

        if size_mb <= 50:
            with open(final_path, "rb") as vf:
                if thumb_path:
                    with open(thumb_path, "rb") as th:
                        bot.send_video(chat_id, vf, caption=f"{title}\nDuration: {int(duration)}s", thumb=th, supports_streaming=True)
                else:
                    bot.send_video(chat_id, vf, caption=f"{title}\nDuration: {int(duration)}s", supports_streaming=True)
        else:
            # badi file → channel par bhej + link do
            with open(final_path, "rb") as vf:
                msg = bot.send_video(CHANNEL_ID, vf, caption=f"{title}\nDuration: {int(duration)}s", supports_streaming=True)

            bot.send_message(
                chat_id,
                f"⚠️ File too big ({int(size_mb)}MB).\n📺 Watch/Download here:\n"
                f"https://t.me/c/{str(CHANNEL_ID)[4:]}/{msg.message_id}"
            )

    except Exception as e:
        print("process_video error:", e)
        bot.send_message(chat_id, f"❌ Failed: {e}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        with user_data_lock:
            user_data.pop(chat_id, None)

# ---------- run ----------
print("Bot is running...")
bot.infinity_polling()
