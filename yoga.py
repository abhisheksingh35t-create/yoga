#!/usr/bin/env python3
"""
Habit.Yoga Referral Bot v2
- Habit.yoga OTP workflow: 1 point cost, admin-set reward per refer
- Bot referral system: share bot link â†’ friend joins force channel â†’ you get points
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
BOT_TOKEN     = "8304089414:AAH_zuOyKUxANR_dzmIl1QLfx_kF2bp8Pe0"
ADMIN_ID      = 1446058092
DATA_FILE     = "bot_data.json"
_BOT_USERNAME = "@Anendj2000_bot"

REGISTER_URL = "https://auth-service.habuild.in/public/user/v1/register-user"
LOGIN_URL    = "https://auth-service.habuild.in/public/auth/v1/login"
VERIFY_URL   = "https://auth-service.habuild.in/public/auth/v1/verify-otp"

HEADERS = {
    "accept": "application/json",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/json",
    "origin": "https://habit.yoga",
    "referer": "https://habit.yoga/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "cross-site",
    "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1",
}
REG_HEADERS = {**HEADERS, "authorization": "Bearer"}

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
            logging.info(f"âœ… Data loaded: {len(_data['users'])} users")
        except Exception as e:
            logging.warning(f"Load failed: {e}")

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

async def api_post(url, payload, headers):
    s = await get_session()
    try:
        async with s.post(url, json=payload, headers=headers) as r:
            text = await r.text()
            logging.info(f"API {url} â†’ {r.status}: {text[:200]}")
            if r.status in (200, 201):
                try: return json.loads(text), None
                except: return None, "Invalid JSON"
            return None, f"HTTP {r.status}: {text[:150]}"
    except asyncio.TimeoutError: return None, "Timeout"
    except Exception as e:     return None, str(e)

async def api_register(phone, code, name, did, sid):
    return await api_post(REGISTER_URL, {
        "name": name, "phoneNumber": phone, "referredBy": code,
        "sourceData": {"type": "Referral", "refererurl": "", "timezone": "Asia/Kolkata"},
        "experimentMetaInfo": {"deviceId": did, "sessionId": sid},
    }, REG_HEADERS)

async def api_send_otp(phone, did, sid):
    resp, err = await api_post(LOGIN_URL, {
        "method": "phone_otp", "otpChannel": "sms", "phoneNumber": phone,
        "sourceData": {"type": "portal", "utm_source": "web_app"},
        "experimentMetaInfo": {"deviceId": did, "sessionId": sid},
        "registerUser": False,
    }, HEADERS)
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
    }, HEADERS)

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

FORCE_CHANNELS = [""]  # Add channel usernames without @ if needed

async def is_channel_member(bot, uid: int) -> bool:
    for ch in FORCE_CHANNELS:
        if not ch:
            continue
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

BTN_WORKFLOW = "ðŸš€ Start Workflow"
BTN_STATS    = "ðŸ“Š Total Stats"
BTN_LINK     = "ðŸ”— Refer Link"
BTN_CHANGE   = "ðŸ”„ Code Update"
BTN_HELP     = "ðŸ’¡ Help"
BTN_ADMIN    = "ðŸ‘‘ Admin Panel"
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
    return admin_menu_kb() if uid == ADMIN_ID else main_menu_kb()

def kb_inline(*rows):
    return InlineKeyboardMarkup(rows)

def kb_cancel():
    return kb_inline([InlineKeyboardButton("âŒ Cancel", callback_data="cancel")])

def kb_otp_fail():
    return kb_inline([
        InlineKeyboardButton("ðŸ”„ Naya OTP Bhejo", callback_data="retry_otp"),
        InlineKeyboardButton("âŒ Cancel",           callback_data="cancel"),
    ])

def kb_otp_input():
    return kb_inline([InlineKeyboardButton("âŒ Cancel", callback_data="cancel")])

def kb_after_refer():
    return kb_inline([
        InlineKeyboardButton("âš¡ï¸ Ek Aur Refer", callback_data="refer_more"),
        InlineKeyboardButton("ðŸ  Main Menu",     callback_data="main_menu"),
    ])

def kb_join_channel():
    rows = [[InlineKeyboardButton(f"ðŸ“¢ {ch} Join Karo", url=f"https://t.me/{ch.lstrip('@')}")] for ch in FORCE_CHANNELS if ch]
    rows.append([InlineKeyboardButton("âœ… qr mango", callback_data="check_joined")])
    return kb_inline(*rows)

def kb_admin_main():
    return kb_inline(
        [InlineKeyboardButton("ðŸ‘¥ All Users",               callback_data="adm_users")],
        [InlineKeyboardButton("âž• Points Add",               callback_data="adm_add_pts"),
         InlineKeyboardButton("âž– Points Remove",            callback_data="adm_rem_pts")],
        [InlineKeyboardButton("ðŸ¤– Bot Refer Points",         callback_data="adm_bot_pts")],
        [InlineKeyboardButton("ðŸŽ Signup Bonus",             callback_data="adm_signup_bonus")],
        [InlineKeyboardButton("ðŸ“£ Broadcast",                callback_data="adm_broadcast")],
        [InlineKeyboardButton("âŒ Close",                     callback_data="adm_close")],
    )

def kb_number_type():
    return kb_inline(
        [InlineKeyboardButton("ðŸ‡®ðŸ‡³ Indian (10 digits)", callback_data="num_type_indian")],
        [InlineKeyboardButton("ðŸŒ International (with +)", callback_data="num_type_intl")],
        [InlineKeyboardButton("âŒ Cancel", callback_data="cancel")],
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

# ==================== HANDLER: receive_link (FIXED) ====================
async def receive_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle user-provided Habit.Yoga referral link/code"""
    uid = update.effective_user.id
    text = update.message.text.strip()
    code = extract_code(text)
    
    if not code:
        await update.message.reply_text(
            "âŒ *Invalid referral link/code!*\n\n"
            "Please send a valid Habit.Yoga link like:\n"
            "`https://habit.yoga/yourcode`\n"
            "or just the code: `yourcode`\n\n"
            "Try again or press Cancel.",
            parse_mode="Markdown",
            reply_markup=kb_cancel(),
        )
        return ASKING_LINK
    
    # Save the code to user data
    async with _lock:
        u = get_user(uid)
        u["refer_code"] = code
    asyncio.create_task(save_data())
    
    await update.message.reply_text(
        f"âœ… *Referral code updated!*\n\n"
        f"Your code: `{code}`\n\n"
        f"Now you can use ðŸš€ Start Workflow to refer friends.\n"
        f"Each referral costs 1 point.",
        parse_mode="Markdown",
        reply_markup=get_menu_kb(uid),
    )
    return ConversationHandler.END

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
        f"ðŸ  *Main Menu*\n"
        f"ðŸ‘¤ *{name}*\n\n"
        f"â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”\n"
        f"ðŸ’° *Points:*  `{pts}`\n"
        f"â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”\n\n"
        f"ðŸ‘‡ *Option choose karo:*"
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
        ch_list = "\n".join(f"   â€¢ {ch}" for ch in FORCE_CHANNELS if ch)
        await update.message.reply_text(
            f"ðŸ‘‹ *Welcome {name}!*\n\n"
            f"ðŸŽ‰ Ek dost ne tumhe refer kiya!\n"
            f"ðŸŽ Unhe milega: *+{brp} points*\n\n"
            f"âš ï¸ *Reward ke liye dono channels join karo:*\n{ch_list}\n\n"
            f"ðŸ‘‡ Niche buttons se join karo, phir âœ… dabao!",
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
            f"ðŸŽ‰ *Bot Refer Complete!*\n\n"
            f"âœ… *{new_name}* channel join kar liya!\n"
            f"ðŸŽ *+{brp} points* aapke account mein add ho gaye!\n\n"
            f"ðŸ’° *Aapke Total Points:* `{ref_u['points']}`\n"
            f"ðŸ¤ *Total Bot Refers:* `{ref_u['bot_refers']}`",
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
            "âš ï¸ *Pehle apna Habit.Yoga code set karo!*\n\n"
            "Apna referral link ya code bhejo:\n"
            "`https://habit.yoga/yourcode`",
            parse_mode="Markdown",
            reply_markup=kb_cancel(),
        )
        return ASKING_LINK

    if pts < 1:
        await update.message.reply_text(
            f"âŒ *Points Khatam!*\n\n"
            f"ðŸ’° Aapke Points: *{pts}*\n\n"
            f"ðŸ’¡ *Points kaise kamao:*\n"
            f"â†’ ðŸ”— Refer Link se dost bulao â†’ *+{get_brp()} pts* milenge\n"
            f"â†’ 1 point = 1 workflow chalao\n"
            f"â†’ Jitne refers utne points!",
            parse_mode="Markdown",
            reply_markup=get_menu_kb(uid),
        )
        return ConversationHandler.END

    # Store the refer code in context for later use
    ctx.user_data["refer_code"] = code

    # Ask for number type first
    await update.message.reply_text(
        "ðŸ“ž *Pehle number type select karo:*",
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
        f"ðŸ“Š *Total Bot Stats*\n\n"
        f"â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”\n"
        f"ðŸ‘¥ *Total Users:*  `{st['users']}`\n"
        f"âœ… *OTP Refers Done:*  `{st['refers']}`\n"
        f"ðŸ’° *Total Points Distributed:* `{st['points']}`\n"
        f"â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”\n\n"
        f"ðŸ‘¤ *Aapki Personal Stats:*\n"
        f"ðŸ’° Points: *{u['points']}*\n"
        f"ðŸŽ¯ OTP Refers: *{u['total_refers']}*\n"
        f"ðŸ¤ Bot Refers: *{u['bot_refers']}*",
        parse_mode="Markdown",
        reply_markup=get_menu_kb(uid),
    )
    return ConversationHandler.END

