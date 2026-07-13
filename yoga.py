#!/usr/bin/env python3
"""
Habit.Yoga Referral Bot v2
- Habit.yoga OTP workflow: 1 point cost, admin-set reward per refer
- Bot referral system: share bot link → friend joins force channel → you get points
- Admin panel: points, rewards, force channel, bot-refer points, broadcast
- 2000+ concurrent users support
- NEW: Indian/International number selection + new/old user detection
"""

import os, re, json, uuid, asyncio, logging, random
import aiofiles
from typing import Optional
import aiohttp
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, ChatMember,
)
from telegram.ext import (
    Application, CommandHandler, ConversationHandler,
    MessageHandler, CallbackQueryHandler, filters, ContextTypes,
)

# ==================== CONFIG ====================
BOT_TOKEN     = os.environ.get("BOT_TOKEN", "8304089414:AAH_zuOyKUxANR_dzmIl1QLfx_kF2bp8Pe0")
ADMIN_IDS     = [1446058092, 6894923643]
ADMIN_ID      = ADMIN_IDS[0]  # kept for compatibility
DATA_FILE     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_data.json")
_BOT_USERNAME = "Anendj2000_bot"  # filled at startup

REGISTER_URL = "https://auth-service.habuild.in/public/user/v1/register-user"
LOGIN_URL    = "https://auth-service.habuild.in/public/auth/v1/login"
VERIFY_URL   = "https://auth-service.habuild.in/public/auth/v1/verify-otp"

USER_AGENTS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.7 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

def _make_headers(extra_auth=False):
    ua = random.choice(USER_AGENTS)
    is_safari = "Safari" in ua and "Chrome" not in ua
    h = {
        "accept": "application/json",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "origin": "https://habit.yoga",
        "referer": "https://habit.yoga/",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "cross-site",
        "user-agent": ua,
    }
    # Safari doesn't send sec-ch-ua headers; Chrome does
    if not is_safari:
        h["sec-ch-ua"] = '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"'
        h["sec-ch-ua-mobile"] = "?0"
        h["sec-ch-ua-platform"] = '"Windows"'
    # No broken "Bearer" with no token — that flags bots
    return h

HEADERS     = _make_headers(extra_auth=False)
REG_HEADERS = _make_headers(extra_auth=False)  # same headers, no fake auth

# ==================== DATA STORE ====================
_data: dict = {
    "settings": {
        "bot_refer_points": 5,
        "force_channel": "",
        "signup_bonus": 0,
    },
    "users": {},
}
_lock = asyncio.Lock()

async def load_data():
    global _data
    if os.path.exists(DATA_FILE):
        try:
            async with aiofiles.open(DATA_FILE, "r") as f:
                _data = json.loads(await f.read())
            s = _data.setdefault("settings", {})
            s.setdefault("bot_refer_points", 5)
            s.setdefault("force_channel", "")
            s.setdefault("signup_bonus", 0)
            _data.setdefault("users", {})
            logging.info(f"✅ Data loaded: {len(_data['users'])} users from {DATA_FILE}")
        except Exception as e:
            logging.warning(f"Load failed: {e}")
    else:
        logging.warning(f"⚠️ Data file not found at {DATA_FILE} - starting fresh")

async def save_data():
    async with _lock:
        try:
            async with aiofiles.open(DATA_FILE, "w") as f:
                await f.write(json.dumps(_data, indent=2, ensure_ascii=False))
        except Exception as e:
            logging.error(f"Save failed: {e}")

def get_user(uid: int) -> dict:
    key = str(uid)
    if key not in _data["users"]:
        _data["users"][key] = {
            "name": "", "refer_code": "", "points": 0,
            "total_refers": 0, "bot_refers": 0,
            "referred_by": "", "ref_rewarded": False,
        }
    u = _data["users"][key]
    u.setdefault("name", "")
    u.setdefault("refer_code", "")
    u.setdefault("points", 0)
    u.setdefault("total_refers", 0)
    u.setdefault("bot_refers", 0)
    u.setdefault("referred_by", "")
    u.setdefault("ref_rewarded", False)
    return u

def get_signup_bonus() -> int:
    return _data["settings"].get("signup_bonus", 0)

def get_brp() -> int:
    return _data["settings"].get("bot_refer_points", 5)

def get_force_channel() -> str:
    return _data["settings"].get("force_channel", "")

def get_total_stats() -> dict:
    users = _data.get("users", {})
    total_users  = len(users)
    total_refers = sum(u.get("total_refers", 0) for u in users.values())
    total_bot_refs = sum(u.get("bot_refers", 0) for u in users.values())
    total_points = sum(u.get("points", 0) for u in users.values())
    return {
        "users": total_users,
        "refers": total_refers,
        "bot_refs": total_bot_refs,
        "points": total_points,
    }

async def add_points(uid: int, pts: int):
    async with _lock:
        u = get_user(uid)
        u["points"] = max(0, u["points"] + pts)
    asyncio.create_task(save_data())

async def do_otp_refer_complete(uid: int):
    async with _lock:
        u   = get_user(uid)
        u["points"]       = max(0, u["points"] - 1)
        u["total_refers"] += 1
    asyncio.create_task(save_data())

async def do_bot_refer_reward(referrer_id: int):
    async with _lock:
        u   = get_user(referrer_id)
        brp = get_brp()
        u["points"]     = max(0, u["points"] + brp)
        u["bot_refers"] += 1
    asyncio.create_task(save_data())

# ==================== HTTP ====================
_session: Optional[aiohttp.ClientSession] = None
_cookie_jar: Optional[aiohttp.CookieJar] = None

async def get_session():
    global _session
    if _session is None or _session.closed:
        conn = aiohttp.TCPConnector(
            limit=500, limit_per_host=100, ssl=False, ttl_dns_cache=300
        )
        _session = aiohttp.ClientSession(
            connector=conn,
            timeout=aiohttp.ClientTimeout(total=30, connect=10),
        )
    return _session

