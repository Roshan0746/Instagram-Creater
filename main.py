import sys, logging, asyncio, re, json, os, time, random, string, uuid, html, sqlite3
from datetime import datetime, timedelta
import aiohttp
from yarl import URL
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, CallbackQueryHandler

# --- 1. SYSTEM PATCHES ---
try:
    import imghdr
except ImportError:
    import filetype
    class MockImghdr:
        @staticmethod
        def what(file, h=None):
            kind = filetype.guess(file)
            return kind.extension if kind else None
    sys.modules['imghdr'] = MockImghdr()

import pytz, apscheduler.util
def fixed_normalize(tz): return pytz.utc
apscheduler.util.astimezone = fixed_normalize

# --- 2. CONFIGURATION (RAILWAY READY) ---
# Reads variables from Railway Dashboard. Defaults provided for safety.
BOT_TOKEN = os.getenv("BOT_TOKEN") 
ADMIN_ID = int(os.getenv("ADMIN_ID", "0")) 

STATE_EMAIL, STATE_OTP = range(2)

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Mobile Safari/537.36'
]

# OPTIONAL: Add proxies here if Instagram blocks Railway IP
# Format: "http://user:pass@ip:port"
PROXIES = [] 

# --- 3. ACCESS DATABASE (TEMP PATH FIX) ---
# Uses /tmp/ folder to avoid permission errors on Railway
DB_PATH = '/tmp/access_control.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS whitelist (user_id INTEGER PRIMARY KEY, expiry TEXT)')
    try: c.execute("ALTER TABLE whitelist ADD COLUMN expiry TEXT")
    except sqlite3.OperationalError: pass 
    # Auto-add Admin if ID is valid
    if ADMIN_ID != 0:
        c.execute("INSERT OR IGNORE INTO whitelist (user_id, expiry) VALUES (?, ?)", (ADMIN_ID, "2099-01-01 00:00:00"))
    conn.commit()
    conn.close()

