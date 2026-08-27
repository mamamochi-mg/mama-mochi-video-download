import asyncio
import html
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

import storage
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.request import HTTPXRequest
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
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()
ADMIN_USER_IDS = {int(x.strip()) for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip().isdigit()}
MAX_URL_LENGTH = int(os.getenv("MAX_URL_LENGTH", "2000"))
DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT_SECONDS", "900"))
UPLOAD_TIMEOUT = int(os.getenv("UPLOAD_TIMEOUT_SECONDS", "1800"))
FAST_UPLOAD_MODE = os.getenv("FAST_UPLOAD_MODE", "1").lower() in {"1", "true", "yes"}
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


def profile(update: Update) -> tuple[int, str, str]:
    user = update.effective_user
    user_id = user.id
    username = f"@{user.username}" if user.username else "(no username)"
    display_name = " ".join(filter(None, [user.first_name, user.last_name])) or "(no name)"
    storage.upsert_user(user_id, username, display_name)
    return user_id, username, display_name


def consent_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅  Agree & Continue", callback_data="privacy:agree")], [InlineKeyboardButton("❌  Decline", callback_data="privacy:decline")]])


async def notify_admin(context: ContextTypes.DEFAULT_TYPE, update: Update, url: str, action: str = "link received", quality: str = "") -> None:
    user_id, username, display_name = profile(update)
    link_id = storage.log_link(user_id, username, display_name, url, action, quality)
    if not ADMIN_CHAT_ID:
        return
    admin_text = (
        "<b>🔔 NEW DOWNLOAD ACTIVITY</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>User:</b> {html.escape(display_name)}\n"
        f"🔖 <b>Username:</b> {html.escape(username)}\n"
        f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
        f"📌 <b>Action:</b> {html.escape(action)}\n"
        f"🔗 <b>Link:</b> {html.escape(url[:1800])}"
    )
    if quality:
        admin_text += f"\n🎚 <b>Quality:</b> {quality}"
    try:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_text, parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        log.exception("admin notification failed for link id %s", link_id)


async def privacy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id, _, _ = profile(update)
    if query.data == "privacy:agree":
        storage.set_consent(user_id, True)
        context.user_data["mode"] = "home"
        await show_home(query.message, edit=True)
    else:
        await query.edit_message_text("Privacy consent မပေးထားသဖြင့် bot ကို ဆက်သုံး၍မရပါ။ /start ဖြင့် ပြန်စနိုင်ပါတယ်။")


def valid_url(value: str) -> bool:
    if not value or len(value) > MAX_URL_LENGTH:
        return False
    try:
        parsed = urlparse(value.strip())
        host = (parsed.hostname or "").lower()
        return parsed.scheme in {"http", "https"} and any(host == h or host.endswith("." + h) for h in SUPPORTED_HOSTS)
    except ValueError:
        return False


def extract_supported_url(text: str) -> str | None:
    """Accept a raw URL or a Telegram share caption containing one URL."""
    candidate = text.strip()
    if valid_url(candidate):
        return candidate
    for found in re.findall(r"https?://[^\s<>]+", candidate):
        found = found.rstrip(".,!?)\\\"]'")
        if valid_url(found):
            return found
    return None


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
        [InlineKeyboardButton("⌂  Home", callback_data="ui:home")],
    ]
    return InlineKeyboardMarkup(rows)


def safe_filename(name: str, extension: str) -> str:
    cleaned = re.sub(r"[^\w\-. ()\[\]]+", "_", name, flags=re.UNICODE).strip(" .")
    return (cleaned[:120] or "download") + extension


def entry_url(entry: dict) -> str | None:
    url = entry.get("webpage_url") or entry.get("url")
    if url and str(url).startswith("http"):
        return str(url)
    video_id = entry.get("id")
    return f"https://www.youtube.com/watch?v={video_id}" if video_id else None


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