async def prefetch_cookies():
    """Visit habit.yoga first to get anti-bot cookies, like a real browser."""
    global _cookie_jar
    try:
        jar = aiohttp.CookieJar(unsafe=True)
        conn = aiohttp.TCPConnector(limit=5, ssl=False, force_close=True)
        async with aiohttp.ClientSession(
            connector=conn,
            cookie_jar=jar,
            timeout=aiohttp.ClientTimeout(total=15, connect=10),
        ) as s:
            ua = random.choice(USER_AGENTS)
            async with s.get("https://habit.yoga/", headers={
                "user-agent": ua,
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "accept-language": "en-US,en;q=0.9",
                "sec-fetch-dest": "document",
                "sec-fetch-mode": "navigate",
                "sec-fetch-site": "none",
                "sec-fetch-user": "?1",
                "upgrade-insecure-requests": "1",
            }) as r:
                await r.text()
        _cookie_jar = jar
        logging.info(f"\u2705 Prefetched cookies from habit.yoga")
    except Exception as e:
        logging.warning(f"Cookie prefetch failed: {e}")

def _get_cookie_header():
    """Build Cookie header from prefetched jar."""
    if _cookie_jar is None:
        return None
    try:
        cookies = []
        for cookie in _cookie_jar:
            cookies.append(f"{cookie.key}={cookie.value}")
        return "; ".join(cookies) if cookies else None
    except:
        return None

async def api_post(url, payload, headers, max_retries=5):
    for attempt in range(max_retries + 1):
        # Rotate user-agent on retries
        if attempt > 0:
            headers = {**headers, "user-agent": random.choice(USER_AGENTS)}

        # Add cookies from prefetch if available
        cookie_hdr = _get_cookie_header()
        if cookie_hdr:
            headers = {**headers, "cookie": cookie_hdr}

        s = await get_session()
        try:
            async with s.post(url, json=payload, headers=headers) as r:
                text = await r.text()
                logging.info(f"API {url} \u2192 {r.status}: {text[:200]} (attempt {attempt+1})")
                if r.status in (200, 201):
                    try: return json.loads(text), None
                    except: return None, "Invalid JSON"
                # 418 = rate limited -> retry with longer backoff
                if r.status == 418 and attempt < max_retries:
                    delay = 10 * (attempt + 1) + random.randint(1, 5)  # 11-15s, 21-25s, 31-35s, 41-45s, 51-55s
                    logging.warning(f"418 rate limited, retry {attempt+1}/{max_retries} after {delay}s")
                    await asyncio.sleep(delay)
                    if attempt == 2:
                        await prefetch_cookies()
                    continue
                return None, f"HTTP {r.status}: {text[:150]}"
        except asyncio.TimeoutError:
            if attempt < max_retries:
                await asyncio.sleep(5)
                continue
            return None, "Timeout"
        except Exception as e:
            if attempt < max_retries:
                await asyncio.sleep(5)
                continue
            return None, str(e)
    return None, "HTTP 418: Rate limited after retries - server IP may be temporarily blocked, try again later"

async def api_register(phone, code, name, did, sid):
    return await api_post(REGISTER_URL, {
        "name": name, "phoneNumber": phone, "referredBy": code,
        "sourceData": {"type": "Referral", "refererurl": "", "timezone": "Asia/Kolkata"},
        "experimentMetaInfo": {"deviceId": did, "sessionId": sid},
    }, _make_headers())

async def api_send_otp(phone, did, sid):
    resp, err = await api_post(LOGIN_URL, {
        "method": "phone_otp", "otpChannel": "sms", "phoneNumber": phone,
        "sourceData": {"type": "portal", "utm_source": "web_app"},
        "experimentMetaInfo": {"deviceId": did, "sessionId": sid},
        "registerUser": False,
    }, _make_headers(extra_auth=False))
    if err: return None, err
    if resp and resp.get("message") == "OTP sent to your phone":
        ref = resp.get("data", {}).get("refrence_code")
        if ref: return ref, None
    return None, (resp.get("message", "Unknown") if resp else "No response")

async def api_verify_otp(phone, ref, otp, did, sid):
    return await api_post(VERIFY_URL, {
        "phone": phone, "reference_code": ref, "otp": otp,
        "experimentMetaInfo": {"deviceId": did, "sessionId": sid},
        "registerUser": False,
    }, _make_headers(extra_auth=False))

# ==================== UTILS ====================
NAMES = [
    "Aarav","Vivaan","Aditya","Vihaan","Arjun","Sai","Shaurya","Atharva","Yash","Dhruv",
    "Kabir","Reyansh","Krishna","Laksh","Advik","Pranav","Rudra","Ishaan","Dev","Ansh",
    "Anaya","Aaradhya","Navya","Myra","Ananya","Diya","Sara","Ishita","Aadhya","Riya",
    "Raj","Simran","Priya","Rahul","Neha","Amit","Pooja","Vikram","Anjali","Rohan",
    "Sneha","Manish","Deepika","Kunal","Nidhi","Akash","Ritu","Mohit","Kajal","Tarun",
]

def rand_id():   return str(uuid.uuid4())
def rand_name(): return random.choice(NAMES)

def extract_code(link: str):
    link = link.strip().rstrip("/")
    code = link.replace("https://habit.yoga/", "") if "habit.yoga/" in link else link
    if code and all(c.isalnum() or c == "_" for c in code) and 1 <= len(code) <= 50:
        return code
    return None

def clear_temp(ctx):
    for k in ["phone", "otp_did", "otp_sid", "otp_ref", "num_type"]:
        ctx.user_data.pop(k, None)

FORCE_CHANNELS = ["@earnwithsakx", "@blankkdealz"]

async def is_channel_member(bot, uid: int) -> bool:
    for ch in FORCE_CHANNELS:
        try:
            m = await bot.get_chat_member(ch, uid)
            if m.status in (ChatMember.BANNED, ChatMember.LEFT):
                return False
        except Exception as e:
            logging.warning(f"Channel check error ({ch}): {e}")
            return False
    return True

def bot_refer_link(uid: int) -> str:
    return f"https://t.me/{_BOT_USERNAME}?start=ref_{uid}"

# ==================== KEYBOARDS ====================

