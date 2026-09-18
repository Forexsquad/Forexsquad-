import os
import sqlite3
import logging
import requests

from datetime import datetime, timedelta
from flask import Flask, request, jsonify


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


# ============================================================
# FLASK APP
# ============================================================

app = Flask(__name__)


# ============================================================
# CONFIGURATION
# ============================================================

HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "5000"))

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

TELEGRAM_API_URL = (
    f"https://api.telegram.org/bot{BOT_TOKEN}"
    if BOT_TOKEN
    else ""
)

ADMIN_USERNAME = os.getenv(
    "ADMIN_USERNAME",
    "@Almahmud09"
)

ADMIN_TELEGRAM_ID = int(
    os.getenv(
        "ADMIN_TELEGRAM_ID",
        "5864700037"
    )
)

DB_FILE = os.getenv(
    "DB_FILE",
    "forexsquad.db"
)

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "Hemal@09")


# ============================================================
# DATABASE & MIGRATION
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

    cursor.execute("PRAGMA table_info(members)")
    existing_columns = [col["name"] for col in cursor.fetchall()]

    new_columns = {
        "account_balance": "TEXT DEFAULT '0.0'",
        "daily_max_loss": "TEXT DEFAULT '0.0'",
        "daily_profit_target": "TEXT DEFAULT '0.0'",
        "broker": "TEXT DEFAULT 'N/A'",
        "platform": "TEXT DEFAULT 'N/A'",
        "onboarding_step": "TEXT DEFAULT NULL"
    }

    for col_name, col_type in new_columns.items():
        if col_name not in existing_columns:
            try:
                cursor.execute(f"ALTER TABLE members ADD COLUMN {col_name} {col_type}")
                logger.info(f"Successfully added column '{col_name}' to members table.")
            except Exception as e:
                logger.error(f"Error adding column {col_name}: {e}")

    conn.commit()
    conn.close()
    logger.info("Database initialized and migrated successfully.")


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
        ON CONFLICT(telegram_id)
        DO UPDATE SET
            first_name = excluded.first_name,
            last_name = excluded.last_name,
            username = excluded.username
    """, (telegram_id, first_name, last_name, username))
    conn.commit()
    conn.close()


def update_member_setting(telegram_id, field, value):
    conn = get_db()
    cursor = conn.cursor()
    query = f"UPDATE members SET {field} = ? WHERE telegram_id = ?"
    cursor.execute(query, (str(value), telegram_id))
    conn.commit()
    conn.close()


# ============================================================
# SUBSCRIPTION FUNCTIONS
# ============================================================

def activate_member(telegram_id, days, plan="Premium"):
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
    cursor.execute("UPDATE members SET is_active = 0 WHERE telegram_id = ?", (telegram_id,))
    success = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return success


def extend_member(telegram_id, days):
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
        UPDATE members SET expiry_date = ?, is_active = 1 WHERE telegram_id = ?
    """, (new_expiry_str, telegram_id))

    conn.commit()
    conn.close()
    return True, new_expiry_str


def is_admin(telegram_id):
    return telegram_id == ADMIN_TELEGRAM_ID


# ============================================================
# TELEGRAM API
# ============================================================

def telegram_api(method, payload):
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not configured.")
        return None

    url = f"{TELEGRAM_API_URL}/{method}"
    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            logger.error("Telegram API returned error: %s", result)
        return result
    except Exception as e:
        logger.error("Telegram API error (%s): %s", method, e)
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


# ============================================================
# TELEGRAM KEYBOARD
# ============================================================

def subscription_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "📊 Check Status",
                    "callback_data": "subscription_status"
                }
            ],
            [
                {
                    "text": "💬 Contact Owner",
                    "url": f"https://t.me/{ADMIN_USERNAME.lstrip('@')}"
                }
            ]
        ]
    }


# ============================================================
# MEMBER STATUS & SETTINGS TEXT
# ============================================================

def subscription_status_text(member):
    first_name = member["first_name"] or ""
    last_name = member["last_name"] or ""
    
    balance = member["account_balance"] if member["account_balance"] else "0.0"
    max_loss = member["daily_max_loss"] if member["daily_max_loss"] else "0.0"
    target = member["daily_profit_target"] if member["daily_profit_target"] else "0.0"
    broker = member["broker"] if member["broker"] else "N/A"
    platform = member["platform"] if member["platform"] else "N/A"

    return (
        "👤 <b>Account Info:</b>\n"
        f"ID: <code>{member['telegram_id']}</code>\n"
        f"Name: {first_name} {last_name}\n\n"
        f"📦 <b>Plan:</b> {member['plan']}\n"
        f"⏳ <b>Expires:</b> {member['expiry_date'] or 'N/A'}\n"
        f"🟢 <b>Status:</b> {'Active' if member['is_active'] else 'Inactive'}\n\n"
        "⚙️ <b>Risk & Trading Settings:</b>\n"
        f"💰 Account Balance: ${balance}\n"
        f"🛑 Daily Max Loss: ${max_loss}\n"
        f"🎯 Daily Profit Target: ${target}\n"
        f"🏦 Broker: {broker}\n"
        f"📱 Platform: {platform}\n\n"
        f"👑 <b>Owner / Admin:</b> {ADMIN_USERNAME}"
    )