async def btn_refer_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    u    = get_user(uid)
    brp  = get_brp()
    link = bot_refer_link(uid)
    ch_lines = "\n".join(f"ðŸ“¢ `{ch}`" for ch in FORCE_CHANNELS if ch)
    await update.message.reply_text(
        f"ðŸ”— *Aapka Bot Referral Link*\n\n"
        f"`{link}`\n\n"
        f"ðŸ“¤ *admin se qr mango!*\n"
        f"âœ… after payment point add ho jayenge *+{brp} points* milenge!\n\n"
        f"{ch_lines}\n\n"
        f"ðŸ¤ *Aapke Total Bot Refers:* `{u.get('bot_refers', 0)}`",
        parse_mode="Markdown",
        reply_markup=get_menu_kb(uid),
    )
    return ConversationHandler.END

async def btn_code_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ðŸ”„ *Naya Habit.Yoga Referral Link/Code Bhejo:*\n\n"
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
        f"ðŸ’¡ *Help Guide*\n\n"
        f"â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”\n"
        f"ðŸš€ *Start Workflow*\n"
        f"   â†’ Habit.Yoga ka OTP refer karo\n"
        f"   â†’ 1 refer = 1 point use\n\n"
        f"ðŸ”— *Refer Link*\n"
        f"   â†’ Apna bot invite link share karo\n"
        f"   â†’ Dost join kare + dono channels join kare â†’ *+{brp} pts* milenge!\n\n"
        f"ðŸ“Š *Total Stats*  â†’ Bot ki puri stats dekho\n"
        f"ðŸ”„ *Code Update*  â†’ Naya Habit.Yoga code set karo\n\n"
        f"â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”\n"
        f"ðŸ“Œ *Workflow Steps:*\n"
        f"1ï¸âƒ£ ðŸš€ Start Workflow dabao\n"
        f"2ï¸âƒ£ Number type choose karo\n"
        f"3ï¸âƒ£ Phone number bhejo\n"
        f"4ï¸âƒ£ OTP type karo\n"
        f"5ï¸âƒ£ Done! âœ…\n\n"
        f"ðŸ’¡ OTP na aaye? â†’ Naya OTP button dabao",
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
            "ðŸ‡®ðŸ‡³ *Indian Number* selected.\n\n"
            "ðŸ“± Ab 10-digit mobile number bhejo (e.g., `9876543210`):",
            parse_mode="Markdown",
            reply_markup=kb_cancel(),
        )
        return ASKING_PHONE

    elif data == "num_type_intl":
        ctx.user_data["num_type"] = "international"
        await query.edit_message_text(
            "ðŸŒ *International Number* selected.\n\n"
            "ðŸ“± Pura number country code ke saath bhejo (starting with `+`)\n"
            "Example: `+25777466500`\n\n"
            "âš ï¸ *+ sign mandatory hai!*",
            parse_mode="Markdown",
            reply_markup=kb_cancel(),
        )
        return ASKING_PHONE

    elif data == "cancel":
        await query.edit_message_text("âŒ Cancelled.")
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
            "âŒ Pehle number type select karo. /start se wapas aao.",
            reply_markup=get_menu_kb(uid),
        )
        return ConversationHandler.END

    # Validate and format phone number
    if num_type == "indian":
        if not raw_number.isdigit() or len(raw_number) != 10:
            await update.message.reply_text(
                "âŒ *Invalid Indian number!* 10 digits chahiye.\nDobara bhejo:",
                parse_mode="Markdown",
                reply_markup=kb_cancel(),
            )
            return ASKING_PHONE
        phone = f"+91{raw_number}"
    else:  # international
        if not raw_number.startswith("+"):
            await update.message.reply_text(
                "âŒ *International number must start with +*\n"
                "Example: `+25777466500`\n\nDobara bhejo:",
                parse_mode="Markdown",
                reply_markup=kb_cancel(),
            )
            return ASKING_PHONE
        # Basic validation: after + there should be digits (5-15)
        if not re.match(r"^\+\d{5,15}$", raw_number):
            await update.message.reply_text(
                "âŒ *Invalid format!* Use + followed by 5-15 digits.\nDobara bhejo:",
                parse_mode="Markdown",
                reply_markup=kb_cancel(),
            )
            return ASKING_PHONE
        phone = raw_number

    # Check points again (safety)
    pts = get_user(uid).get("points", 0)
    if pts < 1:
        await update.message.reply_text(
            f"âŒ *Points Khatam!*\n\nðŸ’¡ ðŸ”— Refer Link se dost bulao â†’ *+{get_brp()} pts* milenge",
            parse_mode="Markdown",
            reply_markup=get_menu_kb(uid),
        )
        return ConversationHandler.END

    ctx.user_data["phone"] = phone
    refer_code = ctx.user_data.get("refer_code") or get_user(uid).get("refer_code", "")

    status = await update.message.reply_text(
        f"â³ *Processing...*\nðŸ“± `{phone}`\n\nStep 1/2: Register ho raha hai...",
        parse_mode="Markdown",
    )

    did, sid = rand_id(), rand_id()
    reg_resp, reg_err = await api_register(phone, refer_code, rand_name(), did, sid)

    if reg_err or not reg_resp:
        await status.edit_text(
            f"âŒ *Registration failed!*\n{reg_err or 'No response'}\n\n"
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
            f"âš ï¸ *Number already registered!*\nðŸ“± `{phone}`\n\n"
            "Yeh number pehle se kisi aur ne use kar liya hai.\n"
            "Kripya koi doosra number try karein.",
            parse_mode="Markdown",
            reply_markup=kb_otp_fail(),  # gives option to retry with new number
        )
        # Clear phone from context so next attempt starts fresh
        ctx.user_data.pop("phone", None)
        return ASKING_OTP  # User can click "Naya OTP Bhejo" which will re-enter phone flow

    # New user â€“ proceed with OTP
    await status.edit_text(
        f"âœ… *New user detected!*\nðŸ“± `{phone}`\n\n"
        f"Step 2/2: OTP bhej raha hoon...",
        parse_mode="Markdown",
    )

    otp_did, otp_sid = rand_id(), rand_id()
    ctx.user_data.update({"otp_did": otp_did, "otp_sid": otp_sid})

    otp_ref, err = await api_send_otp(phone, otp_did, otp_sid)

    if err or not otp_ref:
        try:
            await status.edit_text(
                f"âš ï¸ *OTP Nahi Gaya!*\n\nðŸ“± `{phone}`\nâŒ {err or 'Ref nahi mila'}\n\nKya karna hai?",
                parse_mode="Markdown", reply_markup=kb_otp_fail(),
            )
        except: pass
        return ASKING_OTP

    ctx.user_data["otp_ref"] = otp_ref
    try:
        await status.edit_text(
            f"âœ… *OTP Bhej Diya!*\nðŸ“± `{phone}`\n\nðŸ” *6-digit OTP type karo:*",
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
            "âŒ *6-digit OTP chahiye!*\nDobara bhejo:",
            parse_mode="Markdown", reply_markup=kb_otp_fail(),
        )
        return ASKING_OTP

    phone   = ctx.user_data.get("phone")
    otp_ref = ctx.user_data.get("otp_ref")
    did     = ctx.user_data.get("otp_did")
    sid     = ctx.user_data.get("otp_sid")

    if not all([phone, otp_ref, did, sid]):
        await update.message.reply_text(
            "âŒ Session expire ho gaya. /start karo.",
            reply_markup=get_menu_kb(uid),
        )
        return ConversationHandler.END

    proc = await update.message.reply_text("â³ *Verify ho raha hai...*", parse_mode="Markdown")
    result, err = await api_verify_otp(phone, otp_ref, otp, did, sid)

    if err or not result:
        try:
            await proc.edit_text(
                f"âŒ *OTP Galat ya Expire!*\n{err or 'Failed'}\n\nKya karna hai?",
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
            f"ðŸŽ‰ *REFER COMPLETE!* ðŸŽ‰\n\n"
            f"âœ… *Referee:* {member_name}\n"
            f"ðŸ“± *Phone:* `{phone}`\n\n"
            f"â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”\n"
            f"ðŸ’° *Aapke Points:*  `{pts}`\n"
            f"ðŸŽ¯ *Total OTP Refers:*  `{refs}`\n"
            f"â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”\n\n"
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

    # â”€â”€ channel join check â”€â”€
    if data == "check_joined":
        is_member = await is_channel_member(ctx.bot, uid)
        if not is_member:
            await q.answer("âŒ Dono channels join nahi kiye!", show_alert=True)
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
                "âœ… *Channel Join Confirmed!*\n\nAapka referral complete ho gaya ðŸŽ‰",
                parse_mode="Markdown",
            )
        except: pass
        await send_main_menu(update, ctx)
        return ConversationHandler.END

    # â”€â”€ cancel â”€â”€
    elif data == "cancel":
        clear_temp(ctx)
        try: await q.edit_message_text("âŒ Cancelled.")
        except: pass
        await ctx.bot.send_message(uid, "ðŸ  Main Menu:", reply_markup=get_menu_kb(uid))
        return ConversationHandler.END

    # â”€â”€ main menu â”€â”€
    elif data == "main_menu":
        clear_temp(ctx)
        try: await q.edit_message_text("âœ… Done!")
        except: pass
        await send_main_menu(update, ctx)
        return ConversationHandler.END

    # â”€â”€ retry otp â”€â”€
    elif data == "retry_otp":
        phone = ctx.user_data.get("phone")
        if not phone:
            # No phone in context â€“ go back to number type selection
            await q.edit_message_text("â³ Pehle number type select karo.")
            # Restart workflow from number type
            await ctx.bot.send_message(
                uid,
                "ðŸ“ž *Number type select karo:*",
                parse_mode="Markdown",
                reply_markup=kb_number_type(),
            )
            return ASKING_NUM_TYPE

        await q.edit_message_text(
            f"ðŸ”„ *Naya OTP bhej raha hoon...*\nðŸ“± `{phone}`",
            parse_mode="Markdown",
        )
        otp_did, otp_sid = rand_id(), rand_id()
        ctx.user_data.update({"otp_did": otp_did, "otp_sid": otp_sid})
        otp_ref, err = await api_send_otp(phone, otp_did, otp_sid)
        if err or not otp_ref:
            await q.edit_message_text(
                f"âš ï¸ *Phir Nahi Gaya!*\nðŸ“± `{phone}`\nâŒ {err}\n\nKya karna hai?",
                parse_mode="Markdown", reply_markup=kb_otp_fail(),
            )
            return ASKING_OTP
        ctx.user_data["otp_ref"] = otp_ref
        await q.edit_message_text(
            f"âœ… *Naya OTP Bheja!*\nðŸ“± `{phone}`\n\nðŸ” *OTP type karo:*",
            parse_mode="Markdown", reply_markup=kb_otp_input(),
        )
        return ASKING_OTP

    # â”€â”€ refer more â”€â”€
    elif data == "refer_more":
        pts = get_user(uid).get("points", 0)
        if pts < 1:
            try: await q.edit_message_text(f"âŒ Points khatam! ðŸ”— Refer Link se dost bulao â†’ +{get_brp()} pts milenge.")
            except: pass
            await ctx.bot.send_message(uid, "ðŸ  Main Menu:", reply_markup=get_menu_kb(uid))
            return ConversationHandler.END
        # Restart with number type selection
        try: await q.edit_message_text("ðŸ‘ Agla number ke liye type select karo!")
        except: pass
        await ctx.bot.send_message(
            uid,
            "ðŸ“ž *Number type select karo:*",
            parse_mode="Markdown",
            reply_markup=kb_number_type(),
        )
        return ASKING_NUM_TYPE

    # â”€â”€ admin callbacks â”€â”€
    elif uid != ADMIN_ID:
        return ConversationHandler.END

    if data == "adm_users":
        users = _data.get("users", {})
        if not users:
            await q.edit_message_text(
                "Koi user nahi.",
                reply_markup=kb_inline([InlineKeyboardButton("ðŸ”™ Back", callback_data="adm_back")]),
            )
            return ConversationHandler.END
        lines = ["ðŸ‘¥ *All Users:*\n"]
        for i, (u_id, ud) in enumerate(users.items(), 1):
            lines.append(
                f"{i}. `{u_id}` â€” *{ud.get('name','?')}*\n"
                f"   ðŸ’° {ud.get('points',0)} pts | ðŸŽ¯ {ud.get('total_refers',0)} OTP | ðŸ¤ {ud.get('bot_refers',0)} Bot"
            )
            if i >= 50: lines.append("_(+aur hain)_"); break
        try:
            await q.edit_message_text(
                "\n".join(lines), parse_mode="Markdown",
                reply_markup=kb_inline([InlineKeyboardButton("ðŸ”™ Back", callback_data="adm_back")]),
            )
        except:
            await ctx.bot.send_message(uid, "\n".join(lines), parse_mode="Markdown")

    elif data == "adm_add_pts":
        await q.edit_message_text("âž• *Points Add*\n\nUser ka Telegram ID bhejo:", parse_mode="Markdown")
        return ADM_ADD_UID

    elif data == "adm_rem_pts":
        await q.edit_message_text("âž– *Points Remove*\n\nUser ka Telegram ID bhejo:", parse_mode="Markdown")
        return ADM_REM_UID

    elif data == "adm_bot_pts":
        brp = get_brp()
        await q.edit_message_text(
            f"ðŸ¤– *Bot Refer Points*\n\nAbhi: *{brp} pts*\n\nNaya value bhejo:",
            parse_mode="Markdown",
        )
        return ADM_BOT_PTS_AMT

    elif data == "adm_signup_bonus":
        sb = get_signup_bonus()
        await q.edit_message_text(
            f"ðŸŽ *Signup Bonus Set*\n\nAbhi: *{sb} pts*\n\nNaya value bhejo (0 = band karo):",
            parse_mode="Markdown",
        )
        return ADM_SIGNUP_AMT

    elif data == "adm_broadcast":
        await q.edit_message_text("ðŸ“£ *Broadcast*\n\nMessage type karo (sabko jayega):", parse_mode="Markdown")
        return ADM_BROADCAST_MSG

    elif data == "adm_close":
        await q.edit_message_text("âœ… Admin panel band.")
        return ConversationHandler.END

    elif data == "adm_back":
        brp   = get_brp()
        sb    = get_signup_bonus()
        total = len(_data.get("users", {}))
        total_refs = sum(u.get("total_refers", 0) for u in _data.get("users", {}).values())
        await q.edit_message_text(
            f"ðŸ‘‘ *Admin Panel*\n\n"
            f"ðŸ‘¥ Users: *{total}*\n"
            f"âœ… Total OTP Refers: *{total_refs}*\n"
            f"ðŸ¤– Bot Refer Reward: *{brp} pts*\n"
            f"ðŸŽ Signup Bonus: *{sb} pts*",
            parse_mode="Markdown",
            reply_markup=kb_admin_main(),
        )

    return ConversationHandler.END

