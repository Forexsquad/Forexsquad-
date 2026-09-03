import os
import sqlite3
import logging
import requests
from flask import Flask, request, jsonify

# ============================================================
# CONFIGURATION & SETUP
# ============================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Telegram Bot Token provided
BOT_TOKEN = "8879920230:AAHXPrHiOfEBuXwaFH3L5OCK0yMLq2UgApE"
TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

HOST = "0.0.0.0"
PORT = 5000

# Owner / Admin details
ADMIN_USERNAME = "@Almahmud09"
ADMIN_TELEGRAM_ID = 5864700037

DB_FILE = "forexsquad.db"

# ============================================================
# DATABASE FUNCTIONS
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS members (
            telegram_id INTEGER PRIMARY KEY,
            first_name TEXT,
            last_name TEXT,
            username TEXT,
            plan TEXT DEFAULT 'Free',
            expiry_date TEXT DEFAULT NULL,
            is_active INTEGER DEFAULT 1
        )
    """)
    conn.commit()
    conn.close()

def get_member(telegram_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM members WHERE telegram_id = ?", (telegram_id,))
    row = cursor.fetchone()
    conn.close()
    return row

def upsert_member(telegram_id, first_name, last_name, username):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO members (telegram_id, first_name, last_name, username, is_active)
        VALUES (?, ?, ?, ?, 1)
        ON CONFLICT(telegram_id) DO UPDATE SET
            first_name = excluded.first_name,
            last_name = excluded.last_name,
            username = excluded.username
    """, (telegram_id, first_name, last_name, username))
    conn.commit()
    conn.close()

def activate_member(telegram_id, days, plan="Premium"):
    from datetime import datetime, timedelta
    conn = get_db()
    cursor = conn.cursor()
    
    expiry = datetime.utcnow() + timedelta(days=days)
    expiry_str = expiry.strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        UPDATE members 
        SET plan = ?, expiry_date = ?, is_active = 1 
        WHERE telegram_id = ?
    """, (plan, expiry_str, telegram_id))
    
    if cursor.rowcount == 0:
        conn.close()
        return False, "Member not found in database."
        
    conn.commit()
    conn.close()
    return True, expiry_str

def deactivate_member(telegram_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE members 
        SET is_active = 0 
        WHERE telegram_id = ?
    """, (telegram_id,))
    success = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return success

def extend_member(telegram_id, days):
    from datetime import datetime, timedelta
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT expiry_date FROM members WHERE telegram_id = ?", (telegram_id,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        return False, "Member not found."
        
    current_expiry = row["expiry_date"]
    base_date = datetime.utcnow()
    
    if current_expiry:
        try:
            parsed_expiry = datetime.strptime(current_expiry, "%Y-%m-%d %H:%M:%S")
            if parsed_expiry > base_date:
                base_date = parsed_expiry
        except ValueError:
            pass
            
    new_expiry = base_date + timedelta(days=days)
    new_expiry_str = new_expiry.strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute("""
        UPDATE members 
        SET expiry_date = ?, is_active = 1 
        WHERE telegram_id = ?
    """, (new_expiry_str, telegram_id))
    
    conn.commit()
    conn.close()
    return True, new_expiry_str

def is_admin(telegram_id):
    return telegram_id == ADMIN_TELEGRAM_ID

# ============================================================
# TELEGRAM API HELPERS
# ============================================================

def telegram_api(method, payload):
    url = f"{TELEGRAM_API_URL}/{method}"
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.json()
    except Exception as e:
        logger.error(f"Telegram API error ({method}): {e}")
        return None

def send_message(chat_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return telegram_api("sendMessage", payload)

def subscription_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "📊 Check Status", "callback_data": "subscription_status"}],
            [{"text": "💬 Contact Owner", "url": f"https://t.me/{ADMIN_USERNAME.lstrip('@')}"}]
        ]
    }

def subscription_status_text(member):
    return (
        f"👤 <b>Account Info:</b>\n"
        f"ID: <code>{member['telegram_id']}</code>\n"
        f"Name: {member['first_name']} {member['last_name'] or ''}\n\n"
        f"📦 <b>Plan:</b> {member['plan']}\n"
        f"⏳ <b>Expires:</b> {member['expiry_date'] or 'N/A'}\n"
        f"🟢 <b>Status:</b> {'Active' if member['is_active'] else 'Inactive'}\n\n"
        f"👑 <b>Owner / Admin:</b> {ADMIN_USERNAME}"
    )

# ============================================================
# COMMAND HANDLERS
# ============================================================

def handle_start(message):
    user = message.get("from", {})
    telegram_id = int(user.get("id", 0))
    first_name = user.get("first_name", "")
    last_name = user.get("last_name", "")
    username = user.get("username", "")

    upsert_member(telegram_id, first_name, last_name, username)

    welcome_text = (
        f"Welcome to <b>ForexSquad Bot</b>, {first_name}!\n\n"
        "🧠 <b>Explanations and answers</b>\n"
        "Free AI → DeepSeek & ChatGPT\n\n"
        "🖼 <b>Visualize your ideas</b>\n"
        "Make Image → NanoBanana\n\n"
        f"For any support or upgrades, contact the owner: {ADMIN_USERNAME}"
    )
    send_message(telegram_id, welcome_text, subscription_keyboard())