# ============================================================
# ONBOARDING & SETTINGS FLOW
# ============================================================

def start_onboarding(telegram_id, first_name):
    update_member_setting(telegram_id, "onboarding_step", "WAITING_BALANCE")
    send_message(
        telegram_id,
        f"Welcome to <b>ForexSquad Bot</b>, {first_name}!\n\n"
        "Let's set up your trading configuration.\n"
        "💰 Please enter your current <b>Account Balance</b> (e.g., 1000):\n\n"
        "<i>You can type /cancel anytime to stop setup.</i>"
    )


def handle_onboarding_step(telegram_id, text, member):
    step = member["onboarding_step"]

    if step == "WAITING_BALANCE":
        update_member_setting(telegram_id, "account_balance", text.strip())
        update_member_setting(telegram_id, "onboarding_step", "WAITING_MAX_LOSS")
        send_message(telegram_id, "🛑 Enter your <b>Daily Maximum Loss</b> limit (e.g., 50):")

    elif step == "WAITING_MAX_LOSS":
        update_member_setting(telegram_id, "daily_max_loss", text.strip())
        update_member_setting(telegram_id, "onboarding_step", "WAITING_TARGET")
        send_message(telegram_id, "🎯 Enter your <b>Daily Profit Target</b> (e.g., 100):")

    elif step == "WAITING_TARGET":
        update_member_setting(telegram_id, "daily_profit_target", text.strip())
        update_member_setting(telegram_id, "onboarding_step", "WAITING_BROKER")
        send_message(telegram_id, "🏦 Enter your <b>Broker name</b> (e.g., Exness, ICMarkets):")

    elif step == "WAITING_BROKER":
        update_member_setting(telegram_id, "broker", text.strip())
        update_member_setting(telegram_id, "onboarding_step", "WAITING_PLATFORM")
        send_message(telegram_id, "📱 Enter your <b>Trading Platform</b> (e.g., MetaTrader 5, TradingView):")

    elif step == "WAITING_PLATFORM":
        update_member_setting(telegram_id, "platform", text.strip())
        update_member_setting(telegram_id, "onboarding_step", None)
        
        updated_member = get_member(telegram_id)
        send_message(
            telegram_id,
            "✅ <b>All settings saved successfully!</b>\n\n" + subscription_status_text(updated_member),
            subscription_keyboard()
        )


# ============================================================
# COMMAND HANDLERS (/start, /settings, /cancel, /status)
# ============================================================

def handle_start(message):
    user = message.get("from", {})
    telegram_id = int(user.get("id", 0))
    first_name = user.get("first_name", "")
    last_name = user.get("last_name", "")
    username = user.get("username", "")

    if not telegram_id:
        return

    upsert_member(telegram_id, first_name, last_name, username)
    start_onboarding(telegram_id, first_name)


def handle_settings(message):
    user = message.get("from", {})
    telegram_id = int(user.get("id", 0))
    if not telegram_id:
        return

    member = get_member(telegram_id)
    if not member:
        send_message(telegram_id, "Please use /start to register first.")
        return

    update_member_setting(telegram_id, "onboarding_step", "WAITING_BALANCE")
    send_message(
        telegram_id,
        "⚙️ <b>Modify Settings</b>\n\n"
        "💰 Please enter your new <b>Account Balance</b>:"
    )


def handle_cancel(message):
    user = message.get("from", {})
    telegram_id = int(user.get("id", 0))
    if not telegram_id:
        return

    member = get_member(telegram_id)
    if member and member["onboarding_step"]:
        update_member_setting(telegram_id, "onboarding_step", None)
        send_message(telegram_id, "❌ Setup/Settings update cancelled.", subscription_keyboard())
    else:
        send_message(telegram_id, "No active process to cancel.")


def handle_status(message):
    user = message.get("from", {})
    telegram_id = int(user.get("id", 0))
    if not telegram_id:
        return

    member = get_member(telegram_id)
    if not member:
        send_message(telegram_id, "Please use /start to register first.")
        return

    text = subscription_status_text(member)
    send_message(telegram_id, text, subscription_keyboard())


