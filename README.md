# Telegram Video Downloader Bot

ဤ project သည် **ကိုယ်ပိုင် သို့မဟုတ် download ဖြန့်ဝေခွင့်ရှိသော content** များအတွက် Telegram bot တစ်ခုဖြစ်သည်။ URL ပို့ပြီး MP3, 240p, 360p, 480p, 720p, 1080p, 2K/1440p နှင့် 4K/2160p ကို ရွေးနိုင်သည်။ `/music artist - song title` ဖြင့် public music search results ကိုရှာပြီး MP3 အဖြစ် ရွေးချယ်နိုင်သည်။

> YouTube, TikTok, Facebook တို့၏ Terms of Service၊ copyright၊ creator permission နှင့် local law များကို လိုက်နာပါ။ Login wall, DRM, paywall, private content သို့မဟုတ် access-control ကို bypass မလုပ်ပါနှင့်။ Source platform က အဆိုပါ quality မပေးလျှင် bot သည် အနီးစပ်ဆုံးရရှိနိုင်သော quality ကိုသာ download လုပ်နိုင်သည်။

## Project files

| File | Purpose |
|---|---|
| `main.py` | Telegram handlers၊ URL validation၊ download၊ music search |
| `requirements.txt` | Python packages |
| `Dockerfile` | Railway အတွက် Python + FFmpeg container |
| `.env.example` | Environment variables sample |

## လိုအပ်ချက်များ

Python 3.10+၊ FFmpeg နှင့် Telegram bot token လိုအပ်သည်။ `yt-dlp` သည် site extractor များပြောင်းလဲသည့်အတွက် အချိန်နှင့်အမျှ package update လုပ်ရန် လိုနိုင်သည်။ FFmpeg သည် audio extraction နှင့် video/audio merge အတွက် လိုအပ်သည်။ [1] [2]

## Local test

