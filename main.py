import asyncio
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", level=logging.INFO)
log = logging.getLogger("streamline-downloader")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MAX_URL_LENGTH = int(os.getenv("MAX_URL_LENGTH", "2000"))
DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT_SECONDS", "900"))
MAX_CONCURRENT = max(1, int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "2")))
ALLOWED_USER_IDS = {int(x.strip()) for x in os.getenv("ALLOWED_USER_IDS", "").split(",") if x.strip().isdigit()}
SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT)

FORMATS = {
    "mp3": {"label": "MP3 Audio", "selector": "bestaudio/best", "audio": True, "icon": "🎧"},
    "240": {"label": "240p", "selector": "bestvideo[height<=240]+bestaudio/best[height<=240]", "audio": False, "icon": "📱"},
    "360": {"label": "360p", "selector": "bestvideo[height<=360]+bestaudio/best[height<=360]", "audio": False, "icon": "📱"},
    "480": {"label": "480p", "selector": "bestvideo[height<=480]+bestaudio/best[height<=480]", "audio": False, "icon": "📺"},
    "720": {"label": "720p HD", "selector": "bestvideo[height<=720]+bestaudio/best[height<=720]", "audio": False, "icon": "📺"},
    "1080": {"label": "1080p Full HD", "selector": "bestvideo[height<=1080]+bestaudio/best[height<=1080]", "audio": False, "icon": "🎬"},
    "2k": {"label": "2K / 1440p", "selector": "bestvideo[height<=1440]+bestaudio/best[height<=1440]", "audio": False, "icon": "💎"},
    "4k": {"label": "4K / 2160p", "selector": "bestvideo[height<=2160]+bestaudio/best[height<=2160]", "audio": False, "icon": "💎"},
}

SUPPORTED_HOSTS = {
    "youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com",
    "tiktok.com", "www.tiktok.com", "vm.tiktok.com",
    "facebook.com", "www.facebook.com", "m.facebook.com", "fb.watch",
}
REQUESTS: dict[str, dict] = {}
MUSIC_RESULTS: dict[str, list[str]] = {}
USER_ACTIVE: dict[int, asyncio.Task] = {}


def permitted(update: Update) -> bool:
    return not ALLOWED_USER_IDS or bool(update.effective_user and update.effective_user.id in ALLOWED_USER_IDS)


def valid_url(value: str) -> bool:
    if not value or len(value) > MAX_URL_LENGTH:
        return False
    try:
        parsed = urlparse(value.strip())
        host = (parsed.hostname or "").lower()
        return parsed.scheme in {"http", "https"} and any(host == h or host.endswith("." + h) for h in SUPPORTED_HOSTS)
    except ValueError:
        return False


def human_size(value: float | int | None) -> str:
    if not value:
        return "—"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return "—"


def human_time(seconds: float | int | None) -> str:
    if seconds is None or seconds < 0:
        return "—"
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def progress_bar(percent: float, width: int = 16) -> str:
    filled = max(0, min(width, round(percent / 100 * width)))
    return "▰" * filled + "▱" * (width - filled)


def format_keyboard(token: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🎧 MP3", callback_data=f"fmt:{token}:mp3"), InlineKeyboardButton("📱 240p", callback_data=f"fmt:{token}:240")],
        [InlineKeyboardButton("📱 360p", callback_data=f"fmt:{token}:360"), InlineKeyboardButton("📺 480p", callback_data=f"fmt:{token}:480")],
        [InlineKeyboardButton("📺 720p HD", callback_data=f"fmt:{token}:720"), InlineKeyboardButton("🎬 1080p", callback_data=f"fmt:{token}:1080")],
        [InlineKeyboardButton("💎 2K", callback_data=f"fmt:{token}:2k"), InlineKeyboardButton("💎 4K", callback_data=f"fmt:{token}:4k")],
        [InlineKeyboardButton("✖ Cancel", callback_data=f"cancel:{token}")],
    ]
    return InlineKeyboardMarkup(rows)