# ============================================================
# ADMIN COMMANDS (/activate, /deactivate, /extend)
# ============================================================

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
        return

    send_message(telegram_id, f"Successfully activated member <code>{target_id}</code>\nDays: {days}\nPlan: {plan}\nExpires: {result}")
    send_message(target_id, f"🎉 <b>Your subscription has been activated!</b>\n\nPlan: <b>{plan}</b>\nValid for: <b>{days} day(s)</b>", subscription_keyboard())


def handle_admin_deactivate(telegram_id, args):
    if not is_admin(telegram_id):
        return

    if len(args) < 1:
        send_message(telegram_id, "Usage:\n/deactivate <telegram_id>")
        return

    try:
        target_id = int(args[0])
    except ValueError:
        send_message(telegram_id, "Invalid Telegram ID.")
        return

    success = deactivate_member(target_id)
    if success:
        send_message(telegram_id, f"Member <code>{target_id}</code> has been deactivated.")
        send_message(target_id, "⚠️ Your subscription has been deactivated by an administrator.")
    else:
        send_message(telegram_id, f"Member <code>{target_id}</code> was not found.")


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
        return

    send_message(telegram_id, f"Successfully extended member <code>{target_id}</code> by {days} day(s).\nNew Expiry: {result}")
    send_message(target_id, f"⏱️ <b>Your subscription has been extended!</b>\n\nAdded: <b>{days} day(s)</b>\nNew Expiry: <b>{result}</b>", subscription_keyboard())


# ============================================================
# TELEGRAM WEBHOOK ROUTE
# ============================================================

@app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"ok": True}), 200

    if "callback_query" in data:
        callback = data["callback_query"]
        user = callback.get("from", {})
        telegram_id = int(user.get("id", 0))
        callback_data = callback.get("data", "")
        callback_id = callback.get("id")

        if callback_data == "subscription_status":
            member = get_member(telegram_id)
            if member:
                telegram_api("answerCallbackQuery", {"callback_query_id": callback_id})
                text = subscription_status_text(member)
                send_message(telegram_id, text, subscription_keyboard())
            else:
                telegram_api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Please use /start first."})
        return jsonify({"ok": True}), 200

    message = data.get("message") or data.get("edited_message")
    if not message:
        return jsonify({"ok": True}), 200

    text = message.get("text", "").strip()
    user = message.get("from", {})
    telegram_id = int(user.get("id", 0))

    if not telegram_id:
        return jsonify({"ok": True}), 200

    member = get_member(telegram_id)

    if member and member["onboarding_step"] and not text.startswith("/"):
        handle_onboarding_step(telegram_id, text, member)
        return jsonify({"ok": True}), 200

    if not text:
        return jsonify({"ok": True}), 200

    if text.startswith("/"):
        parts = text.split()
        command = parts[0].split("@")[0].lower()
        args = parts[1:]

        logger.info("Telegram command received: %s", command)

        if command == "/start":
            handle_start(message)
        elif command == "/settings":
            handle_settings(message)
        elif command == "/cancel":
            handle_cancel(message)
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
# TRADINGVIEW WEBHOOK FOR ALM04 (WITH BROADCAST)
# ============================================================

@app.route("/webhook/alm04", methods=["POST"])
def tradingview_alm04_webhook():
    secret = request.args.get("secret")
    if secret != WEBHOOK_SECRET:
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json(silent=True)
    if not data:
        raw_text = request.get_data(as_text=True)
        signal_text = raw_text if raw_text else "TradingView Signal Triggered!"
    else:
        signal_text = data.get("text") or str(data)

    logger.info("TradingView signal received: %s", signal_text)

    # 1. Send to Admin first
    send_message(
        ADMIN_TELEGRAM_ID,
        f"🚨 <b>TradingView Signal (Alm04):</b>\n\n{signal_text}"
    )

    # 2. Broadcast to all active members
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id FROM members WHERE is_active = 1")
    active_members = cursor.fetchall()
    conn.close()

    broadcast_text = f"🚨 <b>VIP Forex Signal:</b>\n\n{signal_text}"

    for member in active_members:
        member_id = member["telegram_id"]
        if member_id == ADMIN_TELEGRAM_ID:
            continue  # Skip admin to avoid duplicate notification if already sent above
        try:
            send_message(member_id, broadcast_text)
        except Exception as e:
            logger.error(f"Failed to send signal to {member_id}: {e}")

    return jsonify({"ok": True}), 200


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "online",
        "service": "ForexSquad Bot",
        "telegram_webhook": "/telegram/webhook"
    }), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"}), 200


# ============================================================
# STARTUP
# ============================================================

init_db()

if __name__ == "__main__":
    logger.info("ForexSquad Bot backend starting on %s:%s", HOST, PORT)
    app.run(host=HOST, port=PORT)