အောက်ပါ command များဖြင့် project ကို run ပါ။

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .env.example .env
```

`.env` ထဲတွင် `BOT_TOKEN` ကို ထည့်ပါ။ Token ကို [@BotFather](https://t.me/BotFather) မှ `/newbot` ဖြင့် ဖန်တီးရသည်။ Bot token ကို GitHub၊ screenshot၊ public chat သို့ မတင်ပါနှင့်။ Telegram Bot API request များသည် HTTPS endpoint ကို အသုံးပြုသည်။ [3]

```bash
python main.py
```

ထို့နောက် bot chat ထဲတွင် `/start` ပို့ပြီး public/authorized URL တစ်ခု ပို့ပါ။ Quality button ကို နှိပ်ပြီး result ရယူပါ။ Music search အတွက် ဥပမာ:

```text
/music Alan Walker Faded
```

## Environment variables

| Variable | Required | Example | Meaning |
|---|---:|---|---|
| `BOT_TOKEN` | Yes | `123:ABC...` | BotFather token |
| `ALLOWED_USER_IDS` | No | `123456789,987654321` | သတ်မှတ်ထားသော user များသာ သုံးခွင့်ရစေရန် |
| `MAX_CONCURRENT_DOWNLOADS` | No | `2` | တစ်ပြိုင်နက် download အရေအတွက် |
| `DOWNLOAD_TIMEOUT_SECONDS` | No | `900` | Download timeout |
| `MAX_URL_LENGTH` | No | `2000` | URL input အရှည်ကန့်သတ်ချက် |

Production တွင် `ALLOWED_USER_IDS` သတ်မှတ်ထားခြင်းက public abuse နှင့် bandwidth ကုန်ကျမှုကို လျှော့ချနိုင်သည်။ Telegram user ID သိရန် `@userinfobot` ကဲ့သို့ third-party bot သုံးမည့်အစား ကိုယ်ပိုင် diagnostic handler ထည့်ခြင်းက ပိုလုံခြုံသည်။

## Railway deploy အဆင့်များ

Railway သည် source root တွင် `Dockerfile` အမည်အတိအကျတွေ့လျှင် container build အဖြစ် အသုံးပြုနိုင်သည်။ [4] Railway deployment က build လုပ်ပြီး container ကို start command ဖြင့် run သည်။ [5]

### 1. Git repository ပြင်ဆင်ပါ

```bash
git init
git add main.py requirements.txt Dockerfile .env.example README.md
git commit -m "Initial Telegram downloader bot"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/telegram-video-bot.git
git push -u origin main
```

`.env` ကို commit မလုပ်ပါနှင့်။ `.gitignore` ဖိုင်ဖန်တီးရန်:

```text
.env
.venv/
__pycache__/
*.pyc
```

### 2. Railway project ဖန်တီးပါ

[Railway](https://railway.com) သို့ login ဝင်ပြီး **New Project → Deploy from GitHub Repo** ကိုရွေးပါ။ အထက်ပါ repository ကိုရွေးပြီး deploy လုပ်ပါ။ Root directory တွင် `Dockerfile` ရှိသောကြောင့် Railway က အလိုအလျောက် detect လုပ်မည်။ [4]

### 3. Variables ထည့်ပါ

Service → **Variables** ထဲတွင် အောက်ပါအတိုင်းထည့်ပါ။

```text
BOT_TOKEN=သင်၏_BotFather_token
ALLOWED_USER_IDS=သင်၏_Telegram_user_id
MAX_CONCURRENT_DOWNLOADS=1
DOWNLOAD_TIMEOUT_SECONDS=900
MAX_URL_LENGTH=2000
```

`BOT_TOKEN` ကို log ထဲတွင် print မလုပ်ပါနှင့်။ Railway ၏ variable UI ကို အသုံးပြုပြီး secret အဖြစ်ထားပါ။

### 4. Logs စစ်ပါ

Deploy ပြီးနောက် logs တွင် `bot started` ပေါ်လာရမည်။ မပေါ်ပါက `BOT_TOKEN` variable၊ build logs နှင့် deployment logs ကို စစ်ပါ။ Railway documentation အရ deployment တစ်ခုသည် build၊ deploy နှင့် active/crashed state များဖြင့် ပြသသည်။ [5]

## Bot အသုံးပြုပုံ

| User action | Bot behavior |
|---|---|
| `/start` | အသုံးပြုနည်းပြသည် |
| `/help` | အတိုချုံး help ပြသည် |
| URL ပို့ခြင်း | Quality buttons ပြသည် |
| `MP3` | FFmpeg ဖြင့် MP3 ပြောင်းပေးသည် |
| `240p`–`4K` | ရွေးထားသော maximum height ဖြင့် MP4 download လုပ်သည် |
| `/music query` | Search result ငါးခုအထိ ပြပြီး ရွေးချယ်ခွင့်ပေးသည် |

## အရေးကြီးသော production notes

Railway service filesystem သည် ephemeral ဖြစ်နိုင်သောကြောင့် ဒီ code သည် download ပြီး Telegram သို့ upload လုပ်ပြီးနောက် temporary file ကို ချက်ချင်းဖျက်သည်။ Download အချိန်အတွင်း file များကို `/tmp` အောက်တွင်သာထားသည်။ Railway documentation တွင် service deployment storage နှင့် persistence အခြေအနေများကို သီးခြားဖော်ပြထားသည်။ [5]

4K file များသည် ကြီးမားနိုင်ပြီး download/merge/upload အချိန်နှင့် memory/disk သုံးစွဲမှု မြင့်နိုင်သည်။ အများသုံး bot အဖြစ်ဖွင့်မည်ဆိုလျှင် user allow-list၊ concurrent download limit၊ timeout နှင့် request quota ထည့်သွင်းပါ။ Telegram API သည် `sendVideo`၊ `sendAudio` နှင့် `sendDocument` ကဲ့သို့ file sending methods ပေးထားသော်လည်း လက်တွေ့ upload size နှင့် account/platform policy များပြောင်းလဲနိုင်သောကြောင့် 4K ကို အမြဲအောင်မြင်မည်ဟု မယူဆပါနှင့်။ [3]

`yt-dlp` extractor များသည် source site ပြောင်းလဲမှုကြောင့် အချို့ URL များတွင် ရပ်တန့်နိုင်သည်။ ပုံမှန်အားဖြင့် `requirements.txt` ထဲက `yt-dlp` ကို update/redeploy လုပ်ပါ။ Login လိုအပ်သော private post များ၊ DRM content များနှင့် geo/access restrictions ကို bypass လုပ်ရန် cookies သို့မဟုတ် scraping workaround များ မထည့်ထားပါ။ [2]

## Troubleshooting

**`BOT_TOKEN environment variable is required`** ပေါ်လျှင် Railway Variables ထဲတွင် variable name ကို `BOT_TOKEN` အတိအကျထားပြီး redeploy လုပ်ပါ။

**`Download မအောင်မြင်ပါ`** ပေါ်လျှင် URL သည် public ဖြစ်/မဖြစ်၊ source က quality ကို ပေး/မပေး၊ link သည် supported host ဖြစ်/မဖြစ် စစ်ပါ။ 4K အစား 720p သို့မဟုတ် 1080p ကို စမ်းပါ။

**FFmpeg error** ပေါ်လျှင် Railway build log တွင် `apt-get install ... ffmpeg` အဆင့်အောင်မြင်/မအောင်မြင် စစ်ပါ။ `Dockerfile` အမည်သည် capital `D` ဖြင့် root directory တွင် ရှိရမည်။ [4]

**Bot မတုံ့ပြန်လျှင်** deployment log တွင် `bot started` ရှိ/မရှိ စစ်ပါ။ တစ်ချိန်တည်းတွင် bot instance နှစ်ခု run မနေစေရန် Railway service တစ်ခုတည်းထားပါ။

## Architecture ရွေးချယ်မှု

| Approach | Tradeoffs | Cost | Setup complexity |
|---|---|---:|---:|
| Railway + Docker ဒီ project | FFmpeg/yt-dlp ပါပြီး 24/7 bot အဖြစ် run လွယ်သည်၊ bandwidth နှင့် compute usage ကန့်သတ်ချက်များရှိသည် | Railway plan အလိုက် | အလယ်အလတ် |
| ကိုယ်ပိုင် computer/VPS + Docker | OS control ပိုရ၊ persistent storage ပိုကောင်း၊ server maintenance ကိုယ်တိုင်လုပ်ရသည် | Provider အလိုက် | မြင့် |
| Lighter alternative: download links/official APIs သာသုံးသော bot | Hosting resource နည်း၊ 4K transcoding မလုပ်နိုင်၊ third-party downloader behavior မပါ | နည်း | နည်း |

ဤ project သည် user က Railway ကို တိတိကျကျရွေးထားသောကြောင့် ပထမနည်းလမ်းကို code အဖြစ်ပေးထားသည်။ Lighter alternative သည် copyright risk နှင့် server resource ကို လျှော့ချလိုသူများအတွက် ပိုသင့်တော်သည်။

## References

[1]: https://github.com/yt-dlp/yt-dlp "yt-dlp official repository and documentation"

[2]: https://github.com/yt-dlp/yt-dlp#dependencies "yt-dlp dependencies and FFmpeg documentation"

[3]: https://core.telegram.org/bots/api "Telegram Bot API"

[4]: https://docs.railway.com/builds/dockerfiles "Railway Dockerfiles documentation"

[5]: https://docs.railway.com/deployments/reference "Railway deployments reference"

## Modern UI upgrade

Version အသစ်တွင် bot ကို `STREAMLINE DOWNLOADER` branding ဖြင့် ပြင်ဆင်ထားပြီး download မစခင် title၊ uploader/channel နှင့် duration ကို preview ပြသည်။ Download လုပ်နေစဉ် status message သည် ခန့်မှန်းအားဖြင့် တစ်စက္ကန့်လျှင် update ဖြစ်ပြီး အောက်ပါ data bar ကို ပြသသည်။

```text
⬇️ DOWNLOADING…
▰▰▰▰▰▰▰▱▱▱▱▱▱▱▱▱ 42.5%