def handle_status(message):
    user = message.get("from", {})
    telegram_id = int(user.get("id", 0))
    
    member = get_member(telegram_id)
    if not member:
        send_message(telegram_id, "Please use /start to register first.")
        return
        
    text = subscription_status_text(member)
    send_message(telegram_id, text, subscription_keyboard())

def handle_admin_activate(telegram_id, args):
    if not is_admin(telegram_id):
        return

    if len(args) < 2:
        send_message(telegram_id, "Usage:\n/activate <telegram_id> <days> [plan]")
        return

    try:
        target_id = int(args[0])
        days = int(args[1])
        plan = args[2] if len(args) > 2 else "Premium"
        if days <= 0:
            raise ValueError
    except ValueError:
        send_message(telegram_id, "Invalid parameters.")
        return

    success, result = activate_member(target_id, days, plan)

    if not success:
        send_message(telegram_id, f"Failed to activate member: {result}")
    else:
        send_message(
            telegram_id,
            f"Successfully activated member <code>{target_id}</code> for {days} day(s).\nPlan: {plan}\nExpires: {result}"
        )
        send_message(
            target_id,
            f"🎉 <b>Your subscription has been activated!</b>\nPlan: <b>{plan}</b>\nValid for: <b>{days} day(s)</b>",
            subscription_keyboard()
        )

def handle_admin_deactivate(telegram_id, args):
    if not is_admin(telegram_id):
        return

    if len(args) < 1:
        send_message(telegram_id, "Usage:\n/deactivate <telegram_id>")
        return

    try:
        target_id = int(args[0])
    except ValueError:
        send_message(telegram_id, "Invalid telegram ID.")
        return

    success = deactivate_member(target_id)

    if success:
        send_message(telegram_id, f"Member <code>{target_id}</code> has been deactivated.")
        send_message(target_id, "⚠️ Your subscription has been deactivated by an administrator.")
    else:
        send_message(telegram_id, f"Failed to deactivate member <code>{target_id}</code> (not found or error).")

def handle_admin_extend(telegram_id, args):
    if not is_admin(telegram_id):
        return

    if len(args) < 2:
        send_message(telegram_id, "Usage:\n/extend <telegram_id> <days>")
        return

    try:
        target_id = int(args[0])
        days = int(args[1])
        if days <= 0:
            raise ValueError
    except ValueError:
        send_message(telegram_id, "Invalid parameters.")
        return

    success, result = extend_member(target_id, days)

    if not success:
        send_message(telegram_id, f"Failed to extend subscription: {result}")
    else:
        send_message(telegram_id, f"Successfully extended member <code>{target_id}</code> by {days} day(s).\nNew Expiry: {result}")
        send_message(
            target_id,
            f"⏱️ <b>Your subscription has been extended by {days} day(s)!</b>\nNew Expiry: <b>{result}</b>",
            subscription_keyboard()
        )

# ============================================================
# TELEGRAM WEBHOOK / UPDATE DISPATCHER
# ============================================================

@app.route("/webhook/telegram", methods=["POST"])
def telegram_webhook():
    data = request.get_json(silent=True)

    if not data:
        return jsonify({"ok": True}), 200

    if "callback_query" in data:
        cq = data["callback_query"]
        user = cq.get("from", {})
        telegram_id = int(user.get("id", 0))
        data_str = cq.get("data", "")

        if data_str == "subscription_status":
            member = get_member(telegram_id)
            if member:
                text = subscription_status_text(member)
                telegram_api("answerCallbackQuery", {"callback_query_id": cq.get("id")})
                send_message(telegram_id, text, subscription_keyboard())
            else:
                telegram_api("answerCallbackQuery", {"callback_query_id": cq.get("id"), "text": "Please use /start first."})

        return jsonify({"ok": True}), 200

    message = data.get("message") or data.get("edited_message")
    if not message:
        return jsonify({"ok": True}), 200

    text = message.get("text", "").strip()
    user = message.get("from", {})
    telegram_id = int(user.get("id", 0))

    if not telegram_id or not text:
        return jsonify({"ok": True}), 200

    if text.startswith("/"):
        parts = text.split()
        command = parts[0].split("@")[0].lower()
        args = parts[1:]

        if command == "/start":
            handle_start(message)
        elif command == "/status":
            handle_status(message)
        elif command == "/activate":
            handle_admin_activate(telegram_id, args)
        elif command == "/deactivate":
            handle_admin_deactivate(telegram_id, args)
        elif command == "/extend":
            handle_admin_extend(telegram_id, args)

    return jsonify({"ok": True}), 200

# ============================================================
# APP INITIALIZATION
# ============================================================

if __name__ == "__main__":
    init_db()
    logger.info("ForexSquad Bot backend starting on %s:%s", HOST, PORT)
    app.run(host=HOST, port=PORT)
