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


# ============================================================
# DATABASE
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

    logger.info("Database initialized successfully.")


def get_member(telegram_id):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM members WHERE telegram_id = ?",
        (telegram_id,)
    )

    row = cursor.fetchone()

    conn.close()

    return row


def upsert_member(
    telegram_id,
    first_name,
    last_name,
    username
):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO members (
            telegram_id,
            first_name,
            last_name,
            username,
            is_active
        )
        VALUES (?, ?, ?, ?, 1)

        ON CONFLICT(telegram_id)
        DO UPDATE SET
            first_name = excluded.first_name,
            last_name = excluded.last_name,
            username = excluded.username
    """, (
        telegram_id,
        first_name,
        last_name,
        username
    ))

    conn.commit()
    conn.close()


# ============================================================
# SUBSCRIPTION FUNCTIONS
# ============================================================

def activate_member(
    telegram_id,
    days,
    plan="Premium"
):
    conn = get_db()
    cursor = conn.cursor()

    expiry = datetime.utcnow() + timedelta(days=days)

    expiry_str = expiry.strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    cursor.execute("""
        UPDATE members
        SET
            plan = ?,
            expiry_date = ?,
            is_active = 1
        WHERE telegram_id = ?
    """, (
        plan,
        expiry_str,
        telegram_id
    ))

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
    """, (
        telegram_id,
    ))

    success = cursor.rowcount > 0

    conn.commit()
    conn.close()

    return success


