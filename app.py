import os
import sqlite3
import requests
from datetime import datetime, date, timedelta
from flask import Flask, request, jsonify

app = Flask(__name__)

# ============================================================
# CONFIG
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8879920230:AAHXPrHiOfEBuXwaFH3L5OCK0yMLq2UgApE")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "YOUR_SECRET_KEY")
OWNER_CONTACT = "@Almahmud09"

DATABASE = "forexsquad.db"

# ============================================================
# DATABASE
# ============================================================

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            balance REAL DEFAULT 0.0,
            risk_amount REAL DEFAULT 5.0,
            daily_loss_limit REAL DEFAULT 5.0,
            rr REAL DEFAULT 3.0,
            max_trades_per_day INTEGER DEFAULT 1,
            daily_loss REAL DEFAULT 0.0,
            trades_today INTEGER DEFAULT 0,
            last_reset_date TEXT,
            active INTEGER DEFAULT 0,
            step TEXT DEFAULT 'NONE',
            trial_end_date TEXT,
            created_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_key TEXT UNIQUE,
            symbol TEXT,
            action TEXT,
            price REAL,
            sl REAL,
            tp REAL,
            timeframe TEXT,
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()

# ============================================================
# DAILY RESET & TRIAL CHECK
# ============================================================

def reset_member_if_new_day(member):
    today = str(date.today())
    if member["last_reset_date"] != today:
        conn = get_db()
        conn.execute("""
            UPDATE members
            SET daily_loss = 0,
                trades_today = 0,
                last_reset_date = ?
            WHERE telegram_id = ?
        """, (today, member["telegram_id"]))
        conn.commit()
        conn.close()
        return True
    return False

def check_trial_status(member):
    if member["trial_end_date"]:
        try:
            end_date = datetime.strptime(member["trial_end_date"], "%Y-%m-%d").date()
            if date.today() > end_date:
                conn = get_db()
                conn.execute("UPDATE members SET active = 0 WHERE telegram_id = ?", (member["telegram_id"],))
                conn.commit()
                conn.close()
                return False
        except:
            pass
    return member["active"] == 1

# ============================================================
# TELEGRAM
# ============================================================

def telegram_request(method, data):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    try:
        response = requests.post(url, json=data, timeout=10)
        return response.json()
    except Exception as e:
        print("Telegram error:", e)
        return None