BTN_WORKFLOW = "🚀 Start Workflow"
BTN_STATS    = "📊 Total Stats"
BTN_LINK     = "🔗 Refer Link"
BTN_CHANGE   = "🔄 Code Update"
BTN_HELP     = "💡 Help"
BTN_ADMIN    = "👑 Admin Panel"
ALL_BTNS     = [BTN_WORKFLOW, BTN_STATS, BTN_LINK, BTN_CHANGE, BTN_HELP, BTN_ADMIN]

def main_menu_kb():
    return ReplyKeyboardMarkup(
        [
            [BTN_WORKFLOW],
            [BTN_STATS,  BTN_LINK],
            [BTN_CHANGE, BTN_HELP],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )

def admin_menu_kb():
    return ReplyKeyboardMarkup(
        [
            [BTN_WORKFLOW],
            [BTN_STATS,  BTN_LINK],
            [BTN_CHANGE, BTN_HELP],
            [BTN_ADMIN],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )

def get_menu_kb(uid: int) -> ReplyKeyboardMarkup:
    return admin_menu_kb() if uid in ADMIN_IDS else main_menu_kb()

def kb_inline(*rows):
    return InlineKeyboardMarkup(rows)

def kb_cancel():
    return kb_inline([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])

def kb_otp_fail():
    return kb_inline([
        InlineKeyboardButton("🔄 Naya OTP Bhejo", callback_data="retry_otp"),
        InlineKeyboardButton("❌ Cancel",           callback_data="cancel"),
    ])

def kb_otp_input():
    return kb_inline([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])

def kb_after_refer():
    return kb_inline([
        InlineKeyboardButton("⚡️ Ek Aur Refer", callback_data="refer_more"),
        InlineKeyboardButton("🏠 Main Menu",     callback_data="main_menu"),
    ])

def kb_join_channel():
    rows = [[InlineKeyboardButton(f"📢 {ch} Join Karo", url=f"https://t.me/{ch.lstrip('@')}")] for ch in FORCE_CHANNELS]
    rows.append([InlineKeyboardButton("✅ Dono Join Ho Gaye", callback_data="check_joined")])
    return kb_inline(*rows)

def kb_admin_main():
    return kb_inline(
        [InlineKeyboardButton("👥 All Users",               callback_data="adm_users")],
        [InlineKeyboardButton("➕ Points Add",               callback_data="adm_add_pts"),
         InlineKeyboardButton("➖ Points Remove",            callback_data="adm_rem_pts")],
        [InlineKeyboardButton("🤖 Bot Refer Points",         callback_data="adm_bot_pts")],
        [InlineKeyboardButton("🎁 Signup Bonus",             callback_data="adm_signup_bonus")],
        [InlineKeyboardButton("📣 Broadcast",                callback_data="adm_broadcast")],
        [InlineKeyboardButton("❌ Close",                     callback_data="adm_close")],
    )

def kb_number_type():
    return kb_inline(
        [InlineKeyboardButton("🇮🇳 Indian (10 digits)", callback_data="num_type_indian")],
        [InlineKeyboardButton("🌍 International (with +)", callback_data="num_type_intl")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    )

# ==================== STATES ====================
(
    ASKING_LINK, ASKING_NUM_TYPE, ASKING_PHONE, ASKING_OTP,
    ADM_MAIN,
    ADM_ADD_UID, ADM_ADD_AMT,
    ADM_REM_UID, ADM_REM_AMT,
    ADM_BOT_PTS_AMT,
    ADM_SIGNUP_AMT,
    ADM_BROADCAST_MSG,
) = range(12)

# ==================== MAIN MENU ====================
async def send_main_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    name = update.effective_user.first_name or "Dost"
    u    = get_user(uid)
    pts      = u.get("points", 0)
    refs     = u.get("total_refers", 0)
    bot_refs = u.get("bot_refers", 0)
    code     = u.get("refer_code", "")

    text = (
        f"🏠 *Main Menu*\n"
        f"👤 *{name}*\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💰 *Points:*  `{pts}`\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"👇 *Option choose karo:*"
    )

    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_menu_kb(uid))
    else:
        await ctx.bot.send_message(uid, text, parse_mode="Markdown", reply_markup=get_menu_kb(uid))

# ==================== /start ====================
async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    name = update.effective_user.first_name or ""
    args = ctx.args or []

    referrer_id: Optional[int] = None

    async with _lock:
        u = get_user(uid)
        is_new = not u["name"]
        if is_new:
            u["name"] = name
            sb = get_signup_bonus()
            if sb > 0:
                u["points"] += sb

        if args and args[0].startswith("ref_"):
            ref_str = args[0][4:]
            if ref_str.isdigit():
                rid = int(ref_str)
                if rid != uid and not u.get("referred_by"):
                    u["referred_by"] = str(rid)
                    referrer_id = rid

    asyncio.create_task(save_data())
    clear_temp(ctx)

    if referrer_id:
        await handle_new_bot_refer(update, ctx, uid, referrer_id)
        return ConversationHandler.END

    await send_main_menu(update, ctx)
    return ConversationHandler.END

async def handle_new_bot_refer(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE,
    new_uid: int, referrer_id: int
):
    name = update.effective_user.first_name or "Dost"
    brp  = get_brp()

    is_member = await is_channel_member(ctx.bot, new_uid)
    if is_member:
        await _reward_bot_referrer(ctx, new_uid, referrer_id, name, brp)
        await send_main_menu(update, ctx)
    else:
        ctx.user_data["pending_referrer"] = str(referrer_id)
        ch_list = "\n".join(f"   • {ch}" for ch in FORCE_CHANNELS)
        await update.message.reply_text(
            f"👋 *Welcome {name}!*\n\n"
            f"🎉 Ek dost ne tumhe refer kiya!\n"
            f"🎁 Unhe milega: *+{brp} points*\n\n"
            f"⚠️ *Reward ke liye dono channels join karo:*\n{ch_list}\n\n"
            f"👇 Niche buttons se join karo, phir ✅ dabao!",
            parse_mode="Markdown",
            reply_markup=kb_join_channel(),
        )

async def _reward_bot_referrer(
    ctx: ContextTypes.DEFAULT_TYPE,
    new_uid: int, referrer_id: int,
    new_name: str, brp: int
):
    async with _lock:
        u = get_user(new_uid)
        if u.get("ref_rewarded"):
            return
        u["ref_rewarded"] = True
    asyncio.create_task(save_data())

    await do_bot_refer_reward(referrer_id)
    ref_u = get_user(referrer_id)
    try:
        await ctx.bot.send_message(
            referrer_id,
            f"🎉 *Bot Refer Complete!*\n\n"
            f"✅ *{new_name}* channel join kar liya!\n"
            f"🎁 *+{brp} points* aapke account mein add ho gaye!\n\n"
            f"💰 *Aapke Total Points:* `{ref_u['points']}`\n"
            f"🤝 *Total Bot Refers:* `{ref_u['bot_refers']}`",
            parse_mode="Markdown",
        )
    except Exception as e:
        logging.warning(f"Notify referrer failed: {e}")

# ==================== MENU BUTTONS ====================

async def btn_workflow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    u    = get_user(uid)
    pts  = u.get("points", 0)
    code = u.get("refer_code", "")

    if not code:
        await update.message.reply_text(
            "⚠️ *Pehle apna Habit.Yoga code set karo!*\n\n"
            "Apna referral link ya code bhejo:\n"
            "`https://habit.yoga/yourcode`",
            parse_mode="Markdown",
            reply_markup=kb_cancel(),
        )
        return ASKING_LINK

    if pts < 1:
        await update.message.reply_text(
            f"❌ *Points Khatam!*\n\n"
            f"💰 Aapke Points: *{pts}*\n\n"
            f"💡 *Points kaise kamao:*\n"
            f"→ 🔗 Refer Link se dost bulao → *+{get_brp()} pts* milenge\n"
            f"→ 1 point = 1 workflow chalao\n"
            f"→ Jitne refers utne points!",
            parse_mode="Markdown",
            reply_markup=get_menu_kb(uid),
        )
        return ConversationHandler.END

    # Store the refer code in context for later use
    ctx.user_data["refer_code"] = code

    # Ask for number type first
    await update.message.reply_text(
        "📞 *Pehle number type select karo:*",
        parse_mode="Markdown",
        reply_markup=kb_number_type(),
    )
    return ASKING_NUM_TYPE

async def btn_total_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    u    = get_user(uid)
    st   = get_total_stats()
    brp  = get_brp()

    await update.message.reply_text(
        f"📊 *Total Bot Stats*\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"👥 *Total Users:*  `{st['users']}`\n"
        f"✅ *OTP Refers Done:*  `{st['refers']}`\n"
        f"💰 *Total Points Distributed:* `{st['points']}`\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"👤 *Aapki Personal Stats:*\n"
        f"💰 Points: *{u['points']}*\n"
        f"🎯 OTP Refers: *{u['total_refers']}*\n"
        f"🤝 Bot Refers: *{u['bot_refers']}*",
        parse_mode="Markdown",
        reply_markup=get_menu_kb(uid),
    )
    return ConversationHandler.END

async def btn_refer_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    u    = get_user(uid)
    brp  = get_brp()
    link = bot_refer_link(uid)
    ch_lines = "\n".join(f"📢 `{ch}`" for ch in FORCE_CHANNELS)
    await update.message.reply_text(
        f"🔗 *Aapka Bot Referral Link*\n\n"
        f"`{link}`\n\n"
        f"📤 *Ye link apne doston ko bhejo!*\n"
        f"✅ Dost join kare + dono channels join kare → *+{brp} points* milenge!\n\n"
        f"{ch_lines}\n\n"
        f"🤝 *Aapke Total Bot Refers:* `{u.get('bot_refers', 0)}`",
        parse_mode="Markdown",
        reply_markup=get_menu_kb(uid),
    )
    return ConversationHandler.END

async def btn_code_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔄 *Naya Habit.Yoga Referral Link/Code Bhejo:*\n\n"
        "`https://habit.yoga/yourcode`\n"
        "_Ya sirf code: `yourcode`_",
        parse_mode="Markdown",
        reply_markup=kb_cancel(),
    )
    return ASKING_LINK

async def btn_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    brp = get_brp()
    await update.message.reply_text(
        f"💡 *Help Guide*\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🚀 *Start Workflow*\n"
        f"   → Habit.Yoga ka OTP refer karo\n"
        f"   → 1 refer = 1 point use\n\n"
        f"🔗 *Refer Link*\n"
        f"   → Apna bot invite link share karo\n"
        f"   → Dost join kare + dono channels join kare → *+{brp} pts* milenge!\n\n"
        f"📊 *Total Stats*  → Bot ki puri stats dekho\n"
        f"🔄 *Code Update*  → Naya Habit.Yoga code set karo\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📌 *Workflow Steps:*\n"
        f"1️⃣ 🚀 Start Workflow dabao\n"
        f"2️⃣ Number type choose karo\n"
        f"3️⃣ Phone number bhejo\n"
        f"4️⃣ OTP type karo\n"
        f"5️⃣ Done! ✅\n\n"
        f"💡 OTP na aaye? → Naya OTP button dabao",
        parse_mode="Markdown",
        reply_markup=get_menu_kb(uid),
    )
    return ConversationHandler.END

# ==================== LINK / CODE INPUT ====================

async def receive_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    raw = update.message.text.strip()

    if not raw:
        await update.message.reply_text(
            "❌ Valid referral link ya code bhejo:\n"
            "`https://habit.yoga/yourcode`\n"
            "_Ya sirf code: `yourcode`_",
            parse_mode="Markdown",
            reply_markup=kb_cancel(),
        )
        return ASKING_LINK

    code = extract_code(raw)
    if not code:
        await update.message.reply_text(
            "❌ *Invalid code!*\n\n"
            "Sahi format mein bhejo:\n"
            "`https://habit.yoga/yourcode`\n"
            "_Ya sirf code: `yourcode`_",
            parse_mode="Markdown",
            reply_markup=kb_cancel(),
        )
        return ASKING_LINK

    # Save the refer code
    async with _lock:
        u = get_user(uid)
        u["refer_code"] = code
    asyncio.create_task(save_data())

    await update.message.reply_text(
        f"✅ *Referral code set ho gaya!*\n"
        f"📌 Code: `{code}`\n\n"
        f"Ab 🚀 Start Workflow dabao!",
        parse_mode="Markdown",
        reply_markup=get_menu_kb(uid),
    )
    return ConversationHandler.END

# ==================== NUMBER TYPE & PHONE ====================

async def receive_number_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    uid = update.effective_user.id

    if data == "num_type_indian":
        ctx.user_data["num_type"] = "indian"
        await query.edit_message_text(
            "🇮🇳 *Indian Number* selected.\n\n"
            "📱 Ab 10-digit mobile number bhejo (e.g., `9876543210`):",
            parse_mode="Markdown",
            reply_markup=kb_cancel(),
        )
        return ASKING_PHONE

    elif data == "num_type_intl":
        ctx.user_data["num_type"] = "international"
        await query.edit_message_text(
            "🌍 *International Number* selected.\n\n"
            "📱 Pura number country code ke saath bhejo (starting with `+`)\n"
            "Example: `+25777466500`\n\n"
            "⚠️ *+ sign mandatory hai!*",
            parse_mode="Markdown",
            reply_markup=kb_cancel(),
        )
        return ASKING_PHONE

    elif data == "cancel":
        await query.edit_message_text("❌ Cancelled.")
        await send_main_menu(update, ctx)
        return ConversationHandler.END

    # Should not reach here
    return ASKING_NUM_TYPE

async def receive_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    raw_number = update.message.text.strip().replace(" ", "")

    num_type = ctx.user_data.get("num_type")
    if not num_type:
        await update.message.reply_text(
            "❌ Pehle number type select karo. /start se wapas aao.",
            reply_markup=get_menu_kb(uid),
        )
        return ConversationHandler.END

    # Validate and format phone number
    if num_type == "indian":
        if not raw_number.isdigit() or len(raw_number) != 10:
            await update.message.reply_text(
                "❌ *Invalid Indian number!* 10 digits chahiye.\nDobara bhejo:",
                parse_mode="Markdown",
                reply_markup=kb_cancel(),
            )
            return ASKING_PHONE
        phone = f"+91{raw_number}"
    else:  # international
        if not raw_number.startswith("+"):
            await update.message.reply_text(
                "❌ *International number must start with +*\n"
                "Example: `+25777466500`\n\nDobara bhejo:",
                parse_mode="Markdown",
                reply_markup=kb_cancel(),
            )
            return ASKING_PHONE
        # Basic validation: after + there should be digits (5-15)
        if not re.match(r"^\+\d{5,15}$", raw_number):
            await update.message.reply_text(
                "❌ *Invalid format!* Use + followed by 5-15 digits.\nDobara bhejo:",
                parse_mode="Markdown",
                reply_markup=kb_cancel(),
            )
            return ASKING_PHONE
        phone = raw_number

    # Check points again (safety)
    pts = get_user(uid).get("points", 0)
    if pts < 1:
        await update.message.reply_text(
            f"❌ *Points Khatam!*\n\n💡 🔗 Refer Link se dost bulao → *+{get_brp()} pts* milenge",
            parse_mode="Markdown",
            reply_markup=get_menu_kb(uid),
        )
        return ConversationHandler.END

    ctx.user_data["phone"] = phone
    refer_code = ctx.user_data.get("refer_code") or get_user(uid).get("refer_code", "")

    status = await update.message.reply_text(
        f"⏳ *Processing...*\n📱 `{phone}`\n\nStep 1/2: Register ho raha hai...",
        parse_mode="Markdown",
    )

    did, sid = rand_id(), rand_id()
    await asyncio.sleep(random.uniform(0.5, 1.5))  # human-like delay
    reg_resp, reg_err = await api_register(phone, refer_code, rand_name(), did, sid)

    if reg_err or not reg_resp:
        await status.edit_text(
            f"❌ *Registration failed!*\n{reg_err or 'No response'}\n\n"
            "⏳ Agar rate limit hai to 1-2 min wait karke try karo.\n"
            "Kya karna hai?",
            parse_mode="Markdown",
            reply_markup=kb_otp_fail(),
        )
        return ASKING_OTP

    # Check if number is already registered (old user)
    try:
        account = reg_resp.get("result", {}).get("data", {}).get("account", {})
        is_verified = account.get("is_phone_number_verified", False)
    except:
        is_verified = False

    if is_verified:
        await status.edit_text(
            f"⚠️ *Number already registered!*\n📱 `{phone}`\n\n"
            "Yeh number pehle se kisi aur ne use kar liya hai.\n"
            "Kripya koi doosra number try karein.",
            parse_mode="Markdown",
            reply_markup=kb_otp_fail(),  # gives option to retry with new number
        )
        # Clear phone from context so next attempt starts fresh
        ctx.user_data.pop("phone", None)
        return ASKING_OTP  # User can click "Naya OTP Bhejo" which will re-enter phone flow

    # New user – proceed with OTP
    await status.edit_text(
        f"✅ *New user detected!*\n📱 `{phone}`\n\n"
        f"Step 2/2: OTP bhej raha hoon...",
        parse_mode="Markdown",
    )

    otp_did, otp_sid = rand_id(), rand_id()
    ctx.user_data.update({"otp_did": otp_did, "otp_sid": otp_sid})

    otp_ref, err = await api_send_otp(phone, otp_did, otp_sid)

    if err or not otp_ref:
        try:
            await status.edit_text(
                f"⚠️ *OTP Nahi Gaya!*\n\n📱 `{phone}`\n❌ {err or 'Ref nahi mila'}\n\nKya karna hai?",
                parse_mode="Markdown", reply_markup=kb_otp_fail(),
            )
        except: pass
        return ASKING_OTP

    ctx.user_data["otp_ref"] = otp_ref
    try:
        await status.edit_text(
            f"✅ *OTP Bhej Diya!*\n📱 `{phone}`\n\n🔐 *6-digit OTP type karo:*",
            parse_mode="Markdown", reply_markup=kb_otp_input(),
        )
    except: pass
    return ASKING_OTP

# ==================== OTP VERIFICATION ====================

async def receive_otp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    otp = update.message.text.strip()

    if not otp.isdigit() or len(otp) != 6:
        await update.message.reply_text(
            "❌ *6-digit OTP chahiye!*\nDobara bhejo:",
            parse_mode="Markdown", reply_markup=kb_otp_fail(),
        )
        return ASKING_OTP

    phone   = ctx.user_data.get("phone")
    otp_ref = ctx.user_data.get("otp_ref")
    did     = ctx.user_data.get("otp_did")
    sid     = ctx.user_data.get("otp_sid")

    if not all([phone, otp_ref, did, sid]):
        await update.message.reply_text(
            "❌ Session expire ho gaya. /start karo.",
            reply_markup=get_menu_kb(uid),
        )
        return ConversationHandler.END

    proc = await update.message.reply_text("⏳ *Verify ho raha hai...*", parse_mode="Markdown")
    result, err = await api_verify_otp(phone, otp_ref, otp, did, sid)

    if err or not result:
        try:
            await proc.edit_text(
                f"❌ *OTP Galat ya Expire!*\n{err or 'Failed'}\n\nKya karna hai?",
                parse_mode="Markdown", reply_markup=kb_otp_fail(),
            )
        except: pass
        return ASKING_OTP

    member_name = result.get("data", {}).get("member", {}).get("name", "User")
    await do_otp_refer_complete(uid)

    u    = get_user(uid)
    pts  = u["points"]
    refs = u["total_refers"]
    clear_temp(ctx)

    try:
        await proc.edit_text(
            f"🎉 *REFER COMPLETE!* 🎉\n\n"
            f"✅ *Referee:* {member_name}\n"
            f"📱 *Phone:* `{phone}`\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💰 *Aapke Points:*  `{pts}`\n"
            f"🎯 *Total OTP Refers:*  `{refs}`\n"
            f"━━━━━━━━━━━━━━━━\n\n"
            f"Aur refer karna hai?",
            parse_mode="Markdown",
            reply_markup=kb_after_refer(),
        )
    except: pass
    return ConversationHandler.END

# ==================== INLINE CALLBACKS ====================
async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    data = q.data
    uid  = update.effective_user.id

    # ── channel join check ──
    if data == "check_joined":
        is_member = await is_channel_member(ctx.bot, uid)
        if not is_member:
            await q.answer("❌ Dono channels join nahi kiye!", show_alert=True)
            try:
                await q.edit_message_reply_markup(reply_markup=kb_join_channel())
            except: pass
            return ConversationHandler.END

        pending = ctx.user_data.pop("pending_referrer", None)
        if pending and pending.isdigit():
            referrer_id = int(pending)
            name = update.effective_user.first_name or "Dost"
            brp  = get_brp()
            await _reward_bot_referrer(ctx, uid, referrer_id, name, brp)

        try:
            await q.edit_message_text(
                "✅ *Channel Join Confirmed!*\n\nAapka referral complete ho gaya 🎉",
                parse_mode="Markdown",
            )
        except: pass
        await send_main_menu(update, ctx)
        return ConversationHandler.END

    # ── cancel ──
    elif data == "cancel":
        clear_temp(ctx)
        try: await q.edit_message_text("❌ Cancelled.")
        except: pass
        await ctx.bot.send_message(uid, "🏠 Main Menu:", reply_markup=get_menu_kb(uid))
        return ConversationHandler.END

    # ── main menu ──
    elif data == "main_menu":
        clear_temp(ctx)
        try: await q.edit_message_text("✅ Done!")
        except: pass
        await send_main_menu(update, ctx)
        return ConversationHandler.END

    # ── retry otp ──
    elif data == "retry_otp":
        phone = ctx.user_data.get("phone")
        if not phone:
            # No phone in context – go back to number type selection
            await q.edit_message_text("⏳ Pehle number type select karo.")
            # Restart workflow from number type
            await ctx.bot.send_message(
                uid,
                "📞 *Number type select karo:*",
                parse_mode="Markdown",
                reply_markup=kb_number_type(),
            )
            return ASKING_NUM_TYPE

        await q.edit_message_text(
            f"🔄 *Naya OTP bhej raha hoon...*\n📱 `{phone}`",
            parse_mode="Markdown",
        )
        otp_did, otp_sid = rand_id(), rand_id()
        ctx.user_data.update({"otp_did": otp_did, "otp_sid": otp_sid})
        otp_ref, err = await api_send_otp(phone, otp_did, otp_sid)
        if err or not otp_ref:
            await q.edit_message_text(
                f"⚠️ *Phir Nahi Gaya!*\n📱 `{phone}`\n❌ {err}\n\nKya karna hai?",
                parse_mode="Markdown", reply_markup=kb_otp_fail(),
            )
            return ASKING_OTP
        ctx.user_data["otp_ref"] = otp_ref
        await q.edit_message_text(
            f"✅ *Naya OTP Bheja!*\n📱 `{phone}`\n\n🔐 *OTP type karo:*",
            parse_mode="Markdown", reply_markup=kb_otp_input(),
        )
        return ASKING_OTP

    # ── refer more ──
    elif data == "refer_more":
        pts = get_user(uid).get("points", 0)
        if pts < 1:
            try: await q.edit_message_text(f"❌ Points khatam! 🔗 Refer Link se dost bulao → +{get_brp()} pts milenge.")
            except: pass
            await ctx.bot.send_message(uid, "🏠 Main Menu:", reply_markup=get_menu_kb(uid))
            return ConversationHandler.END
        # Restart with number type selection
        try: await q.edit_message_text("👍 Agla number ke liye type select karo!")
        except: pass
        await ctx.bot.send_message(
            uid,
            "📞 *Number type select karo:*",
            parse_mode="Markdown",
            reply_markup=kb_number_type(),
        )
        return ASKING_NUM_TYPE

    # ── admin callbacks ──
    elif uid not in ADMIN_IDS:
        return ConversationHandler.END

    if data == "adm_users":
        users = _data.get("users", {})
        if not users:
            await q.edit_message_text(
                "Koi user nahi.",
                reply_markup=kb_inline([InlineKeyboardButton("🔙 Back", callback_data="adm_back")]),
            )
            return ConversationHandler.END
        lines = ["👥 *All Users:*\n"]
        for i, (u_id, ud) in enumerate(users.items(), 1):
            lines.append(
                f"{i}. `{u_id}` — *{ud.get('name','?')}*\n"
                f"   💰 {ud.get('points',0)} pts | 🎯 {ud.get('total_refers',0)} OTP | 🤝 {ud.get('bot_refers',0)} Bot"
            )
            if i >= 50: lines.append("_(+aur hain)_"); break
        try:
            await q.edit_message_text(
                "\n".join(lines), parse_mode="Markdown",
                reply_markup=kb_inline([InlineKeyboardButton("🔙 Back", callback_data="adm_back")]),
            )
        except:
            await ctx.bot.send_message(uid, "\n".join(lines), parse_mode="Markdown")

    elif data == "adm_add_pts":
        await q.edit_message_text("➕ *Points Add*\n\nUser ka Telegram ID bhejo:", parse_mode="Markdown")
        return ADM_ADD_UID

    elif data == "adm_rem_pts":
        await q.edit_message_text("➖ *Points Remove*\n\nUser ka Telegram ID bhejo:", parse_mode="Markdown")
        return ADM_REM_UID

    elif data == "adm_bot_pts":
        brp = get_brp()
        await q.edit_message_text(
            f"🤖 *Bot Refer Points*\n\nAbhi: *{brp} pts*\n\nNaya value bhejo:",
            parse_mode="Markdown",
        )
        return ADM_BOT_PTS_AMT

    elif data == "adm_signup_bonus":
        sb = get_signup_bonus()
        await q.edit_message_text(
            f"🎁 *Signup Bonus Set*\n\nAbhi: *{sb} pts*\n\nNaya value bhejo (0 = band karo):",
            parse_mode="Markdown",
        )
        return ADM_SIGNUP_AMT

    elif data == "adm_broadcast":
        await q.edit_message_text("📣 *Broadcast*\n\nMessage type karo (sabko jayega):", parse_mode="Markdown")
        return ADM_BROADCAST_MSG

    elif data == "adm_close":
        await q.edit_message_text("✅ Admin panel band.")
        return ConversationHandler.END

    elif data == "adm_back":
        brp   = get_brp()
        sb    = get_signup_bonus()
        total = len(_data.get("users", {}))
        total_refs = sum(u.get("total_refers", 0) for u in _data.get("users", {}).values())
        await q.edit_message_text(
            f"👑 *Admin Panel*\n\n"
            f"👥 Users: *{total}*\n"
            f"✅ Total OTP Refers: *{total_refs}*\n"
            f"🤖 Bot Refer Reward: *{brp} pts*\n"
            f"🎁 Signup Bonus: *{sb} pts*",
            parse_mode="Markdown",
            reply_markup=kb_admin_main(),
        )

    return ConversationHandler.END

# ==================== ADMIN HANDLERS ====================

async def admin_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Access denied.")
        return ConversationHandler.END

    brp   = get_brp()
    sb    = get_signup_bonus()
    total = len(_data.get("users", {}))
    total_refs = sum(u.get("total_refers", 0) for u in _data.get("users", {}).values())
    total_brefs = sum(u.get("bot_refers", 0) for u in _data.get("users", {}).values())

    await update.message.reply_text(
        f"👑 *Admin Panel*\n\n"
        f"👥 *Total Users:* `{total}`\n"
        f"🎯 *OTP Refers:* `{total_refs}`\n"
        f"🤝 *Bot Refers:* `{total_brefs}`\n"
        f"🤖 *Bot Refer Reward:* `{brp} pts`\n"
        f"🎁 *Signup Bonus:* `{sb} pts`\n\n"
        f"Choose option:",
        parse_mode="Markdown",
        reply_markup=kb_admin_main(),
    )
    return ADM_MAIN

async def adm_recv_add_uid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    txt = update.message.text.strip()
    if not txt.isdigit():
        await update.message.reply_text("❌ Sirf Telegram ID (number) bhejo:"); return ADM_ADD_UID
    ctx.user_data["target_uid"] = int(txt)
    u = get_user(int(txt))
    await update.message.reply_text(
        f"User: `{txt}`\nCurrent: *{u['points']} pts*\n\nKitne add karne hain?",
        parse_mode="Markdown",
    )
    return ADM_ADD_AMT

async def adm_recv_add_amt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    txt = update.message.text.strip()
    if not txt.isdigit() or int(txt) <= 0:
        await update.message.reply_text("❌ Valid number bhejo:"); return ADM_ADD_AMT
    amt   = int(txt)
    t_uid = ctx.user_data.get("target_uid")
    await add_points(t_uid, amt)
    u = get_user(t_uid)
    await update.message.reply_text(
        f"✅ *Done!*\n`{t_uid}` ko *+{amt} pts* mile!\nNew balance: *{u['points']}*",
        parse_mode="Markdown", reply_markup=kb_admin_main(),
    )
    try:
        await ctx.bot.send_message(
            t_uid,
            f"🎉 *Points Mila!*\n\n➕ *+{amt} points* add hue!\n💰 Total: *{u['points']}*",
            parse_mode="Markdown",
        )
    except: pass
    return ConversationHandler.END

async def adm_recv_rem_uid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    txt = update.message.text.strip()
    if not txt.isdigit():
        await update.message.reply_text("❌ Sirf Telegram ID bhejo:"); return ADM_REM_UID
    ctx.user_data["target_uid"] = int(txt)
    u = get_user(int(txt))
    await update.message.reply_text(
        f"User: `{txt}`\nCurrent: *{u['points']} pts*\n\nKitne remove karne hain?",
        parse_mode="Markdown",
    )
    return ADM_REM_AMT

async def adm_recv_rem_amt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    txt = update.message.text.strip()
    if not txt.isdigit() or int(txt) <= 0:
        await update.message.reply_text("❌ Valid number bhejo:"); return ADM_REM_AMT
    amt   = int(txt)
    t_uid = ctx.user_data.get("target_uid")
    await add_points(t_uid, -amt)
    u = get_user(t_uid)
    await update.message.reply_text(
        f"✅ Done!\n`{t_uid}` se *-{amt} pts*!\nNew: *{u['points']}*",
        parse_mode="Markdown", reply_markup=kb_admin_main(),
    )
    return ConversationHandler.END

async def adm_recv_bot_pts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    txt = update.message.text.strip()
    if not txt.isdigit() or int(txt) <= 0:
        await update.message.reply_text("❌ Valid number bhejo:"); return ADM_BOT_PTS_AMT
    async with _lock:
        _data["settings"]["bot_refer_points"] = int(txt)
    asyncio.create_task(save_data())
    await update.message.reply_text(
        f"✅ *Bot Refer Reward = {txt} pts* set!", parse_mode="Markdown", reply_markup=kb_admin_main(),
    )
    return ConversationHandler.END

async def adm_recv_signup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    txt = update.message.text.strip()
    if not txt.isdigit() or int(txt) < 0:
        await update.message.reply_text("❌ 0 ya usse bada number bhejo:"); return ADM_SIGNUP_AMT
    async with _lock:
        _data["settings"]["signup_bonus"] = int(txt)
    asyncio.create_task(save_data())
    msg = f"✅ *Signup Bonus = {txt} pts* set!" if int(txt) > 0 else "✅ *Signup Bonus band kar diya!*"
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb_admin_main())
    return ConversationHandler.END

