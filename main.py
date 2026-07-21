import os
import io
import time
import logging
import sqlite3
import asyncio
import requests
import xml.etree.ElementTree as ET
from threading import Thread
from flask import Flask
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from telegram import (
    Update, 
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters
)

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "TELEGRAM_BOT_TOKEN_BURAYA")

# Flask Web Server (7/24 Aktif Tutma)
app = Flask(__name__)

@app.route('/')
def home():
    return "Midas Portfolio Bot 7/24 Aktif!"

def run_web():
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 8080)))

def keep_alive():
    t = Thread(target=run_web, daemon=True)
    t.start()

# Format Yardımcıları
def fmt(val, precision=4):
    if val is None:
        return "0"
    formatted = f"{val:,.{precision}f}"
    if '.' in formatted:
        formatted = formatted.rstrip('0').rstrip('.')
    return formatted

def fmt_usd(val):
    if val is None:
        return "$0.00"
    return f"${val:,.2f}"

# TCMB Canlı USD Kuru Çekici
def fetch_usd_rate() -> float:
    try:
        url = "https://www.tcmb.gov.tr/kurlar/today.xml"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            for currency in root.findall('Currency'):
                if currency.get('Kod') == 'USD':
                    rate = currency.find('BanknoteSelling').text or currency.find('ForexSelling').text
                    return float(rate.replace(',', '.'))
    except Exception:
        pass
    
    # Yedek API
    try:
        res = requests.get("https://open.er-api.com/v6/latest/USD", timeout=5)
        if res.status_code == 200:
            data = res.json()
            return float(data["rates"]["TRY"])
    except Exception:
        pass
    
    return 34.0  # Bağlantı koparsa varsayılan ortalama kur

# TEFAS Fon Fiyatı Çekici
def fetch_price(symbol: str) -> float:
    symbol = symbol.upper().strip()
    try:
        url = f"https://fontaraf.com/api/fund/{symbol}"
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if "price" in data:
                return float(data["price"])
    except Exception:
        pass
    return 0.0