# ==================== ADMIN HANDLERS ====================

async def admin_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("âŒ Access denied.")
        return ConversationHandler.END

    brp   = get_brp()
    sb    = get_signup_bonus()
    total = len(_data.get("users", {}))
    total_refs = sum(u.get("total_refers", 0) for u in _data.get("users", {}).values())
    total_brefs = sum(u.get("bot_refers", 0) for u in _data.get("users", {}).values())

    await update.message.reply_text(
        f"ðŸ‘‘ *Admin Panel*\n\n"
        f"ðŸ‘¥ *Total Users:* `{total}`\n"
        f"ðŸŽ¯ *OTP Refers:* `{total_refs}`\n"
        f"ðŸ¤ *Bot Refers:* `{total_brefs}`\n"
        f"ðŸ¤– *Bot Refer Reward:* `{brp} pts`\n"
        f"ðŸŽ *Signup Bonus:* `{sb} pts`\n\n"
        f"Choose option:",
        parse_mode="Markdown",
        reply_markup=kb_admin_main(),
    )
    return ADM_MAIN

async def adm_recv_add_uid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    txt = update.message.text.strip()
    if not txt.isdigit():
        await update.message.reply_text("âŒ Sirf Telegram ID (number) bhejo:"); return ADM_ADD_UID
    ctx.user_data["target_uid"] = int(txt)
    u = get_user(int(txt))
    await update.message.reply_text(
        f"User: `{txt}`\nCurrent: *{u['points']} pts*\n\nKitne add karne hain?",
        parse_mode="Markdown",
    )
    return ADM_ADD_AMT