📦 Data: 18.4 MB / 43.3 MB
🚀 Speed: 2.1 MB/s
⏱ ETA: 00:00:12
```

ထပ်မံထည့်ထားသော feature များမှာ request token ဖြင့် user request မရောထွေးစေခြင်း၊ `/cancel` command နှင့် inline Cancel button၊ တစ် user တစ်ချိန်တည်း download တစ်ခုသာလုပ်ခွင့်၊ concurrent download limit၊ timeout၊ temporary file cleanup၊ metadata preview မအောင်မြင်လျှင် သေချာသော error message နှင့် MP3/video အတွက် သီးခြား upload presentation တို့ ဖြစ်သည်။

### Upgrade code deploy လုပ်ခြင်း

Local project folder ထဲတွင် code အသစ်ကို replace လုပ်ပြီး commit/push လုပ်ပါ။ Railway သည် GitHub source ပြောင်းလဲမှုကို detect လုပ်ပြီး service ကို ပြန် build/deploy လုပ်နိုင်သည်။ [5]

```bash
git add main.py README.md .gitignore Dockerfile requirements.txt
git commit -m "Upgrade bot UI and live download progress"
git push origin main
```

`MAX_CONCURRENT_DOWNLOADS=1` သို့မဟုတ် `2` ကို စတင်အသုံးပြုရန် အကြံပြုသည်။ 4K download များအတွက် CPU၊ disk နှင့် upload bandwidth ပိုသုံးနိုင်သောကြောင့် Railway logs တွင် memory/storage error ရှိ/မရှိ စောင့်ကြည့်ပါ။

## Premium button-first interface

Version အသစ်၏ entry screen သည် command များကို မှတ်သားစရာမလိုဘဲ button များဖြင့် စတင်အသုံးပြုနိုင်သော dashboard ဖြစ်သည်။ Home screen တွင် `Download Video`, `Music Search`, `Settings` နှင့် `Help` ပါဝင်ပြီး screen တိုင်းတွင် `Home` ပြန်သွားနိုင်သည်။

### User flow

```text
/start
   ↓