def home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬇️  Download Video", callback_data="ui:download")],
        [InlineKeyboardButton("🎵  Music Search", callback_data="ui:music")],
        [InlineKeyboardButton("🕘  History", callback_data="ui:history"), InlineKeyboardButton("⚙️  Settings", callback_data="ui:settings")],
        [InlineKeyboardButton("❓  Help", callback_data="ui:help")],
    ])


def back_home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⌂  Home", callback_data="ui:home")]])


async def show_home(message, edit: bool = False) -> None:
    text = (
        "<b>⚡ STREAMLINE</b>  <i>MEDIA DOWNLOADER</i>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<b>Fast. Clean. Simple.</b>\n\n"
        "Download videos and audio from\n"
        "YouTube  •  TikTok  •  Facebook\n\n"
        "<i>Choose an action below to get started.</i>"
    )
    if edit:
        await message.edit_text(text, parse_mode="HTML", reply_markup=home_keyboard())
    else:
        await message.reply_text(text, parse_mode="HTML", reply_markup=home_keyboard())


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not permitted(update):
        return
    user_id, _, _ = profile(update)
    if not storage.has_consent(user_id):
        await update.message.reply_text(
            "<b>⚡ STREAMLINE PRIVACY NOTICE</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            "Download request လုပ်သောအခါ သင်ပို့သော link၊ Telegram username/display name နှင့် user ID ကို admin monitoring chat သို့ ပို့ပြီး abuse prevention နှင့် service management အတွက် မှတ်တမ်းတင်ပါမည်။\n\n"
            "Password၊ cookies သို့မဟုတ် private account data များကို မသိမ်းပါ။ သဘောတူမှ bot ကို ဆက်သုံးနိုင်ပါမည်။",
            parse_mode="HTML", reply_markup=consent_keyboard()
        )
    else:
        context.user_data["mode"] = "home"
        await show_home(update.message)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if permitted(update):
        await show_help(update.message)


async def show_help(message) -> None:
    text = (
        "<b>❓ HOW IT WORKS</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<b>1.</b> Tap <b>Download Video</b>\n"
        "<b>2.</b> Send a public video link\n"
        "<b>3.</b> Choose MP3 or video quality\n"
        "<b>4.</b> Watch the live download progress\n\n"
        "<b>Supported:</b> YouTube, TikTok, Facebook\n"
        "<b>Quality:</b> MP3 to 4K where the source provides it\n\n"
        "<i>Only download content you own or have permission to use.</i>"
    )
    await message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("⬇️  Start Download", callback_data="ui:download")],
        [InlineKeyboardButton("⌂  Home", callback_data="ui:home")],
    ]))