async def adm_recv_add_amt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    txt = update.message.text.strip()
    if not txt.isdigit() or int(txt) <= 0:
        await update.message.reply_text("âŒ Valid number bhejo:"); return ADM_ADD_AMT
    amt   = int(txt)
    t_uid = ctx.user_data.get("target_uid")
    await add_points(t_uid, amt)
    u = get_user(t_uid)
    await update.message.reply_text(
        f"âœ… *Done!*\n`{t_uid}` ko *+{amt} pts* mile!\nNew balance: *{u['points']}*",
        parse_mode="Markdown", reply_markup=kb_admin_main(),
    )
    try:
        await ctx.bot.send_message(
            t_uid,
            f"ðŸŽ‰ *Points Mila!*\n\nâž• *+{amt} points* add hue!\nðŸ’° Total: *{u['points']}*",
            parse_mode="Markdown",
        )
    except: pass
    return ConversationHandler.END

async def adm_recv_rem_uid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    txt = update.message.text.strip()
    if not txt.isdigit():
        await update.message.reply_text("âŒ Sirf Telegram ID bhejo:"); return ADM_REM_UID
    ctx.user_data["target_uid"] = int(txt)
    u = get_user(int(txt))
    await update.message.reply_text(
        f"User: `{txt}`\nCurrent: *{u['points']} pts*\n\nKitne remove karne hain?",
        parse_mode="Markdown",
    )
    return ADM_REM_AMT