async def adm_recv_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    msg   = update.message.text.strip()
    users = list(_data.get("users", {}).keys())
    sent = fail = 0
    status = await update.message.reply_text(f"📣 Sending to {len(users)} users...")
    for u_id in users:
        try:
            await ctx.bot.send_message(int(u_id), f"📣 *Admin Message:*\n\n{msg}", parse_mode="Markdown")
            sent += 1
            await asyncio.sleep(0.04)
        except: fail += 1
    await status.edit_text(f"✅ Broadcast done!\n✅ Sent: {sent}\n❌ Failed: {fail}")
    return ConversationHandler.END

# ==================== MISC ====================
async def cancel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    clear_temp(ctx)
    await update.message.reply_text("❌ Cancelled.\n🏠 Main Menu:", reply_markup=get_menu_kb(uid))
    return ConversationHandler.END

async def unknown_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text("👇 Button se karo:", reply_markup=get_menu_kb(uid))

async def btn_admin_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        await update.message.reply_text("❌ Access denied.")
        return ConversationHandler.END
    brp   = get_brp()
    sb    = get_signup_bonus()
    total = len(_data.get("users", {}))
    total_refs = sum(u.get("total_refers", 0) for u in _data.get("users", {}).values())
    await update.message.reply_text(
        f"👑 *Admin Panel*\n\n"
        f"👥 Users: *{total}*\n"
        f"✅ Total OTP Refers: *{total_refs}*\n"
        f"🤖 Bot Refer Reward: *{brp} pts*\n"
        f"🎁 Signup Bonus: *{sb} pts*",
        parse_mode="Markdown",
        reply_markup=kb_admin_main(),
    )
    return ADM_MAIN