def send_telegram_message(chat_id, message, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return telegram_request("sendMessage", payload)

# ============================================================
# MEMBER
# ============================================================

def get_member(telegram_id):
    conn = get_db()
    member = conn.execute(
        "SELECT * FROM members WHERE telegram_id = ?",
        (telegram_id,)
    ).fetchone()
    conn.close()
    return member

def create_or_reset_member(telegram_id, username):
    conn = get_db()
    conn.execute(
        """
        INSERT INTO members (telegram_id, username, last_reset_date, step, created_at)
        VALUES (?, ?, ?, 'CHOOSE_PLAN', ?)
        ON CONFLICT(telegram_id) DO UPDATE SET username = ?, step = 'CHOOSE_PLAN'
        """,
        (telegram_id, username, str(date.today()), datetime.utcnow().isoformat(), username)
    )
    conn.commit()
    conn.close()

# ============================================================
# FORMATTING
# ============================================================

def format_price(value):
    try:
        if value >= 100:
            return f"{value:.2f}"
        else:
            return f"{value:.5f}"
    except:
        return str(value)

# ============================================================
# SIGNAL VALIDATION
# ============================================================

def validate_signal(data):
    symbol = data.get("symbol")
    action = data.get("action")
    price = data.get("price")
    sl = data.get("sl")
    tp = data.get("tp")

    if not symbol or action not in ["BUY", "SELL"] or price is None or sl is None or tp is None:
        return False, "Invalid parameters or missing TP"
    try:
        price = float(price)
        sl = float(sl)
        tp = float(tp)
    except:
        return False, "Invalid price/SL/TP format"

    if action == "BUY" and (sl >= price or tp <= price):
        return False, "Invalid BUY levels (SL must be < Entry and TP must be > Entry)"
    if action == "SELL" and (sl <= price or tp >= price):
        return False, "Invalid SELL levels (SL must be > Entry and TP must be < Entry)"

    return True, "OK"

def signal_already_processed(signal_key):
    conn = get_db()
    signal = conn.execute("SELECT id FROM signals WHERE signal_key = ?", (signal_key,)).fetchone()
    conn.close()
    return signal is not None

def save_signal(signal_key, symbol, action, price, sl, tp, timeframe):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO signals (signal_key, symbol, action, price, sl, tp, timeframe, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (signal_key, symbol, action, price, sl, tp, timeframe, datetime.utcnow().isoformat())
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()

# ============================================================
# PROCESS SIGNAL FOR MEMBER
# ============================================================

def process_signal_for_member(member, symbol, action, entry, sl, tp, timeframe):
    telegram_id = member["telegram_id"]

    reset_member_if_new_day(member)
    member = get_member(telegram_id)

    if not member or not check_trial_status(member):
        return

    if member["daily_loss"] >= member["daily_loss_limit"]:
        return

    if member["trades_today"] >= member["max_trades_per_day"]:
        return

    emoji = "🟢" if action == "BUY" else "🔴"
    message = (
        f"🚨 *ForexSquad SMC SIGNAL* 🚨\n\n"
        f"{emoji} *{action}*\n\n"
        f"🔹 *Symbol:* `{symbol}`\n"
        f"🔹 *Timeframe:* `{timeframe}`\n\n"
        f"📍 *Entry:* `{format_price(entry)}`\n"
        f"🛑 *Stop Loss:* `{format_price(sl)}`\n"
        f"🎯 *Take Profit:* `{format_price(tp)}`\n\n"
        f"💰 *Risk:* `${member['risk_amount']:.2f}`\n\n"
        f"⚠️ *Max trades/day:* `{member['max_trades_per_day']}`"
    )

    result = send_telegram_message(telegram_id, message)
    if result and result.get("ok"):
        conn = get_db()
        conn.execute("UPDATE members SET trades_today = trades_today + 1 WHERE telegram_id = ?", (telegram_id,))
        conn.commit()
        conn.close()

# ============================================================
# TRADINGVIEW WEBHOOK
# ============================================================

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        secret = request.args.get("secret")
        if secret != WEBHOOK_SECRET:
            return jsonify({"status": "error", "message": "Unauthorized"}), 401

        data = request.get_json(silent=True)
        is_valid, err_msg = validate_signal(data)
        if not data or not is_valid:
            return jsonify({"status": "error", "message": err_msg}), 400

        symbol, action = data.get("symbol"), data.get("action")
        entry = float(data.get("price"))
        sl = float(data.get("sl"))
        tp = float(data.get("tp"))
        timeframe = data.get("timeframe", "UNKNOWN")
        timestamp = data.get("timestamp", datetime.utcnow().isoformat())
        
        signal_key = f"{symbol}|{action}|{timeframe}|{entry}|{timestamp}"

        if signal_already_processed(signal_key):
            return jsonify({"status": "ignored"}), 200

        save_signal(signal_key, symbol, action, entry, sl, tp, timeframe)

        conn = get_db()
        members = conn.execute("SELECT * FROM members WHERE active = 1").fetchall()
        conn.close()

        for member in members:
            process_signal_for_member(member, symbol, action, entry, sl, tp, timeframe)

        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ============================================================
# TELEGRAM WEBHOOK (WIZARD & CALLBACKS)
# ============================================================

@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    try:
        update = request.get_json(silent=True)
        if not update:
            return jsonify({"ok": True})

        if "callback_query" in update:
            query = update["callback_query"]
            telegram_id = query["from"]["id"]
            data = query["data"]

            if data == "trial":
                trial_end = (date.today() + timedelta(days=15)).strftime("%Y-%m-%d")
                conn = get_db()
                conn.execute(
                    "UPDATE members SET active = 1, trial_end_date = ?, step = 'ASK_BALANCE' WHERE telegram_id = ?",
                    (trial_end, telegram_id)
                )
                conn.commit()
                conn.close()

                send_telegram_message(
                    telegram_id,
                    "🎉 *15 Days Free Trial Activated!*\n\n"
                    "Let's set up your trading profile now.\n\n"
                    "💵 Please enter your trading account balance as a number only (e.g., `1000`):"
                )

            elif data == "sub":
                send_telegram_message(
                    telegram_id,
                    f"💎 *Subscription Information*\n\n"
                    f"To purchase a subscription or get VIP access, please contact the owner directly:\n"
                    f"👉 Contact: {OWNER_CONTACT}"
                )

            return jsonify({"ok": True})

        message = update.get("message")
        if not message or "chat" not in message:
            return jsonify({"ok": True})

        telegram_id = message["chat"]["id"]
        username = message["chat"].get("username", "")
        text = (message.get("text") or "").strip()

        member = get_member(telegram_id)

        if text == "/start":
            create_or_reset_member(telegram_id, username)
            keyboard = {
                "inline_keyboard": [
                    [{"text": "🚀 15 Days Free Trial", "callback_data": "trial"}],
                    [{"text": "💎 Subscription / VIP", "callback_data": "sub"}]
                ]
            }
            send_telegram_message(
                telegram_id,
                "✅ *Welcome to ForexSquad Signal Bot!*\n\n"
                "Please choose an option below to get started:",
                reply_markup=keyboard
            )
            return jsonify({"ok": True})

        if text == "/settings":
            if not member or member["active"] != 1:
                send_telegram_message(telegram_id, "⚠️ You don't have an active plan or trial. Type `/start` to begin.")
                return jsonify({"ok": True})

            message_text = (
                "⚙️ *Your Trading Settings*\n\n"
                f"💵 Balance: `${member['balance']:.2f}`\n"
                f"💰 Risk: `${member['risk_amount']:.2f}`\n"
                f"📊 RR: `1:{member['rr']:g}`\n"
                f"🔢 Max Trades/Day: `{member['max_trades_per_day']}`\n"
                f"⏳ Trial Ends: `{member['trial_end_date']}`"
            )
            send_telegram_message(telegram_id, message_text)
            return jsonify({"ok": True})

        if member and member["step"] != "NONE":
            step = member["step"]
            conn = get_db()

            if step == "ASK_BALANCE":
                try:
                    balance = float(text)
                    conn.execute("UPDATE members SET balance = ?, step = 'ASK_RISK' WHERE telegram_id = ?", (balance, telegram_id))
                    conn.commit()
                    conn.close()
                    send_telegram_message(telegram_id, "✅ Balance saved.\n\n💰 How much risk (in dollars) do you want to take per trade? Enter a number only (e.g., `10`):")
                except ValueError:
                    conn.close()
                    send_telegram_message(telegram_id, "❌ Please enter a valid number (e.g., `1000`):")

            elif step == "ASK_RISK":
                try:
                    risk = float(text)
                    conn.execute("UPDATE members SET risk_amount = ?, step = 'ASK_RR' WHERE telegram_id = ?", (risk, telegram_id))
                    conn.commit()
                    conn.close()
                    send_telegram_message(telegram_id, "✅ Risk amount saved.\n\n🎯 What Risk/Reward (RR) ratio reference do you want? Enter a number only (e.g., `3`):")
                except ValueError:
                    conn.close()
                    send_telegram_message(telegram_id, "❌ Please enter a valid number:")

            elif step == "ASK_RR":
                try:
                    rr = float(text)
                    conn.execute("UPDATE members SET rr = ?, step = 'ASK_TRADES' WHERE telegram_id = ?", (rr, telegram_id))
                    conn.commit()
                    conn.close()
                    send_telegram_message(telegram_id, "✅ RR saved.\n\n🔢 What is your maximum number of trades per day? Enter a number only (e.g., `2`):")
                except ValueError:
                    conn.close()
                    send_telegram_message(telegram_id, "❌ Please enter a valid number:")

            elif step == "ASK_TRADES":
                try:
                    trades = int(text)
                    conn.execute("UPDATE members SET max_trades_per_day = ?, step = 'NONE' WHERE telegram_id = ?", (trades, telegram_id))
                    conn.commit()
                    conn.close()
                    send_telegram_message(
                        telegram_id,
                        "🎉 *Setup Complete!* Your settings have been saved successfully.\n\n"
                        "You will now receive TradingView signals according to your preferences. Type `/settings` to view them."
                    )
                except ValueError:
                    conn.close()
                    send_telegram_message(telegram_id, "❌ Please enter a valid number:")

            return jsonify({"ok": True})

        send_telegram_message(telegram_id, "Type `/start` to launch the bot or `/settings` to check your configuration.")
        return jsonify({"ok": True})

    except Exception as e:
        print("Telegram webhook error:", e)
        return jsonify({"ok": True})

# ============================================================
# HEALTH CHECK & START
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "online", "bot": "ForexSquad Signal Bot"})

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