async def ui_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not permitted(update):
        return
    action = query.data.split(":", 1)[1]
    if action == "home":
        context.user_data["mode"] = "home"
        await show_home(query.message, edit=True)
    elif action == "download":
        context.user_data["mode"] = "await_url"
        await query.edit_message_text(
            "<b>⬇️ DOWNLOAD VIDEO</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            "Send a public YouTube, TikTok or Facebook link here.\n\n"
            "<i>Tip: You can paste a link directly from your share menu.</i>",
            parse_mode="HTML", reply_markup=back_home_keyboard()
        )
    elif action == "music":
        context.user_data["mode"] = "music"
        await query.edit_message_text(
            "<b>🎵 MUSIC SEARCH</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            "Type an artist and song title.\n\n"
            "<code>Example: The Weeknd Blinding Lights</code>",
            parse_mode="HTML", reply_markup=back_home_keyboard()
        )
    elif action == "history":
        user_id, _, _ = profile(update)
        rows = storage.recent_links(user_id, 10)
        if not rows:
            text = "<b>🕘 HISTORY</b>\n━━━━━━━━━━━━━━━━━━\n\n📭 No activity yet."
        else:
            lines = ["<b>🕘 RECENT ACTIVITY</b>", "━━━━━━━━━━━━━━━━━━"]
            for row in rows:
                lines.append(f"• <b>{row['action']}</b> — {row['status']}\n  <code>{row['url'][:80]}</code>")
            text = "\n".join(lines)
        await query.edit_message_text(text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=back_home_keyboard())
    elif action == "settings":
        await query.edit_message_text(
            "<b>⚙️ SETTINGS</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            "Quality ကို download တစ်ခုချင်းစီတွင် ရွေးနိုင်ပါတယ်။\n"
            f"Concurrent jobs: <b>{MAX_CONCURRENT}</b>\n"
            f"Timeout: <b>{DOWNLOAD_TIMEOUT}s</b>\n\n"
            "<i>Admin က Railway Variables မှတစ်ဆင့် limits ပြောင်းနိုင်ပါတယ်။</i>",
            parse_mode="HTML", reply_markup=back_home_keyboard()
        )
    elif action == "help":
        await query.edit_message_text(
            "<b>❓ HELP CENTER</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            "<b>Download:</b> Link ပို့ပြီး quality button ရွေးပါ။\n"
            "<b>Music:</b> Music Search ထဲတွင် artist/title ရိုက်ပါ။\n"
            "<b>Progress:</b> Data, speed နှင့် ETA ကို live ပြပါမယ်။\n"
            "<b>Cancel:</b> /cancel သို့မဟုတ် Cancel button နှိပ်ပါ။\n\n"
            "<i>Public နှင့် ခွင့်ပြုထားသော content ကိုသာ အသုံးပြုပါ။</i>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬇️  Start Download", callback_data="ui:download")],
                [InlineKeyboardButton("⌂  Home", callback_data="ui:home")],
            ])
        )


async def receive_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not permitted(update):
        return
    user_id, _, _ = profile(update)
    if not storage.has_consent(user_id):
        await update.message.reply_text("ဆက်သုံးရန် /start ကိုနှိပ်ပြီး Privacy Notice ကို သဘောတူပါ။")
        return
    raw_text = (update.message.text or "").strip()
    text = extract_supported_url(raw_text)
    if context.user_data.get("mode") == "music" and not text:
        await search_music(update.message, raw_text)
        return
    if not text:
        await update.message.reply_text("⚠️ YouTube, TikTok, သို့မဟုတ် Facebook public URL အပြည့်အစုံကို ပို့ပါ။ Link ကို caption/text ထဲမှာပါ ထည့်ပို့နိုင်ပါတယ်။")
        return
    if update.effective_user.id in USER_ACTIVE:
        await update.message.reply_text("⏳ Download တစ်ခု လုပ်ဆောင်နေပါတယ်။ ပြီးအောင်စောင့်ပါ သို့မဟုတ် /cancel ရိုက်ပါ။")
        return
    # Do not block the user-facing flow on admin chat/network latency.
    asyncio.create_task(notify_admin(context, update, text, action="link received"))
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
        caption = f"⚡ {title[:850]}\n\nQuality: {FORMATS[key]['label']}"
        with open(file_path, "rb") as media:
            if key == "mp3":
                await context.bot.send_audio(status.chat_id, audio=media, title=title[:200], filename=upload_name, caption="⚡ Streamline Downloader", read_timeout=UPLOAD_TIMEOUT, write_timeout=UPLOAD_TIMEOUT, connect_timeout=60, pool_timeout=60)
            elif FAST_UPLOAD_MODE:
                # Document upload avoids Telegram video processing and is usually faster for large files.
                await context.bot.send_document(status.chat_id, document=media, filename=upload_name, caption=caption, read_timeout=UPLOAD_TIMEOUT, write_timeout=UPLOAD_TIMEOUT, connect_timeout=60, pool_timeout=60)
            else:
                try:
                    await context.bot.send_video(status.chat_id, video=media, caption=caption, filename=upload_name, supports_streaming=True, read_timeout=UPLOAD_TIMEOUT, write_timeout=UPLOAD_TIMEOUT, connect_timeout=60, pool_timeout=60)
                except Exception:
                    log.exception("send_video failed; retrying as document")
                    media.seek(0)
                    await context.bot.send_document(status.chat_id, document=media, filename=upload_name, caption=caption, read_timeout=UPLOAD_TIMEOUT, write_timeout=UPLOAD_TIMEOUT, connect_timeout=60, pool_timeout=60)
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