async def adm_recv_rem_amt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    txt = update.message.text.strip()
    if not txt.isdigit() or int(txt) <= 0:
        await update.message.reply_text("âŒ Valid number bhejo:"); return ADM_REM_AMT
    amt   = int(txt)
    t_uid = ctx.user_data.get("target_uid")
    await add_points(t_uid, -amt)
    u = get_user(t_uid)
    await update.message.reply_text(
        f"âœ… Done!\n`{t_uid}` se *-{amt} pts*!\nNew: *{u['points']}*",
        parse_mode="Markdown", reply_markup=kb_admin_main(),
    )
    return ConversationHandler.END

async def adm_recv_bot_pts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    txt = update.message.text.strip()
    if not txt.isdigit() or int(txt) <= 0:
        await update.message.reply_text("âŒ Valid number bhejo:"); return ADM_BOT_PTS_AMT
    async with _lock:
        _data["settings"]["bot_refer_points"] = int(txt)
    asyncio.create_task(save_data())
    await update.message.reply_text(
        f"âœ… *Bot Refer Reward = {txt} pts* set!", parse_mode="Markdown", reply_markup=kb_admin_main(),
    )
    return ConversationHandler.END

async def adm_recv_signup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    txt = update.message.text.strip()
    if not txt.isdigit() or int(txt) < 0:
        await update.message.reply_text("âŒ 0 ya usse bada number bhejo:"); return ADM_SIGNUP_AMT
    async with _lock:
        _data["settings"]["signup_bonus"] = int(txt)
    asyncio.create_task(save_data())
    msg = f"âœ… *Signup Bonus = {txt} pts* set!" if int(txt) > 0 else "âœ… *Signup Bonus band kar diya!*"
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb_admin_main())
    return ConversationHandler.END