def extend_member(
    telegram_id,
    days
):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT expiry_date
        FROM members
        WHERE telegram_id = ?
    """, (
        telegram_id,
    ))

    row = cursor.fetchone()

    if not row:
        conn.close()
        return False, "Member not found."

    current_expiry = row["expiry_date"]

    base_date = datetime.utcnow()

    if current_expiry:
        try:
            parsed_expiry = datetime.strptime(
                current_expiry,
                "%Y-%m-%d %H:%M:%S"
            )

            if parsed_expiry > base_date:
                base_date = parsed_expiry

        except ValueError:
            pass

    new_expiry = base_date + timedelta(days=days)

    new_expiry_str = new_expiry.strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    cursor.execute("""
        UPDATE members
        SET
            expiry_date = ?,
            is_active = 1
        WHERE telegram_id = ?
    """, (
        new_expiry_str,
        telegram_id
    ))

    conn.commit()
    conn.close()

    return True, new_expiry_str


def is_admin(telegram_id):
    return telegram_id == ADMIN_TELEGRAM_ID


# ============================================================
# TELEGRAM API
# ============================================================

def telegram_api(
    method,
    payload
):
    if not BOT_TOKEN:
        logger.error(
            "TELEGRAM_BOT_TOKEN is not configured."
        )
        return None

    url = f"{TELEGRAM_API_URL}/{method}"

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=15
        )

        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):
            logger.error(
                "Telegram API returned error: %s",
                result
            )

        return result

    except requests.RequestException as e:
        logger.error(
            "Telegram API request failed (%s): %s",
            method,
            e
        )

        return None

    except Exception as e:
        logger.error(
            "Telegram API error (%s): %s",
            method,
            e
        )

        return None


def send_message(
    chat_id,
    text,
    reply_markup=None
):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }

    if reply_markup:
        payload["reply_markup"] = reply_markup

    return telegram_api(
        "sendMessage",
        payload
    )


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
                    "url": (
                        "https://t.me/"
                        f"{ADMIN_USERNAME.lstrip('@')}"
                    )
                }
            ]
        ]
    }


# ============================================================
# MEMBER STATUS
# ============================================================

def subscription_status_text(member):
    first_name = member["first_name"] or ""
    last_name = member["last_name"] or ""

    return (
        "👤 <b>Account Info:</b>\n"
        f"ID: <code>{member['telegram_id']}</code>\n"
        f"Name: {first_name} {last_name}\n\n"
        f"📦 <b>Plan:</b> {member['plan']}\n"
        f"⏳ <b>Expires:</b> "
        f"{member['expiry_date'] or 'N/A'}\n"
        f"🟢 <b>Status:</b> "
        f"{'Active' if member['is_active'] else 'Inactive'}\n\n"
        f"👑 <b>Owner / Admin:</b> "
        f"{ADMIN_USERNAME}"
    )


# ============================================================
# /START
# ============================================================

def handle_start(message):
    user = message.get("from", {})

    telegram_id = int(
        user.get("id", 0)
    )

    first_name = user.get(
        "first_name",
        ""
    )

    last_name = user.get(
        "last_name",
        ""
    )

    username = user.get(
        "username",
        ""
    )

    if not telegram_id:
        return

    upsert_member(
        telegram_id,
        first_name,
        last_name,
        username
    )

    welcome_text = (
        f"Welcome to "
        f"<b>ForexSquad Bot</b>, "
        f"{first_name}!\n\n"

        "🚀 <b>ForexSquad Bot</b>\n"
        "Professional Forex Signal System\n\n"

        "📊 Get quality trading signals "
        "directly through Telegram.\n\n"

        f"For support or upgrades, "
        f"contact: {ADMIN_USERNAME}"
    )

    send_message(
        telegram_id,
        welcome_text,
        subscription_keyboard()
    )


# ============================================================
# /STATUS
# ============================================================

def handle_status(message):
    user = message.get(
        "from",
        {}
    )

    telegram_id = int(
        user.get("id", 0)
    )

    if not telegram_id:
        return

    member = get_member(
        telegram_id
    )

    if not member:
        send_message(
            telegram_id,
            "Please use /start to register first."
        )
        return

    text = subscription_status_text(
        member
    )

    send_message(
        telegram_id,
        text,
        subscription_keyboard()
    )


# ============================================================
# ADMIN /ACTIVATE
# ============================================================

def handle_admin_activate(
    telegram_id,
    args
):
    if not is_admin(telegram_id):
        return

    if len(args) < 2:
        send_message(
            telegram_id,
            "Usage:\n"
            "/activate <telegram_id> <days> [plan]"
        )
        return

    try:
        target_id = int(args[0])
        days = int(args[1])

        plan = (
            args[2]
            if len(args) > 2
            else "Premium"
        )

        if days <= 0:
            raise ValueError

    except ValueError:
        send_message(
            telegram_id,
            "Invalid parameters."
        )
        return

    success, result = activate_member(
        target_id,
        days,
        plan
    )

    if not success:
        send_message(
            telegram_id,
            f"Failed to activate member: "
            f"{result}"
        )

        return

    send_message(
        telegram_id,
        "Successfully activated member "
        f"<code>{target_id}</code>\n"
        f"Days: {days}\n"
        f"Plan: {plan}\n"
        f"Expires: {result}"
    )

    send_message(
        target_id,
        "🎉 <b>Your subscription has been activated!</b>\n\n"
        f"Plan: <b>{plan}</b>\n"
        f"Valid for: <b>{days} day(s)</b>",
        subscription_keyboard()
    )


# ============================================================
# ADMIN /DEACTIVATE
# ============================================================

def handle_admin_deactivate(
    telegram_id,
    args
):
    if not is_admin(telegram_id):
        return

    if len(args) < 1:
        send_message(
            telegram_id,
            "Usage:\n"
            "/deactivate <telegram_id>"
        )
        return

    try:
        target_id = int(args[0])

    except ValueError:
        send_message(
            telegram_id,
            "Invalid Telegram ID."
        )
        return

    success = deactivate_member(
        target_id
    )

    if success:
        send_message(
            telegram_id,
            f"Member <code>{target_id}</code> "
            "has been deactivated."
        )

        send_message(
            target_id,
            "⚠️ Your subscription has been "
            "deactivated by an administrator."
        )

    else:
        send_message(
            telegram_id,
            f"Member <code>{target_id}</code> "
            "was not found."
        )


# ============================================================
# ADMIN /EXTEND
# ============================================================

def handle_admin_extend(
    telegram_id,
    args
):
    if not is_admin(telegram_id):
        return

    if len(args) < 2:
        send_message(
            telegram_id,
            "Usage:\n"
            "/extend <telegram_id> <days>"
        )
        return

    try:
        target_id = int(args[0])
        days = int(args[1])

        if days <= 0:
            raise ValueError

    except ValueError:
        send_message(
            telegram_id,
            "Invalid parameters."
        )
        return

    success, result = extend_member(
        target_id,
        days
    )

    if not success:
        send_message(
            telegram_id,
            f"Failed to extend subscription: "
            f"{result}"
        )
        return

    send_message(
        telegram_id,
        f"Successfully extended member "
        f"<code>{target_id}</code> by "
        f"{days} day(s).\n"
        f"New Expiry: {result}"
    )

    send_message(
        target_id,
        "⏱️ <b>Your subscription has "
        "been extended!</b>\n\n"
        f"Added: <b>{days} day(s)</b>\n"
        f"New Expiry: <b>{result}</b>",
        subscription_keyboard()
    )


# ============================================================
# TELEGRAM WEBHOOK
# ============================================================

@app.route(
    "/telegram/webhook",
    methods=["POST"]
)
def telegram_webhook():

    data = request.get_json(
        silent=True
    )

    if not data:
        logger.info(
            "Telegram webhook received empty JSON."
        )

        return jsonify({
            "ok": True
        }), 200

    logger.info(
        "Telegram update received."
    )

    if "callback_query" in data:

        callback = data["callback_query"]

        user = callback.get(
            "from",
            {}
        )

        telegram_id = int(
            user.get("id", 0)
        )

        callback_data = callback.get(
            "data",
            ""
        )

        callback_id = callback.get(
            "id"
        )

        if callback_data == "subscription_status":

            member = get_member(
                telegram_id
            )

            if member:

                telegram_api(
                    "answerCallbackQuery",
                    {
                        "callback_query_id":
                            callback_id
                    }
                )

                text = subscription_status_text(
                    member
                )

                send_message(
                    telegram_id,
                    text,
                    subscription_keyboard()
                )

            else:

                telegram_api(
                    "answerCallbackQuery",
                    {
                        "callback_query_id":
                            callback_id,
                        "text":
                            "Please use /start first."
                    }
                )

        return jsonify({
            "ok": True
        }), 200

    message = (
        data.get("message")
        or data.get("edited_message")
    )

    if not message:
        return jsonify({
            "ok": True
        }), 200

    text = (
        message.get("text", "")
        .strip()
    )

    user = message.get(
        "from",
        {}
    )

    telegram_id = int(
        user.get("id", 0)
    )

    if not telegram_id:
        return jsonify({
            "ok": True
        }), 200

    if not text:
        return jsonify({
            "ok": True
        }), 200

    if text.startswith("/"):

        parts = text.split()

        command = (
            parts[0]
            .split("@")[0]
            .lower()
        )

        args = parts[1:]

        logger.info(
            "Telegram command received: %s",
            command
        )

        if command == "/start":

            handle_start(
                message
            )

        elif command == "/status":

            handle_status(
                message
            )

        elif command == "/activate":

            handle_admin_activate(
                telegram_id,
                args
            )

        elif command == "/deactivate":

            handle_admin_deactivate(
                telegram_id,
                args
            )

        elif command == "/extend":

            handle_admin_extend(
                telegram_id,
                args
            )

    return jsonify({
        "ok": True
    }), 200


# ============================================================
# TRADINGVIEW WEBHOOK FOR ALM04
# ============================================================

@app.route(
    "/webhook/alm04",
    methods=["POST"]
)
def tradingview_alm04_webhook():
    secret = request.args.get("secret")
    if secret != "Hemal@":
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json(silent=True)
    
    if not data:
        raw_text = request.get_data(as_text=True)
        signal_text = raw_text if raw_text else "TradingView Signal Triggered!"
    else:
        signal_text = data.get("text") or str(data)

    logger.info("TradingView signal received: %s", signal_text)

    send_message(
        ADMIN_TELEGRAM_ID,
        f"🚨 <b>TradingView Signal (Alm04):</b>\n\n{signal_text}"
    )

    return jsonify({
        "ok": True
    }), 200


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return jsonify({
        "status": "online",
        "service": "ForexSquad Bot",
        "telegram_webhook": "/telegram/webhook"
    }), 200


@app.route(
    "/health",
    methods=["GET"]
)
def health():

    return jsonify({
        "status": "healthy"
    }), 200


# ============================================================
# STARTUP
# ============================================================

init_db()


if __name__ == "__main__":

    logger.info(
        "ForexSquad Bot backend starting "
        "on %s:%s",
        HOST,
        PORT
    )

    app.run(
        host=HOST,
        port=PORT
    )