MUSIC_PAGE_SIZE = 6


def music_keyboard(token: str, page: int, total: int) -> InlineKeyboardMarkup:
    start = page * MUSIC_PAGE_SIZE
    end = min(start + MUSIC_PAGE_SIZE, total)
    rows = []
    for index in range(start, end):
        title = str(MUSIC_RESULTS[token]["results"][index].get("title") or "Unknown track")
        rows.append([InlineKeyboardButton(f"🎵 {index + 1:02d} • {title[:42]}", callback_data=f"music:{token}:{index}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀  Back", callback_data=f"musicpage:{token}:{page - 1}"))
    if end < total:
        nav.append(InlineKeyboardButton("Next  ▶", callback_data=f"musicpage:{token}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔍  New Search", callback_data="ui:music"), InlineKeyboardButton("⌂  Home", callback_data="ui:home")])
    return InlineKeyboardMarkup(rows)


def music_card(token: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
    data = MUSIC_RESULTS.get(token)
    if not data:
        return "<b>🎵 SEARCH EXPIRED</b>\n\nPlease start a new search.", InlineKeyboardMarkup([[InlineKeyboardButton("🔍  New Search", callback_data="ui:music"), InlineKeyboardButton("⌂  Home", callback_data="ui:home")]])
    results = data["results"]
    total = len(results)
    page = max(0, min(page, (total - 1) // MUSIC_PAGE_SIZE))
    first = page * MUSIC_PAGE_SIZE
    last = min(first + MUSIC_PAGE_SIZE, total)
    text = (
        "<b>🎵 MUSIC DISCOVERY</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<i>{data['query'][:70]}</i>\n\n"
        f"Showing <b>{first + 1}–{last}</b> of <b>{total}</b> tracks\n\n"
        "Tap <b>Select this track</b> to preview its details and download MP3."
    )
    return text, music_keyboard(token, page, total)


async def search_music(message, query_text: str) -> None:
    if not query_text:
        await message.reply_text("Artist နှင့် song title ကို ရိုက်ပါ။")
        return
    status = await message.reply_text("🎵 <b>SEARCHING MUSIC…</b>\n\nCurating your results…", parse_mode="HTML")
    try:
        opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = await asyncio.to_thread(ydl.extract_info, f"ytsearch20:{query_text}", False)
        entries = [e for e in data.get("entries", []) if e]
        results = []
        for entry in entries:
            url = entry_url(entry)
            if url:
                results.append({"url": url, "title": entry.get("title") or "Unknown title", "channel": entry.get("channel") or entry.get("uploader") or "Unknown artist", "duration": entry.get("duration")})
        if not results:
            await status.edit_text("❌ No tracks found. Try another artist or title.", reply_markup=back_home_keyboard())
            return
        token = uuid.uuid4().hex[:10]
        MUSIC_RESULTS[token] = {"query": query_text, "results": results}
        text, keyboard = music_card(token, 0)
        await status.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        log.exception("music search failed")
        await status.edit_text("❌ Music search မအောင်မြင်ပါ။ နောက်တစ်ကြိမ် ပြန်စမ်းပါ။", reply_markup=back_home_keyboard())


async def music_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not permitted(update):
        return
    query_text = " ".join(context.args).strip()
    if not query_text:
        await update.message.reply_text("အသုံးပြုနည်း: <code>/music artist - song title</code>", parse_mode="HTML")
        return
    await search_music(update.message, query_text)


async def music_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not permitted(update):
        return
    _, token, page_text = query.data.split(":")
    try:
        page = int(page_text)
    except ValueError:
        page = 0
    text, keyboard = music_card(token, page)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)


async def music_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not permitted(update):
        return
    _, token, index_text = query.data.split(":")
    data = MUSIC_RESULTS.get(token)
    try:
        index = int(index_text)
        track = data["results"][index]
    except (TypeError, KeyError, ValueError, IndexError):
        await query.edit_message_text("Search result သက်တမ်းကုန်သွားပါပြီ။", reply_markup=back_home_keyboard())
        return
    request_token = uuid.uuid4().hex[:10]
    REQUESTS[request_token] = {"url": track["url"], "user_id": update.effective_user.id}
    duration = human_time(track.get("duration"))
    text = (
        "<b>🎧 TRACK SELECTED</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>{track['title'][:180]}</b>\n"
        f"👤 {track['channel'][:80]}   •   ⏱ {duration}\n\n"
        "Ready to download as high-quality MP3."
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎧  Download MP3", callback_data=f"fmt:{request_token}:mp3")],
        [InlineKeyboardButton("◀  Back to Results", callback_data=f"musicpage:{token}:{index // MUSIC_PAGE_SIZE}"), InlineKeyboardButton("🔍  New Search", callback_data="ui:music")],
        [InlineKeyboardButton("⌂  Home", callback_data="ui:home")],
    ])
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)