def get_user_access(uid):
    """Checks validity for UI display"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT expiry FROM whitelist WHERE user_id=?", (uid,))
    res = c.fetchone()
    conn.close()
    if not res or res[0] is None: return False, None
    try:
        expiry = datetime.strptime(res[0], "%Y-%m-%d %H:%M:%S")
        if datetime.now() > expiry: return False, "Expired"
        return True, expiry
    except: return False, None

def set_access_db(uid, expiry_str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO whitelist (user_id, expiry) VALUES (?, ?)", (uid, expiry_str))
    conn.commit()
    conn.close()

# --- 4. THE ENGINE ---
class InstaCreatorAsync:
    def __init__(self, email):
        self.email = email
        self.device_id = str(uuid.uuid4())
        self.password = f"{email.split('@')[0].capitalize()}{random.randint(10,99)}#@*"
        self.full_name = f"{random.choice(['Aarav', 'Vivaan'])} {random.choice(['Sharma', 'Patel'])}"
        self.ua = random.choice(USER_AGENTS)
        self.proxy = random.choice(PROXIES) if PROXIES else None
        self.session = None; self.username = ""; self.signup_code = ""

    async def init_session(self):
        connector = aiohttp.TCPConnector(ssl=False)
        self.session = aiohttp.ClientSession(connector=connector)
        self.session.headers.update({
            'User-Agent': self.ua, 'Accept': '*/*', 'Accept-Language': 'en-US,en;q=0.9',
            'X-ASBD-ID': '129477', 'X-IG-App-ID': '936619743392459', 'X-Requested-With': 'XMLHttpRequest'
        })

    async def warmup(self):
        try:
            await self.session.get('https://www.instagram.com/', proxy=self.proxy)
            await asyncio.sleep(random.uniform(5, 8))
            async with self.session.get('https://www.instagram.com/accounts/emailsignup/', proxy=self.proxy) as r:
                cookies = self.session.cookie_jar.filter_cookies(URL('https://www.instagram.com'))
                if 'csrftoken' in cookies: self.session.headers.update({'X-CSRFToken': cookies['csrftoken'].value})
            return True, "Warmup Done"
        except Exception as e: return False, str(e)

    async def check_availability(self):
        url = 'https://www.instagram.com/api/v1/web/accounts/web_create_ajax/attempt/'
        payload = {'enc_password': f"#PWD_INSTAGRAM_BROWSER:0:{int(time.time())}:{self.password}", 'email': self.email, 'first_name': self.full_name, 'username': '', 'client_id': self.device_id, 'optIntoOneTap': 'false'}
        async with self.session.post(url, data=payload, proxy=self.proxy) as res:
            data = await res.json()
            if 'username_suggestions' in data:
                self.username = data['username_suggestions'][0]; return True, self.username
            return False, "Rejected"

    async def send_otp(self):
        try:
            age_url = 'https://www.instagram.com/api/v1/web/consent/check_age_eligibility/'
            await self.session.post(age_url, data={'day': '10', 'month': '5', 'year': '1998'}, proxy=self.proxy)
            await asyncio.sleep(2)
            otp_url = 'https://www.instagram.com/api/v1/accounts/send_verify_email/'
            async with self.session.post(otp_url, data={'device_id': self.device_id, 'email': self.email}, proxy=self.proxy) as res:
                text = await res.text(); return '"email_sent":true' in text, text
        except Exception as e: return False, str(e)

    async def verify_otp_and_create(self, otp):
        try:
            v_url = 'https://www.instagram.com/api/v1/accounts/check_confirmation_code/'
            async with self.session.post(v_url, data={'code': otp, 'device_id': self.device_id, 'email': self.email}, proxy=self.proxy) as res:
                data = await res.json()
                if 'signup_code' in data: self.signup_code = data['signup_code']
                else: return False, "Invalid OTP"
            await asyncio.sleep(random.uniform(3, 5))
            c_url = 'https://www.instagram.com/api/v1/web/accounts/web_create_ajax/'
            payload = {'enc_password': f"#PWD_INSTAGRAM_BROWSER:0:{int(time.time())}:{self.password}", 'email': self.email, 'first_name': self.full_name, 'username': self.username, 'client_id': self.device_id, 'force_sign_up_code': self.signup_code, 'day': '10', 'month': '5', 'year': '1998'}
            async with self.session.post(c_url, data=payload, proxy=self.proxy) as res:
                text = await res.text()
                if '"account_created":true' not in text: return False, text[:100]
            await asyncio.sleep(2); await self.session.get('https://www.instagram.com/accounts/edit/', proxy=self.proxy)
            cookies_list = [f"{c.key}={c.value}" for c in self.session.cookie_jar]
            return True, "; ".join(cookies_list)
        except Exception as e: return False, str(e)

# --- 5. UI HANDLERS ---
async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    user = u.effective_user
    name = user.first_name 
    is_ok, expiry = get_user_access(user.id)
    
    if is_ok:
        t_left = "Unlimited" if (expiry and expiry.year > 2090) else str(expiry - datetime.now()).split('.')[0]
        msg = (f"✨ <b>Premium Session Active</b>\n"
               f"──────────────────\n"
               f"👤 <b>Member:</b> <code>{name}</code>\n"
               f"🛡️ <b>Status:</b> <code>Authorized</code>\n"
               f"⏳ <b>Validity:</b> <code>{t_left}</code>\n"
               f"──────────────────\n"
               f"Ready to generate new accounts.\n"
               f"<i>Tap the button below to start.</i>")
        kb = [['📸 Create Account']]
        if user.id == ADMIN_ID: kb.append(['⚙️ Admin Panel'])
        
        sent_msg = await u.message.reply_text(msg, reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True), parse_mode='HTML')
        
        c.job_queue.run_repeating(update_timer_job, interval=10, first=10, chat_id=u.effective_chat.id, 
                                  data={"msg_id": sent_msg.message_id, "uid": user.id, "name": name})
    else:
        kb = [[InlineKeyboardButton("📩 Request Exclusive Access", callback_data=f"req_{user.id}")]]
        text = (f"🙏 <b>Greetings, {name}!</b>\n\n"
                f"To maintain service stability, access to this tool is currently limited to <b>Authorized Members</b> only.\n\n"
                f"💡 <i>Please tap below to request your access window.</i>")
        await u.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

# Timer Background Job
async def update_timer_job(c: ContextTypes.DEFAULT_TYPE):
    job = c.job
    is_ok, expiry = get_user_access(job.data["uid"])
    if not is_ok:
        await c.bot.edit_message_text("⌛ <b>Session Expired.</b>\nPlease request a new access window.", 
                                      chat_id=job.chat_id, message_id=job.data["msg_id"], parse_mode='HTML')
        job.schedule_removal(); return

    t_left = "Unlimited" if expiry.year > 2090 else str(expiry - datetime.now()).split('.')[0]
    new_msg = (f"✨ <b>Premium Session Active</b>\n"
               f"──────────────────\n"
               f"👤 <b>Member:</b> <code>{job.data['name']}</code>\n"
               f"⏳ <b>Validity:</b> <code>{t_left}</code>\n"
               f"──────────────────\n"
               f"Ready to generate new accounts.")
    try: await c.bot.edit_message_text(new_msg, chat_id=job.chat_id, message_id=job.data["msg_id"], parse_mode='HTML')
    except: pass

# --- 6. ADMIN & CALLBACKS ---
async def admin_dashboard(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID: return
    conn = sqlite3.connect(DB_PATH); c_db = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c_db.execute("SELECT user_id, expiry FROM whitelist WHERE expiry > ? AND user_id != ?", (now, ADMIN_ID))
    users = c_db.fetchall(); conn.close()
    if not users: return await u.message.reply_text("📊 <b>Dashboard Active</b>\n\nNo active users currently.", parse_mode='HTML')
    msg = "📊 <b>Active Users Board</b>\n──────────────────\n"
    for uid, expiry_str in users:
        exp = datetime.strptime(expiry_str, "%Y-%m-%d %H:%M:%S")
        msg += f"👤 <code>{uid}</code> | ⏳ <code>{str(exp - datetime.now()).split('.')[0]}</code>\n"
    await u.message.reply_text(msg + "──────────────────", parse_mode='HTML')

async def admin_until(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID: return
    try:
        uid, h_m = int(c.args[0]), c.args[1]
        set_access_db(uid, f"{datetime.now().strftime('%Y-%m-%d')} {h_m}:00")
        await u.message.reply_text(f"✅ Access set for {uid} until {h_m}")
        await c.bot.send_message(uid, f"✨ Access Granted until <code>{h_m}</code>", parse_mode='HTML')
    except: await u.message.reply_text("❌ Format: /until [ID] [HH:MM]")

async def handle_callbacks(u: Update, c: ContextTypes.DEFAULT_TYPE):
    query = u.callback_query; data = query.data
    if data.startswith("req_"):
        uid = data.split("_")[1]
        kb = [[InlineKeyboardButton("⏱️ +10m", callback_data=f"add_{uid}_10"), InlineKeyboardButton("⏱️ +20m", callback_data=f"add_{uid}_20")],
              [InlineKeyboardButton("❌ Deny", callback_data=f"deny_{uid}")]]
        await c.bot.send_message(ADMIN_ID, f"🔔 Request: <code>{uid}</code>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        await query.answer("Request sent!"); await query.edit_message_text("⏳ Waiting for Admin...")
    elif data.startswith("add_"):
        _, uid, mins = data.split("_"); exp = (datetime.now() + timedelta(minutes=int(mins))).strftime("%Y-%m-%d %H:%M:%S")
        set_access_db(int(uid), exp); await query.edit_message_text(f"✅ Approved {uid}"); await c.bot.send_message(int(uid), f"✨ Access Granted for {mins}m!")

# --- 7. CORE ENGINE FLOW ---
async def start_create(u, c):
    is_ok, _ = get_user_access(u.effective_user.id)
    if not is_ok: await u.message.reply_text("❌ <b>Access Expired!</b>", parse_mode='HTML'); return ConversationHandler.END
    await u.message.reply_text("📧 <b>Input Required</b>\n──────────────────\nPlease enter the <b>Email Address</b>:", parse_mode='HTML', reply_markup=ReplyKeyboardRemove()); return STATE_EMAIL

async def process_email(u, c):
    email = u.message.text.strip(); status_msg = await u.message.reply_text("⚙️ <b>Initializing Session...</b>", parse_mode='HTML')
    creator = InstaCreatorAsync(email); await creator.init_session(); await status_msg.edit_text("📡 <b>Warming up engines...</b>", parse_mode='HTML')
    if (await creator.warmup())[0]:
        await status_msg.edit_text("🔎 <b>Searching Username...</b>", parse_mode='HTML'); await asyncio.sleep(1.2); avail, _ = await creator.check_availability()
        if avail:
            await status_msg.edit_text(f"👤 <b>Identity:</b> <code>@{creator.username}</code>", parse_mode='HTML'); await asyncio.sleep(1.2); sent, _ = await creator.send_otp()
            if sent:
                c.user_data['creator'] = creator
                await status_msg.edit_text(f"✅ <b>OTP Sent Successfully!</b>\n──────────────────\n📧 <b>Email:</b> <code>{email}</code>\n🆔 <b>User:</b> <code>{creator.username}</code>\n\n👇 <b>Enter the 6-digit code below:</b>", parse_mode='HTML'); return STATE_OTP
    await status_msg.edit_text("❌ <b>Process Failed.</b>"); return ConversationHandler.END

async def process_otp(u, c):
    otp = u.message.text.strip(); creator = c.user_data.get('creator'); status_msg = await u.message.reply_text("🔐 <b>Verifying...</b>", parse_mode='HTML')
    for step in ["🎂 Setting Birthdate...", "👤 Identity Confirmed...", "🔑 Applying Password...", "🧬 Finalizing..."]:
        await asyncio.sleep(1.2); await status_msg.edit_text(step, parse_mode='HTML')
    success, result = await creator.verify_otp_and_create(otp)
    if success:
        await status_msg.edit_text(f"🎉 <b>ACCOUNT CREATED</b>\n──────────────────\n👤 <b>User:</b> <code>{creator.username}</code>\n🔑 <b>Pass:</b> <code>{creator.password}</code>\n──────────────────\n🍪 <b>Cookies:</b>\n<pre>{html.escape(result)}</pre>", parse_mode='HTML')
        await u.message.reply_text("✨ Next account?", reply_markup=ReplyKeyboardMarkup([['📸 Create Account']], resize_keyboard=True), parse_mode='HTML')
    else: await status_msg.edit_text(f"❌ Failed: {html.escape(result[:50])}"); return ConversationHandler.END

def main():
    if not BOT_TOKEN:
        print("❌ Error: BOT_TOKEN variable not found on Railway.")
        return
    init_db(); app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler('start', start)); app.add_handler(CommandHandler('until', admin_until))
    app.add_handler(CallbackQueryHandler(handle_callbacks)); app.add_handler(MessageHandler(filters.Regex('^⚙️ Admin Panel$'), admin_dashboard))
    app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex('^📸 Create Account$'), start_create)],
        states={STATE_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_email)], STATE_OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_otp)]}, fallbacks=[CommandHandler('start', start)]))
    print("🤖 Bot Started on Railway!"); app.run_polling()

if __name__ == "__main__": main()