# ==================== SETUP ====================
NOT_BTN = filters.TEXT & ~filters.COMMAND & ~filters.Regex(
    "^(" + "|".join(ALL_BTNS) + ")$"
)

async def post_init(app):
    global _BOT_USERNAME
    await load_data()
    await prefetch_cookies()  # get anti-bot cookies before any API calls
    me = await app.bot.get_me()
    _BOT_USERNAME = me.username
    logging.info(f"Bot: @{_BOT_USERNAME}")

async def post_shutdown(app):
    global _session
    await save_data()
    if _session and not _session.closed:
        await _session.close()

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .concurrent_updates(True)
        .connection_pool_size(128)
        .connect_timeout(10)
        .read_timeout(30)
        .write_timeout(30)
        .build()
    )

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start",  start_cmd),
            CommandHandler("admin",  admin_cmd),
            MessageHandler(filters.Regex(f"^{BTN_WORKFLOW}$"),   btn_workflow),
            MessageHandler(filters.Regex(f"^{BTN_CHANGE}$"),     btn_code_update),
            MessageHandler(filters.Regex(f"^{re.escape(BTN_ADMIN)}$"), btn_admin_panel),
        ],
        states={
            ASKING_LINK:      [MessageHandler(NOT_BTN, receive_link),  CallbackQueryHandler(callback_handler)],
            ASKING_NUM_TYPE:  [CallbackQueryHandler(receive_number_type)],
            ASKING_PHONE:     [MessageHandler(NOT_BTN, receive_phone), CallbackQueryHandler(callback_handler)],
            ASKING_OTP:       [MessageHandler(NOT_BTN, receive_otp),   CallbackQueryHandler(callback_handler)],
            ADM_MAIN:         [
                CallbackQueryHandler(callback_handler),
                MessageHandler(filters.Regex(f"^{re.escape(BTN_ADMIN)}$"), btn_admin_panel),
            ],
            ADM_ADD_UID:      [MessageHandler(NOT_BTN, adm_recv_add_uid)],
            ADM_ADD_AMT:      [MessageHandler(NOT_BTN, adm_recv_add_amt)],
            ADM_REM_UID:      [MessageHandler(NOT_BTN, adm_recv_rem_uid)],
            ADM_REM_AMT:      [MessageHandler(NOT_BTN, adm_recv_rem_amt)],
            ADM_BOT_PTS_AMT:  [MessageHandler(NOT_BTN, adm_recv_bot_pts)],
            ADM_SIGNUP_AMT:   [MessageHandler(NOT_BTN, adm_recv_signup)],
            ADM_BROADCAST_MSG:[MessageHandler(NOT_BTN, adm_recv_broadcast)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_cmd),
            MessageHandler(filters.Regex(f"^{BTN_STATS}$"),  btn_total_stats),
            MessageHandler(filters.Regex(f"^{BTN_LINK}$"),   btn_refer_link),
            MessageHandler(filters.Regex(f"^{BTN_HELP}$"),   btn_help),
            MessageHandler(filters.Regex(f"^{re.escape(BTN_ADMIN)}$"), btn_admin_panel),
            CallbackQueryHandler(callback_handler),
        ],
        per_user=True,
        per_chat=True,
        allow_reentry=True,
        conversation_timeout=600,
    )

    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_STATS}$"), btn_total_stats))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_LINK}$"),  btn_refer_link))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_HELP}$"),  btn_help))
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(BTN_ADMIN)}$"), btn_admin_panel))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_msg))

    print(f"🚀 Bot ready! Admin: {ADMIN_ID}")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