def is_admin(update: Update) -> bool:
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    return user_id in ADMIN_USER_IDS or bool(ADMIN_CHAT_ID and chat_id == ADMIN_CHAT_ID)


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    data = storage.stats()
    await update.message.reply_text(
        "<b>🛡 ADMIN DASHBOARD</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Users: <b>{data['users']}</b>\n"
        f"🔗 Links logged: <b>{data['links']}</b>\n"
        f"✅ Completed: <b>{data['completed']}</b>", parse_mode="HTML"
    )


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not permitted(update):
        return
    user_id, _, _ = profile(update)
    if not storage.has_consent(user_id):
        await update.message.reply_text("ဆက်သုံးရန် /start ကိုနှိပ်ပြီး Privacy Notice ကို သဘောတူပါ။")
        return
    rows = storage.recent_links(user_id, 10)
    if not rows:
        await update.message.reply_text("📭 Download history မရှိသေးပါ။", reply_markup=back_home_keyboard())
        return
    lines = ["<b>🕘 RECENT ACTIVITY</b>", "━━━━━━━━━━━━━━━━━━"]
    for row in rows:
        lines.append(f"• <b>{row['action']}</b> — {row['status']}\n  <code>{row['url'][:100]}</code>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True, reply_markup=back_home_keyboard())


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is required")
    storage.init_db()
    upload_request = HTTPXRequest(connect_timeout=60, read_timeout=UPLOAD_TIMEOUT, write_timeout=UPLOAD_TIMEOUT, pool_timeout=60, connection_pool_size=max(8, MAX_CONCURRENT + 4))
    polling_request = HTTPXRequest(connect_timeout=60, read_timeout=45, write_timeout=60, pool_timeout=30, connection_pool_size=4)
    app = Application.builder().token(BOT_TOKEN).request(upload_request).get_updates_request(polling_request).concurrent_updates(True).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("music", music_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("admin", admin_stats))
    app.add_handler(CallbackQueryHandler(privacy_callback, pattern=r"^privacy:(agree|decline)$"))
    app.add_handler(CallbackQueryHandler(ui_navigation, pattern=r"^ui:(home|download|music|history|settings|help)$"))
    app.add_handler(CallbackQueryHandler(cancel_button, pattern=r"^cancel:[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(format_selected, pattern=r"^fmt:[a-f0-9]+:(mp3|240|360|480|720|1080|2k|4k)$"))
    app.add_handler(CallbackQueryHandler(music_page, pattern=r"^musicpage:[a-f0-9]+:\d+$"))
    app.add_handler(CallbackQueryHandler(music_selected, pattern=r"^music:[a-f0-9]+:\d+$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_url))
    log.info("Streamline Downloader started")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