# Database Kurulumu ve Tablo Yapısı
def init_db():
    conn = sqlite3.connect("portfolio.db")
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS portfolio (
            user_id INTEGER,
            symbol TEXT,
            amount REAL,
            avg_cost REAL,
            PRIMARY KEY (user_id, symbol)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            symbol TEXT,
            action TEXT,
            amount REAL,
            price REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cash (
            user_id INTEGER PRIMARY KEY,
            balance REAL DEFAULT 0.0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            user_id INTEGER,
            symbol TEXT,
            PRIMARY KEY (user_id, symbol)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS price_tracker (
            symbol TEXT PRIMARY KEY,
            last_price REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            user_id INTEGER PRIMARY KEY,
            hide_amounts INTEGER DEFAULT 0,
            currency_pref TEXT DEFAULT 'BOTH',
            notifications INTEGER DEFAULT 1
        )
    """)
    conn.commit()
    conn.close()

# Kullanıcı Ayarlarını Getir
def get_user_settings(user_id: int):
    conn = sqlite3.connect("portfolio.db")
    cursor = conn.cursor()
    cursor.execute("SELECT hide_amounts, currency_pref, notifications FROM settings WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"hide": bool(row[0]), "currency": row[1], "notify": bool(row[2])}
    return {"hide": False, "currency": "BOTH", "notify": True}

# Dinamik Ana Menü
def get_main_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📊 Portföyüm"), KeyboardButton("📈 Grafik")],
            [KeyboardButton("👀 Takip Listesi"), KeyboardButton("🔍 Fon Ara")],
            [KeyboardButton("➕ Fon Ekle"), KeyboardButton("🗑️ Fon Sil")],
            [KeyboardButton("💵 Nakit"), KeyboardButton("📈 Ort. Performans")],
            [KeyboardButton("📜 Geçmiş"), KeyboardButton("📄 PDF Raporu")],
            [KeyboardButton("⚙️ Ayarlar")]
        ],
        resize_keyboard=True
    )

# --- VERİTABANI İŞLEMLERİ ---
def db_add_asset(user_id: int, symbol: str, amount: float, cost: float):
    symbol = symbol.upper().strip()
    conn = sqlite3.connect("portfolio.db")
    cursor = conn.cursor()
    cursor.execute("SELECT amount, avg_cost FROM portfolio WHERE user_id = ? AND symbol = ?", (user_id, symbol))
    row = cursor.fetchone()
    if row:
        old_amount, old_cost = row
        new_amount = old_amount + amount
        if new_amount > 0:
            new_cost = ((old_amount * old_cost) + (amount * cost)) / new_amount
            cursor.execute("UPDATE portfolio SET amount = ?, avg_cost = ? WHERE user_id = ? AND symbol = ?", (new_amount, new_cost, user_id, symbol))
        else:
            cursor.execute("DELETE FROM portfolio WHERE user_id = ? AND symbol = ?", (user_id, symbol))
    else:
        cursor.execute("INSERT INTO portfolio (user_id, symbol, amount, avg_cost) VALUES (?, ?, ?, ?)", (user_id, symbol, amount, cost))
    
    cursor.execute("INSERT INTO history (user_id, symbol, action, amount, price) VALUES (?, ?, 'FON_EKLE', ?, ?)", (user_id, symbol, amount, cost))
    conn.commit()
    conn.close()

def db_remove_asset(user_id: int, symbol: str):
    symbol = symbol.upper().strip()
    conn = sqlite3.connect("portfolio.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM portfolio WHERE user_id = ? AND symbol = ?", (user_id, symbol))
    cursor.execute("INSERT INTO history (user_id, symbol, action, amount, price) VALUES (?, ?, 'FON_SIL', 0, 0)", (user_id, symbol))
    conn.commit()
    conn.close()

def db_get_portfolio(user_id: int):
    conn = sqlite3.connect("portfolio.db")
    cursor = conn.cursor()
    cursor.execute("SELECT symbol, amount, avg_cost FROM portfolio WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def db_get_cash(user_id: int) -> float:
    conn = sqlite3.connect("portfolio.db")
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM cash WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0.0

def db_update_cash(user_id: int, delta: float, is_set: bool = False):
    conn = sqlite3.connect("portfolio.db")
    cursor = conn.cursor()
    current = db_get_cash(user_id)
    new_bal = delta if is_set else current + delta
    if new_bal < 0: new_bal = 0.0
    cursor.execute("INSERT OR REPLACE INTO cash (user_id, balance) VALUES (?, ?)", (user_id, new_bal))
    cursor.execute("INSERT INTO history (user_id, symbol, action, amount, price) VALUES (?, 'NAKİT', 'NAKIT_GUNCELLE', ?, ?)", (user_id, new_bal, 0))
    conn.commit()
    conn.close()
    return new_bal

# --- BOT KOMUTLARI & YÖNETİCİLERİ ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    msg = (
        "📊 *Midas & TEFAS Portföy Takip Botu*\n\n"
        "Aşağıdaki menü butonlarını kullanarak tüm işlemlerinizi yapabilirsiniz.\n"
        "🔹 Hızlı Fon Ekleme: `/ekle <sembol> <adet> <maliyet>`\n"
        "🔹 Hızlı Fon Silme: `/sil <sembol>`"
    )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_main_keyboard())

async def portfoy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    items = db_get_portfolio(user_id)
    cash_bal = db_get_cash(user_id)
    settings = get_user_settings(user_id)
    usd_rate = fetch_usd_rate()

    hidden = settings["hide"]
    curr_pref = settings["currency"]

    if not items and cash_bal <= 0:
        await update.message.reply_text("📭 Portföyünüz boş. `/ekle` veya **💵 Nakit** butonu ile varlık ekleyebilirsiniz.", reply_markup=get_main_keyboard())
        return

    text = f"💼 *PORTFÖY ÖZETİ* (💱 1 USD = {usd_rate:.2f} TL)\n"
    text += "───────────────────────────\n"
    total_cost_tl = 0.0
    total_value_tl = 0.0

    for symbol, amount, avg_cost in items:
        cur_price = fetch_price(symbol)
        if cur_price <= 0: cur_price = avg_cost

        cost_val = amount * avg_cost
        curr_val = amount * cur_price
        pnl = curr_val - cost_val
        pnl_pct = (pnl / cost_val * 100) if cost_val > 0 else 0.0

        total_cost_tl += cost_val
        total_value_tl += curr_val

        pnl_str = f"+%{pnl_pct:.2f}" if pnl >= 0 else f"-%{abs(pnl_pct):.2f}"
        icon = "🟢" if pnl >= 0 else "🔴"

        text += f"🔹 *{symbol}*\n"
        if hidden:
            text += f"   Adet: `***` | Maliyet: `***` | Değer: `***`\n"
        else:
            if curr_pref == "TL":
                text += f"   Adet: `{fmt(amount, 2)}` | Maliyet: `{fmt(avg_cost, 4)} TL`\n"
                text += f"   Güncel: `{fmt(cur_price, 4)} TL` | Değer: `{fmt(curr_val, 2)} TL`\n"
                text += f"   Durum: {icon} `{fmt(pnl, 2)} TL` ({pnl_str})\n"
            elif curr_pref == "USD":
                text += f"   Adet: `{fmt(amount, 2)}` | Maliyet: `{fmt_usd(avg_cost/usd_rate)}`\n"
                text += f"   Güncel: `{fmt_usd(cur_price/usd_rate)}` | Değer: `{fmt_usd(curr_val/usd_rate)}`\n"
                text += f"   Durum: {icon} `{fmt_usd(pnl/usd_rate)}` ({pnl_str})\n"
            else: # BOTH
                text += f"   Adet: `{fmt(amount, 2)}` | Ort. Maliyet: `{fmt(avg_cost, 4)} TL` (`{fmt_usd(avg_cost/usd_rate)}`)\n"
                text += f"   Güncel: `{fmt(cur_price, 4)} TL` | Değer: `{fmt(curr_val, 2)} TL` (`{fmt_usd(curr_val/usd_rate)}`)\n"
                text += f"   Durum: {icon} `{fmt(pnl, 2)} TL` / `{fmt_usd(pnl/usd_rate)}` ({pnl_str})\n"
        text += "───────────────────────────\n"

    total_val_with_cash_tl = total_value_tl + cash_bal
    total_pnl_tl = total_value_tl - total_cost_tl
    total_pnl_pct = (total_pnl_tl / total_cost_tl * 100) if total_cost_tl > 0 else 0.0
    total_icon = "🚀" if total_pnl_tl >= 0 else "🔻"

    if hidden:
        text += f"💵 *Nakit Bakiye:* `***`\n"
        text += f"📈 *Genel Portföy Bakiye:* `***`\n"
    else:
        if curr_pref == "TL":
            text += f"💵 *Nakit Bakiye:* `{fmt(cash_bal, 2)} TL`\n"
            text += f"💰 *Toplam Fon Maliyeti:* `{fmt(total_cost_tl, 2)} TL`\n"
            text += f"📈 *Toplam Fon Değeri:* `{fmt(total_value_tl, 2)} TL`\n"
            text += f"📊 *Genel Portföy Bakiye:* `{fmt(total_val_with_cash_tl, 2)} TL`\n"
            text += f"{total_icon} *Toplam Kar/Zarar:* `{fmt(total_pnl_tl, 2)} TL` (%{total_pnl_pct:.2f})\n"
        elif curr_pref == "USD":
            text += f"💵 *Nakit Bakiye:* `{fmt_usd(cash_bal/usd_rate)}`\n"
            text += f"💰 *Toplam Fon Maliyeti:* `{fmt_usd(total_cost_tl/usd_rate)}`\n"
            text += f"📈 *Toplam Fon Değeri:* `{fmt_usd(total_value_tl/usd_rate)}`\n"
            text += f"📊 *Genel Portföy Bakiye:* `{fmt_usd(total_val_with_cash_tl/usd_rate)}`\n"
            text += f"{total_icon} *Toplam Kar/Zarar:* `{fmt_usd(total_pnl_tl/usd_rate)}` (%{total_pnl_pct:.2f})\n"
        else: # BOTH
            text += f"💵 *Nakit Bakiye:* `{fmt(cash_bal, 2)} TL` (`{fmt_usd(cash_bal/usd_rate)}`)\n"
            text += f"💰 *Toplam Fon Maliyeti:* `{fmt(total_cost_tl, 2)} TL` (`{fmt_usd(total_cost_tl/usd_rate)}`)\n"
            text += f"📈 *Toplam Fon Değeri:* `{fmt(total_value_tl, 2)} TL` (`{fmt_usd(total_value_tl/usd_rate)}`)\n"
            text += f"📊 *Genel Portföy Bakiye:* `{fmt(total_val_with_cash_tl, 2)} TL` (`{fmt_usd(total_val_with_cash_tl/usd_rate)}`)\n"
            text += f"{total_icon} *Toplam Kar/Zarar:* `{fmt(total_pnl_tl, 2)} TL` / `{fmt_usd(total_pnl_tl/usd_rate)}` (%{total_pnl_pct:.2f})\n"

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())

# --- AYARLAR MENÜSÜ & INLINE İŞLEMLERİ ---
async def ayarlar_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    settings = get_user_settings(user_id)

    curr_str = "TL & USD ($)" if settings["currency"] == "BOTH" else settings["currency"]
    hide_str = "🙈 Gizli" if settings["hide"] else "👁️ Görünür"
    notify_str = "🔔 Açık" if settings["notify"] else "🔕 Kapalı"

    text = (
        "⚙️ *PORTFÖY BAZLI BOT AYARLARI*\n\n"
        f"🔹 *Para Birimi Gösterimi:* `{curr_str}`\n"
        f"🔹 *Bakiye Tutarları:* `{hide_str}`\n"
        f"🔹 *Fiyat Artış Bildirimleri:* `{notify_str}`\n\n"
        "Aşağıdaki butonları kullanarak ayarlarınızı anlık değiştirebilirsiniz:"
    )

    keyboard = [
        [
            InlineKeyboardButton("🔀 Para Birimi Değiştir", callback_data="set_curr_toggle"),
            InlineKeyboardButton("👁️/🙈 Bakiye Gizle", callback_data="set_hide_toggle")
        ],
        [
            InlineKeyboardButton("🔔 Bildirimleri Değiştir", callback_data="set_notify_toggle"),
            InlineKeyboardButton("📥 Veritabanı Yedeği Al", callback_data="get_backup")
        ]
    ]

    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        query = update.callback_query
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    conn = sqlite3.connect("portfolio.db")
    cursor = conn.cursor()
    settings = get_user_settings(user_id)

    if data == "set_curr_toggle":
        next_curr = "USD" if settings["currency"] == "TL" else ("BOTH" if settings["currency"] == "USD" else "TL")
        cursor.execute("INSERT OR REPLACE INTO settings (user_id, hide_amounts, currency_pref, notifications) VALUES (?, ?, ?, ?)",
                       (user_id, int(settings["hide"]), next_curr, int(settings["notify"])))
    elif data == "set_hide_toggle":
        new_hide = not settings["hide"]
        cursor.execute("INSERT OR REPLACE INTO settings (user_id, hide_amounts, currency_pref, notifications) VALUES (?, ?, ?, ?)",
                       (user_id, int(new_hide), settings["currency"], int(settings["notify"])))
    elif data == "set_notify_toggle":
        new_notify = not settings["notify"]
        cursor.execute("INSERT OR REPLACE INTO settings (user_id, hide_amounts, currency_pref, notifications) VALUES (?, ?, ?, ?)",
                       (user_id, int(settings["hide"]), settings["currency"], int(new_notify)))
    elif data == "get_backup":
        conn.close()
        try:
            with open("portfolio.db", "rb") as doc:
                await context.bot.send_document(chat_id=user_id, document=doc, filename="portfolio_backup.db", caption="📥 Portföy veritabanı yedeğiniz.")
        except Exception as e:
            await query.message.reply_text(f"❌ Yedek gönderilirken hata oluştu: {e}")
        return

    conn.commit()
    conn.close()
    await ayarlar_menu(update, context)

# --- DİĞER FONKSİYONLAR ---
async def grafik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    items = db_get_portfolio(user_id)
    cash_bal = db_get_cash(user_id)

    if not items and cash_bal <= 0:
        await update.message.reply_text("📭 Grafiğinizi oluşturmak için portföyünüzde varlık olmalıdır.", reply_markup=get_main_keyboard())
        return

    labels, sizes = [], []
    for symbol, amount, avg_cost in items:
        cur_price = fetch_price(symbol)
        if cur_price <= 0: cur_price = avg_cost
        labels.append(symbol)
        sizes.append(amount * cur_price)

    if cash_bal > 0:
        labels.append("NAKİT")
        sizes.append(cash_bal)

    fig, ax = plt.subplots(figsize=(6, 6))
    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#17becf']
    ax.pie(sizes, labels=labels, autopct='%1.1f%%', startangle=140, colors=colors[:len(labels)], wedgeprops=dict(width=0.4, edgecolor='w'))
    ax.set_title("Portföy & Nakit Dağılımı", fontsize=14, pad=20)

    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', dpi=150)
    buf.seek(0)
    plt.close()

    await update.message.reply_photo(photo=buf, caption="📊 Portföy Varlık Dağılım Grafiğiniz", reply_markup=get_main_keyboard())

async def nakit_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bal = db_get_cash(user_id)
    usd_rate = fetch_usd_rate()
    settings = get_user_settings(user_id)

    bal_str = "***" if settings["hide"] else f"{fmt(bal, 2)} TL (`{fmt_usd(bal/usd_rate)}`)"
    text = (
        f"💵 *NAKİT PORTFÖYÜ*\n\n"
        f"Mevcut Nakit Bakiyeniz: `{bal_str}`\n\n"
        f"💡 *Nakit Güncelleme Komutları:*\n"
        f"🔹 Nakit Ekle: `/nakitekle <tutar>` (Örn: `/nakitekle 5000`)\n"
        f"🔹 Nakit Çıkar: `/nakitcikar <tutar>` (Örn: `/nakitcikar 1500`)"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())

async def nakit_ekle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("❌ Kullanım: `/nakitekle 1000`", parse_mode="Markdown")
        return
    try:
        val = float(context.args[0].replace(',', '.'))
        new_bal = db_update_cash(user_id, val)
        await update.message.reply_text(f"✅ Nakit eklendi! Yeni Bakiye: `{fmt(new_bal, 2)} TL`", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Geçerli bir sayı yazın.")

async def nakit_cikar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("❌ Kullanım: `/nakitcikar 500`", parse_mode="Markdown")
        return
    try:
        val = float(context.args[0].replace(',', '.'))
        new_bal = db_update_cash(user_id, -val)
        await update.message.reply_text(f"✅ Nakit çıkarıldı! Yeni Bakiye: `{fmt(new_bal, 2)} TL`", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Geçerli bir sayı yazın.")

async def ort_performans_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    items = db_get_portfolio(user_id)
    if not items:
        await update.message.reply_text("📭 Performans analizi için portföyünüzde fon olmalıdır.", reply_markup=get_main_keyboard())
        return

    best_symbol, best_pct = None, -999999.0
    worst_symbol, worst_pct = None, 999999.0
    total_cost, total_val = 0.0, 0.0

    for symbol, amount, avg_cost in items:
        cur_price = fetch_price(symbol)
        if cur_price <= 0: cur_price = avg_cost
        cost = amount * avg_cost
        val = amount * cur_price
        pnl_pct = ((val - cost) / cost * 100) if cost > 0 else 0.0

        total_cost += cost
        total_val += val

        if pnl_pct > best_pct:
            best_pct = pnl_pct
            best_symbol = symbol
        if pnl_pct < worst_pct:
            worst_pct = pnl_pct
            worst_symbol = symbol

    weighted_return = ((total_val - total_cost) / total_cost * 100) if total_cost > 0 else 0.0

    msg = (
        f"📈 *PORTFÖY PERFORMANS ANALİZİ*\n"
        f"───────────────────────────\n"
        f"🚀 *En Yüksek Getiri:* `{best_symbol}` (%{best_pct:.2f})\n"
        f"🔻 *En Düşük Getiri:* `{worst_symbol}` (%{worst_pct:.2f})\n"
        f"📊 *Ağırlıklı Ortalama Getiri:* %{weighted_return:.2f}\n"
        f"───────────────────────────\n"
        f"💡 Performans hesaplaması mevcut canlı fon fiyatlarına göre yapılır."
    )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_main_keyboard())

async def takip_listesi_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect("portfolio.db")
    cursor = conn.cursor()
    cursor.execute("SELECT symbol FROM watchlist WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        msg = (
            "👀 *TAKİP LİSTESİ*\n\n"
            "Takip listeniz boş.\n"
            "🔹 Fon Takip Etmek İçin: `/takip <sembol>` (Örn: `/takip TI2`)\n"
            "🔹 Takipten Çıkarmak İçin: `/takipsil <sembol>`"
        )
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_main_keyboard())
        return

    msg = "👀 *TAKİP ETTİĞİNİZ FONLAR*\n───────────────────────────\n"
    for (sym,) in rows:
        price = fetch_price(sym)
        msg += f"🔹 *{sym}*: `{fmt(price, 6)} TL`\n"
    msg += "───────────────────────────\n💡 `/takip <sembol>` veya `/takipsil <sembol>` yazarak yönetebilirsiniz."
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_main_keyboard())

async def takip_ekle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("❌ Kullanım: `/takip TI2`", parse_mode="Markdown")
        return
    sym = context.args[0].upper().strip()
    conn = sqlite3.connect("portfolio.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO watchlist (user_id, symbol) VALUES (?, ?)", (user_id, sym))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"👀 *{sym}* takip listenize eklendi!", parse_mode="Markdown")

async def takip_sil_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("❌ Kullanım: `/takipsil TI2`", parse_mode="Markdown")
        return
    sym = context.args[0].upper().strip()
    conn = sqlite3.connect("portfolio.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM watchlist WHERE user_id = ? AND symbol = ?", (user_id, sym))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"🗑️ *{sym}* takip listenizden çıkarıldı.", parse_mode="Markdown")

async def fon_ara_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Sorgulamak istediğiniz fon için: `/ara <sembol>` yazın.\nÖrnek: `/ara TI2`", parse_mode="Markdown")

async def fon_ara_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Örnek kullanım: `/ara TI2`", parse_mode="Markdown")
        return
    sym = context.args[0].upper().strip()
    price = fetch_price(sym)
    usd_rate = fetch_usd_rate()
    if price > 0:
        await update.message.reply_text(f"🔍 *{sym} Fon Fiyatı:*\n💵 TL Fiyat: `{fmt(price, 6)} TL`\n💲 USD Fiyat: `{fmt_usd(price/usd_rate)}`", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ *{sym}* için fiyat bilgisi alınamadı veya kod hatalı.", parse_mode="Markdown")

async def gecmis_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect("portfolio.db")
    cursor = conn.cursor()
    cursor.execute("SELECT symbol, action, amount, price, timestamp FROM history WHERE user_id = ? ORDER BY id DESC LIMIT 10", (user_id,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📜 Henüz işlem geçmişiniz bulunmuyor.", reply_markup=get_main_keyboard())
        return

    msg = "📜 *SON İŞLEM GEÇMİŞİ*\n───────────────────────────\n"
    for sym, act, amt, prc, ts in rows:
        msg += f"🗓 `{ts}` | *{act}*\n   Fon: `{sym}` | Adet: `{fmt(amt, 2)}` | Fiyat: `{fmt(prc, 4)} TL`\n"
        msg += "───────────────────────────\n"
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_main_keyboard())

async def pdf_raporu_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    items = db_get_portfolio(user_id)
    cash_bal = db_get_cash(user_id)
    usd_rate = fetch_usd_rate()

    fig, ax = plt.subplots(figsize=(8.5, 11))
    ax.axis('off')

    report_text = f"MIDAS & TEFAS PORTFOY DETAYLI RAPORU (1 USD = {usd_rate:.2f} TL)\n"
    report_text += "="*55 + "\n\n"

    tot_val = 0.0
    tot_cost = 0.0

    for sym, amt, cost in items:
        p = fetch_price(sym)
        if p <= 0: p = cost
        c_val = amt * cost
        v_val = amt * p
        tot_cost += c_val
        tot_val += v_val
        report_text += f"Fon: {sym:<5} | Adet: {amt:<8.2f} | Mal(TL): {cost:<7.4f} | Guncel(TL): {p:<7.4f} | Deger: {v_val:<9.2f} TL (${v_val/usd_rate:<7.2f})\n"

    report_text += "-"*55 + "\n"
    report_text += f"Nakit Bakiye      : {cash_bal:.2f} TL (${cash_bal/usd_rate:.2f})\n"
    report_text += f"Toplam Fon Maliyet: {tot_cost:.2f} TL (${tot_cost/usd_rate:.2f})\n"
    report_text += f"Toplam Fon Deger   : {tot_val:.2f} TL (${tot_val/usd_rate:.2f})\n"
    report_text += f"Genel Portfoy    : {tot_val + cash_bal:.2f} TL (${(tot_val + cash_bal)/usd_rate:.2f})\n"
    report_text += f"Toplam Kar/Zarar  : {tot_val - tot_cost:.2f} TL (${(tot_val - tot_cost)/usd_rate:.2f})\n"

    ax.text(0.03, 0.95, report_text, transform=ax.transAxes, fontsize=9, verticalalignment='top', fontfamily='monospace')

    buf = io.BytesIO()
    plt.savefig(buf, format='pdf', bbox_inches='tight')
    buf.seek(0)
    plt.close()

    await update.message.reply_document(
        document=buf,
        filename="Portfoy_Raporu.pdf",
        caption="📄 Detaylı TL ve USD Destekli PDF Raporunuz Hazırlandı.",
        reply_markup=get_main_keyboard()
    )

async def fon_ekle_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("➕ Fon eklemek için şu formatta yazın:\n`/ekle <sembol> <adet> <maliyet>`\n\nÖrnek: `/ekle TP2 1000 2.15`", parse_mode="Markdown")

async def fon_sil_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🗑️ Fon silmek için şu formatta yazın:\n`/sil <sembol>`\n\nÖrnek: `/sil TP2`", parse_mode="Markdown")

async def ekle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if len(context.args) < 3:
        await update.message.reply_text("❌ Örnek kullanım: `/ekle TP2 1000 2.15`", parse_mode="Markdown")
        return
    try:
        symbol = context.args[0].upper()
        amount = float(context.args[1].replace(',', '.'))
        cost = float(context.args[2].replace(',', '.'))
        db_add_asset(user_id, symbol, amount, cost)
        await update.message.reply_text(f"✅ *{symbol}* eklendi!\nAdet: `{fmt(amount, 2)}` | Maliyet: `{fmt(cost, 6)} TL`", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Lütfen sayıları doğru yazın.")

async def sil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if len(context.args) < 1:
        await update.message.reply_text("❌ Örnek kullanım: `/sil TP2`", parse_mode="Markdown")
        return
    symbol = context.args[0].upper()
    db_remove_asset(user_id, symbol)
    await update.message.reply_text(f"🗑️ *{symbol}* portföyden silindi.", parse_mode="Markdown")

# --- ARKA PLAN FİYAT & BİLDİRİM TAKİBİ ---
def start_price_monitor():
    async def monitor():
        from telegram import Bot
        bot = Bot(token=BOT_TOKEN)
        while True:
            try:
                conn = sqlite3.connect("portfolio.db")
                cursor = conn.cursor()
                cursor.execute("SELECT DISTINCT symbol FROM portfolio UNION SELECT DISTINCT symbol FROM watchlist")
                symbols = [r[0] for r in cursor.fetchall()]

                for sym in symbols:
                    new_price = fetch_price(sym)
                    if new_price <= 0: continue

                    cursor.execute("SELECT last_price FROM price_tracker WHERE symbol = ?", (sym,))
                    row = cursor.fetchone()
                    
                    if row:
                        old_price = row[0]
                        if new_price > old_price:
                            diff = new_price - old_price
                            pct = (diff / old_price) * 100

                            cursor.execute("SELECT user_id, amount FROM portfolio WHERE symbol = ?", (sym,))
                            holders = cursor.fetchall()
                            for u_id, amt in holders:
                                settings = get_user_settings(u_id)
                                if not settings["notify"]: continue

                                gain = amt * diff
                                total_val = amt * new_price
                                alert = (
                                    f"🚀 *FİYAT ARTIŞ BİLDİRİMİ!* 📈\n\n"
                                    f"🔹 *Fon Kodu:* `{sym}`\n"
                                    f"💵 *Eski Fiyat:* `{fmt(old_price, 6)} TL`\n"
                                    f"🟢 *Yeni Fiyat:* `{fmt(new_price, 6)} TL`\n"
                                    f"📊 *Değişim:* `+% {pct:.2f}` (`+{fmt(diff, 6)} TL`)\n\n"
                                    f"💼 *Portföyünüzdeki Etkisi:*\n"
                                    f"📦 *Adet:* `{fmt(amt, 2)}` \n"
                                    f"💰 *Anlık Kar Kazancı:* `+{fmt(gain, 2)} TL`\n"
                                    f"📈 *Toplam Fon Değeri:* `{fmt(total_val, 2)} TL`"
                                )
                                try:
                                    await bot.send_message(chat_id=u_id, text=alert, parse_mode="Markdown")
                                except Exception:
                                    pass

                    cursor.execute("INSERT OR REPLACE INTO price_tracker (symbol, last_price) VALUES (?, ?)", (sym, new_price))
                    conn.commit()
                conn.close()
            except Exception as e:
                logging.error(f"Fiyat takip hatası: {e}")
            await asyncio.sleep(600)

    def run_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(monitor())

    t = Thread(target=run_loop, daemon=True)
    t.start()

def main():
    init_db()
    keep_alive()
    start_price_monitor()

    while True:
        try:
            logging.info("Bot başlatılıyor...")
            application = ApplicationBuilder().token(BOT_TOKEN).build()

            # Komutlar
            application.add_handler(CommandHandler("start", start))
            application.add_handler(CommandHandler("ekle", ekle))
            application.add_handler(CommandHandler("sil", sil))
            application.add_handler(CommandHandler("portfoy", portfoy))
            application.add_handler(CommandHandler("grafik", grafik))
            application.add_handler(CommandHandler("nakitekle", nakit_ekle_cmd))
            application.add_handler(CommandHandler("nakitcikar", nakit_cikar_cmd))
            application.add_handler(CommandHandler("takip", takip_ekle_cmd))
            application.add_handler(CommandHandler("takipsil", takip_sil_cmd))
            application.add_handler(CommandHandler("ara", fon_ara_cmd))

            # Inline Callback Handlers
            application.add_handler(CallbackQueryHandler(settings_callback))

            # Buton Mesaj Yakalayıcılar
            application.add_handler(MessageHandler(filters.Regex(r"^📊 Portföyüm$"), portfoy))
            application.add_handler(MessageHandler(filters.Regex(r"^📈 Grafik$"), grafik))
            application.add_handler(MessageHandler(filters.Regex(r"^👀 Takip Listesi$"), takip_listesi_view))
            application.add_handler(MessageHandler(filters.Regex(r"^🔍 Fon Ara$"), fon_ara_prompt))
            application.add_handler(MessageHandler(filters.Regex(r"^➕ Fon Ekle$"), fon_ekle_prompt))
            application.add_handler(MessageHandler(filters.Regex(r"^🗑️ Fon Sil$"), fon_sil_prompt))
            application.add_handler(MessageHandler(filters.Regex(r"^💵 Nakit$"), nakit_view))
            application.add_handler(MessageHandler(filters.Regex(r"^📈 Ort\. Performans$"), ort_performans_view))
            application.add_handler(MessageHandler(filters.Regex(r"^📜 Geçmiş$"), gecmis_view))
            application.add_handler(MessageHandler(filters.Regex(r"^📄 PDF Raporu$"), pdf_raporu_view))
            application.add_handler(MessageHandler(filters.Regex(r"^⚙️ Ayarlar$"), ayarlar_menu))

            application.run_polling(drop_pending_updates=True)
        except Exception as e:
            logging.error(f"Hata oluştu, yeniden başlatılıyor: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