Premium Home Dashboard
   ├── Download Video → Send Link → Link Preview → Quality Grid → Live Data Bar → File
   ├── Music Search   → Type Query → Result Buttons → Download MP3
   ├── Settings       → Limits / Current Configuration
   └── Help           → Guided Instructions → Start Download
```

Download screen သည် title၊ uploader နှင့် duration ကို preview ပြပြီး quality ကို grid buttons ဖြင့် ရွေးစေသည်။ Processing အချိန်တွင် progress message သည် percentage၊ data size၊ speed နှင့် ETA ကို live ပြသသည်။ Download ရပ်လိုလျှင် `/cancel` သို့မဟုတ် inline Cancel button ကို အသုံးပြုနိုင်သည်။

## YouTube/Music မအလုပ်လုပ်သည့် ပြဿနာပြင်ဆင်ချက်

လက်ရှိ fix တွင် အရေးကြီးသော အချက်သုံးချက်ကို ထည့်ထားသည်။ ပထမအချက်မှာ `main.py` တွင် လိုအပ်နေသော `asyncio` import ကို ပြန်ထည့်ထားခြင်းဖြစ်သည်။ ဒုတိယအချက်မှာ YouTube ၏ JavaScript challenge များကို ဖြေရှင်းရန် Deno runtime ကို Docker image ထဲ ထည့်ထားခြင်းဖြစ်သည်။ yt-dlp ၏ official EJS guide အရ YouTube download အတွက် external JavaScript runtime လိုအပ်နိုင်ပြီး Deno ကို recommended runtime အဖြစ် ဖော်ပြထားသည်။ [6]

တတိယအချက်မှာ `requirements.txt` ကို `yt-dlp[default]` သို့ ပြောင်းထားခြင်းဖြစ်ပြီး companion EJS dependency များကို ထည့်သွင်းနိုင်စေသည်။ `Dockerfile` သည် FFmpeg၊ Deno နှင့် Python packages များကို build လုပ်ပေးမည်။

### Railway တွင် အရေးကြီးသော redeploy steps

GitHub repository သို့ update files များ push ပြီး Railway တွင် **Redeploy** လုပ်ပါ။ Build log ထဲတွင် `deno` download အဆင့်နှင့် `pip install yt-dlp[default]` အောင်မြင်ကြောင်း စစ်ပါ။ Service Variables တွင် `BOT_TOKEN` ရှိနေကြောင်း သေချာပါစေ။ Build cache ကြောင့် version အဟောင်းသုံးနေပါက Railway တွင် redeploy with cache clear option ရှိလျှင် အသုံးပြုပါ သို့မဟုတ် new deployment trigger လုပ်ပါ။

```bash
git add main.py requirements.txt Dockerfile README.md
git commit -m "Fix YouTube extractor and music search runtime"
git push origin main
```

Deploy ပြီးနောက် `/start` → `Download Video` → public YouTube URL → `360p` ကို ပထမဆုံးစမ်းပါ။ Music အတွက် `Music Search` button → `artist song title` ရိုက် → result button → `Download MP3` ကို စမ်းပါ။ 4K ကို အရင်မစမ်းဘဲ 360p/720p ဖြင့် service တက်ကြောင်း အတည်ပြုခြင်းက ပိုသင့်တော်သည်။

[6]: https://github.com/yt-dlp/yt-dlp/wiki/ejs "yt-dlp official External JavaScript Runtime and EJS setup guide"