def safe_filename(name: str, extension: str) -> str:
    cleaned = re.sub(r"[^\w\-. ()\[\]]+", "_", name, flags=re.UNICODE).strip(" .")
    return (cleaned[:120] or "download") + extension


def preview_media(url: str) -> dict:
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def download_media(url: str, key: str, workdir: str, state: dict) -> tuple[str, str]:
    chosen = FORMATS[key]
    output_template = str(Path(workdir) / "%(title).120s.%(ext)s")

    def hook(data: dict) -> None:
        status = data.get("status")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            downloaded = data.get("downloaded_bytes") or 0
            state.update({
                "phase": "downloading", "downloaded": downloaded, "total": total,
                "percent": downloaded / total * 100 if total else 0,
                "speed": data.get("speed") or 0, "eta": data.get("eta"),
            })
        elif status == "finished":
            state.update({"phase": "processing", "percent": 100, "downloaded": data.get("total_bytes") or 0})

    options = {
        "format": chosen["selector"], "outtmpl": output_template, "noplaylist": True,
        "restrictfilenames": True, "quiet": True, "no_warnings": True,
        "merge_output_format": "mp4", "socket_timeout": 30, "retries": 2,
        "max_filesize": 2 * 1024 * 1024 * 1024, "progress_hooks": [hook],
    }
    if chosen["audio"]:
        options["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get("title", "download")
    files = [p for p in Path(workdir).iterdir() if p.is_file()]
    if not files:
        raise RuntimeError("Download completed but no output file was found")
    return str(max(files, key=lambda p: p.stat().st_size)), title


async def edit_progress(bot, chat_id: int, message_id: int, state: dict, phase: str, force: bool = False) -> None:
    now = time.monotonic()
    if not force and now - state.get("last_edit", 0) < 1.5:
        return
    state["last_edit"] = now
    percent = state.get("percent", 0)
    speed = human_size(state.get("speed")) + "/s" if state.get("speed") else "—"
    text = (
        "<b>⚡ STREAMLINE DOWNLOADER</b>\n\n"
        f"<b>{phase}</b>\n"
        f"<code>{progress_bar(percent)} {percent:5.1f}%</code>\n\n"
        f"📦 <b>Data:</b> {human_size(state.get('downloaded'))} / {human_size(state.get('total'))}\n"
        f"🚀 <b>Speed:</b> {speed}\n"
        f"⏱ <b>ETA:</b> {human_time(state.get('eta'))}\n\n"
        "Please keep this chat open while processing…"
    )
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, parse_mode="HTML")
    except Exception as exc:
        if "message is not modified" not in str(exc).lower():
            log.debug("progress edit skipped: %s", exc)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not permitted(update):
        return
    await update.message.reply_text(
        "<b>⚡ STREAMLINE DOWNLOADER</b>\n\n"
        "YouTube • TikTok • Facebook\n"
        "MP3 • 240p • 360p • 480p • 720p • 1080p • 2K • 4K\n\n"
        "🔗 Link တစ်ခု ပို့ပြီး quality ရွေးပါ။\n"
        "🎵 Music ရှာရန် <code>/music artist - song</code>\n"
        "🛑 လက်ရှိအလုပ်ကို ရပ်ရန် <code>/cancel</code>\n\n"
        "<i>ကိုယ်ပိုင် သို့မဟုတ် ခွင့်ပြုထားသော content ကိုသာ အသုံးပြုပါ။</i>", parse_mode="HTML"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if permitted(update):
        await start(update, context)


async def receive_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not permitted(update):
        return
    text = (update.message.text or "").strip()
    if not valid_url(text):
        await update.message.reply_text("⚠️ YouTube, TikTok, သို့မဟုတ် Facebook public URL အပြည့်အစုံကို ပို့ပါ။")
        return
    if update.effective_user.id in USER_ACTIVE:
        await update.message.reply_text("⏳ Download တစ်ခု လုပ်ဆောင်နေပါတယ်။ ပြီးအောင်စောင့်ပါ သို့မဟုတ် /cancel ရိုက်ပါ။")
        return
    message = await update.message.reply_text("🔎 <b>Analyzing link…</b>", parse_mode="HTML")
    try:
        info = await asyncio.to_thread(preview_media, text)
        token = uuid.uuid4().hex[:10]
        REQUESTS[token] = {"url": text, "user_id": update.effective_user.id}
        title = (info.get("title") or "Unknown title")[:180]
        uploader = (info.get("uploader") or info.get("channel") or "Unknown")[:80]
        duration = human_time(info.get("duration"))
        preview = (
            "<b>✅ LINK READY</b>\n\n"
            f"🎞 <b>{title}</b>\n"
            f"👤 {uploader}\n"
            f"⏱ {duration}\n\n"
            "ရွေးချယ်မည့် format ကို နှိပ်ပါ:"
        )
        await message.edit_text(preview, parse_mode="HTML", reply_markup=format_keyboard(token))
    except Exception:
        log.exception("metadata preview failed")
        await message.edit_text("❌ ဒီ link ကို ဖတ်မရပါ။ Public/authorized link ဖြစ်ကြောင်း စစ်ပြီး ပြန်ပို့ပါ။")


async def format_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not permitted(update):
        return
    _, token, key = query.data.split(":")
    request = REQUESTS.get(token)
    if not request or request.get("user_id") != update.effective_user.id or key not in FORMATS:
        await query.edit_message_text("⚠️ ဒီ request သက်တမ်းကုန်သွားပါပြီ။ URL ပြန်ပို့ပါ။")
        return
    if update.effective_user.id in USER_ACTIVE:
        await query.answer("Download တစ်ခု လုပ်ဆောင်နေပါတယ်။", show_alert=True)
        return
    state = {"phase": "queued", "percent": 0, "downloaded": 0, "total": 0, "last_edit": 0, "cancelled": False}
    status = await query.edit_message_text(
        f"<b>🚀 STARTING • {FORMATS[key]['icon']} {FORMATS[key]['label']}</b>\n\n<code>{progress_bar(0)} 0.0%</code>", parse_mode="HTML"
    )
    workdir = tempfile.mkdtemp(prefix="tgdl-")
    task = asyncio.current_task()
    USER_ACTIVE[update.effective_user.id] = task
    try:
        async with SEMAPHORE:
            worker = asyncio.create_task(asyncio.to_thread(download_media, request["url"], key, workdir, state))
            while not worker.done():
                await edit_progress(context.bot, status.chat_id, status.message_id, state, "⬇️ DOWNLOADING…")
                await asyncio.sleep(1.0)
                if state.get("cancelled"):
                    worker.cancel()
                    raise asyncio.CancelledError
            file_path, title = await asyncio.wait_for(worker, timeout=DOWNLOAD_TIMEOUT)
        await edit_progress(context.bot, status.chat_id, status.message_id, state, "🧩 PROCESSING…", force=True)
        await context.bot.send_chat_action(status.chat_id, ChatAction.UPLOAD_DOCUMENT)
        extension = ".mp3" if key == "mp3" else ".mp4"
        upload_name = safe_filename(title, extension)
        with open(file_path, "rb") as media:
            if key == "mp3":
                await context.bot.send_audio(status.chat_id, audio=media, title=title[:200], filename=upload_name, caption="⚡ Streamline Downloader")
            else:
                await context.bot.send_video(status.chat_id, video=media, caption=f"⚡ {title[:850]}\n\nQuality: {FORMATS[key]['label']}", filename=upload_name, supports_streaming=True)
        await query.edit_message_text("<b>✅ COMPLETE</b>\n\nဖိုင်ကို အောင်မြင်စွာ ပို့ပြီးပါပြီ။ နောက်ထပ် link ပို့နိုင်ပါတယ်။", parse_mode="HTML")
    except asyncio.CancelledError:
        await context.bot.send_message(status.chat_id, "🛑 Download ကို ရပ်လိုက်ပါပြီ။")
    except asyncio.TimeoutError:
        await context.bot.send_message(status.chat_id, "⏰ Timeout ဖြစ်သွားပါတယ်။ Quality နိမ့်တာကို စမ်းပါ။")
    except Exception as exc:
        log.exception("download failed: %s", exc)
        await context.bot.send_message(status.chat_id, "❌ Download မအောင်မြင်ပါ။ Link သို့မဟုတ် quality ကို ပြန်စစ်ပြီး စမ်းပါ။")
    finally:
        USER_ACTIVE.pop(update.effective_user.id, None)
        REQUESTS.pop(token, None)
        shutil.rmtree(workdir, ignore_errors=True)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not permitted(update):
        return
    task = USER_ACTIVE.get(update.effective_user.id)
    if task and task is not asyncio.current_task():
        task.cancel()
        await update.message.reply_text("🛑 Cancel request လက်ခံပြီးပါပြီ။")
    else:
        await update.message.reply_text("လက်ရှိ download လုပ်ဆောင်နေခြင်း မရှိပါ။")


async def cancel_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Cancel လုပ်နေပါတယ်…")
    token = query.data.split(":", 1)[1]
    request = REQUESTS.get(token)
    if request and request.get("user_id") == update.effective_user.id:
        task = USER_ACTIVE.get(update.effective_user.id)
        if task:
            task.cancel()
        REQUESTS.pop(token, None)
        await query.edit_message_text("🛑 Request ကို cancel လုပ်လိုက်ပါပြီ။")
    else:
        await query.edit_message_text("ဒီ request သက်တမ်းကုန်သွားပါပြီ။")


async def music_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not permitted(update):
        return
    query_text = " ".join(context.args).strip()
    if not query_text:
        await update.message.reply_text("အသုံးပြုနည်း: <code>/music artist - song title</code>", parse_mode="HTML")
        return
    status = await update.message.reply_text("🎵 <b>Searching music…</b>", parse_mode="HTML")
    try:
        opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = await asyncio.to_thread(ydl.extract_info, f"ytsearch5:{query_text}", False)
        entries = data.get("entries", [])[:5]
        if not entries:
            await status.edit_text("ရှာမတွေ့ပါ။")
            return
        token = uuid.uuid4().hex[:10]
        MUSIC_RESULTS[token] = [e.get("webpage_url") or e.get("url") for e in entries if e.get("webpage_url") or e.get("url")]
        keyboard = [[InlineKeyboardButton(f"🎵 {(e.get('title') or 'Unknown')[:55]}", callback_data=f"music:{token}:{i}")] for i, e in enumerate(entries)]
        await status.edit_text("<b>🎵 MUSIC RESULTS</b>\n\nရွေးချယ်ပါ:", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception:
        log.exception("music search failed")
        await status.edit_text("❌ Music search မအောင်မြင်ပါ။ နောက်တစ်ကြိမ် ပြန်စမ်းပါ။")


async def music_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not permitted(update):
        return
    _, token, index = query.data.split(":")
    urls = MUSIC_RESULTS.get(token, [])
    try:
        url = urls[int(index)]
    except (ValueError, IndexError):
        await query.edit_message_text("Search result သက်တမ်းကုန်သွားပါပြီ။ /music နဲ့ ပြန်ရှာပါ။")
        return
    if not valid_url(url):
        await query.edit_message_text("ဒီ result ကို download မလုပ်နိုင်ပါ။")
        return
    request_token = uuid.uuid4().hex[:10]
    REQUESTS[request_token] = {"url": url, "user_id": update.effective_user.id}
    await query.edit_message_text("<b>🎵 AUDIO READY</b>\n\nMP3 ကို စတင်ရန် အောက်က button ကို နှိပ်ပါ။", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎧 Download MP3", callback_data=f"fmt:{request_token}:mp3")], [InlineKeyboardButton("✖ Cancel", callback_data=f"cancel:{request_token}")]]))


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is required")
    app = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("music", music_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CallbackQueryHandler(cancel_button, pattern=r"^cancel:[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(format_selected, pattern=r"^fmt:[a-f0-9]+:(mp3|240|360|480|720|1080|2k|4k)$"))
    app.add_handler(CallbackQueryHandler(music_selected, pattern=r"^music:[a-f0-9]+:\d+$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_url))
    log.info("Streamline Downloader started")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