async def adm_recv_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    msg   = update.message.text.strip()
    users = list(_data.get("users", {}).keys())
    sent = fail = 0
    status = await update.message.reply_text(f"ðŸ“£ Sending to {len(users)} users...")
    for u_id in users:
        try:
            await ctx.bot.send_message(int(u_id), f"ðŸ“£ *Admin Message:*\n\n{msg}", parse_mode="Markdown")
            sent += 1
            await asyncio.sleep(0.04)
        except: fail += 1
    await status.edit_text(f"âœ… Broadcast done!\nâœ… Sent: {sent}\nâŒ Failed: {fail}")
    return ConversationHandler.END

# ==================== MISC ====================
async def cancel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    clear_temp(ctx)
    await update.message.reply_text("âŒ Cancelled.\nðŸ  Main Menu:", reply_markup=get_menu_kb(uid))
    return ConversationHandler.END

async def unknown_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text("ðŸ‘‡ Button se karo:", reply_markup=get_menu_kb(uid))

async def btn_admin_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        await update.message.reply_text("âŒ Access denied.")
        return ConversationHandler.END
    brp   = get_brp()
    sb    = get_signup_bonus()
    total = len(_data.get("users", {}))
    total_refs = sum(u.get("total_refers", 0) for u in _data.get("users", {}).values())
    await update.message.reply_text(
        f"ðŸ‘‘ *Admin Panel*\n\n"
        f"ðŸ‘¥ Users: *{total}*\n"
        f"âœ… Total OTP Refers: *{total_refs}*\n"
        f"ðŸ¤– Bot Refer Reward: *{brp} pts*\n"
        f"ðŸŽ Signup Bonus: *{sb} pts*",
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

    print(f"ðŸš€ Bot ready! Admin: {ADMIN_ID}")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
