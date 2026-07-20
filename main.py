import os
import io
import time
import logging
import sqlite3
import requests
from threading import Thread
from flask import Flask
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters
)

# Logging Ayarları
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "TELEGRAM_BOT_TOKEN_BURAYA")
ADMIN_ID = os.getenv("ADMIN_ID", "0")

# Flask Web Sunucusu (7/24 Aktif Kalma)
app = Flask(__name__)

@app.route('/')
def home():
    return "Midas Portfolio Bot 7/24 Aktif!"

def run_web():
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 8080)))

def keep_alive():
    t = Thread(target=run_web)
    t.daemon = True
    t.start()

# Alt Menü Buton Klavyesi (Görselindeki İle Birebir)
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📊 Portföyüm"), KeyboardButton("📈 Grafik")],
        [KeyboardButton("👀 Takip Listesi"), KeyboardButton("➕ Fon Ekle")],
        [KeyboardButton("🗑️ Fon Sil"), KeyboardButton("💵 Nakit")],
        [KeyboardButton("📈 Ort. Performans"), KeyboardButton("🔍 Fon Ara")],
        [KeyboardButton("⏰ Alarmlar"), KeyboardButton("📜 Geçmiş")],
        [KeyboardButton("📄 PDF Raporu"), KeyboardButton("👁️ Tutarları Göster")]
    ],
    resize_keyboard=True
)

# SQLite Veritabanı
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
    conn.commit()
    conn.close()

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
    
    cursor.execute("INSERT INTO history (user_id, symbol, action, amount, price) VALUES (?, ?, 'ADD', ?, ?)", (user_id, symbol, amount, cost))
    conn.commit()
    conn.close()

def db_remove_asset(user_id: int, symbol: str):
    symbol = symbol.upper().strip()
    conn = sqlite3.connect("portfolio.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM portfolio WHERE user_id = ? AND symbol = ?", (user_id, symbol))
    cursor.execute("INSERT INTO history (user_id, symbol, action, amount, price) VALUES (?, ?, 'REMOVE', 0, 0)", (user_id, symbol))
    conn.commit()
    conn.close()

def db_get_portfolio(user_id: int):
    conn = sqlite3.connect("portfolio.db")
    cursor = conn.cursor()
    cursor.execute("SELECT symbol, amount, avg_cost FROM portfolio WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def db_clear_portfolio(user_id: int):
    conn = sqlite3.connect("portfolio.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM portfolio WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

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

# Komut ve Buton Fonksiyonları
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📊 *Midas & TEFAS Portföy Takip Botu*\n\n"
        "Aşağıdaki menü butonlarını kullanarak işlem yapabilir veya komut yazabilirsiniz:\n"
        "🔹 `/ekle <sembol> <adet> <maliyet>`\n"
        "🔹 `/sil <sembol>`"
    )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "💡 *Kullanım Örnekleri:*\n\n"
        "`/ekle TP2 1000 2.15` -> 1000 adet TP2 fonu ekler.\n"
        "`/sil TP2` -> TP2 fonunu siler.\n"
        "`/portfoy` veya **📊 Portföyüm** butonu ile özeti görebilirsin."
    )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)

async def ekle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if len(context.args) < 3:
        await update.message.reply_text("❌ *Hatalı Kullanım!*\nÖrnek: `/ekle TP2 1000 2.15`", parse_mode="Markdown")
        return
    try:
        symbol = context.args[0].upper()
        amount = float(context.args[1].replace(',', '.'))
        cost = float(context.args[2].replace(',', '.'))
        db_add_asset(user_id, symbol, amount, cost)
        await update.message.reply_text(f"✅ *{symbol}* portföye eklendi!\nAdet: `{amount}` | Maliyet: `{cost:.2f} TL`", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Sayısal değerleri doğru girdiğinizden emin olun.")

async def sil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if len(context.args) < 1:
        await update.message.reply_text("❌ *Hatalı Kullanım!*\nÖrnek: `/sil TP2`", parse_mode="Markdown")
        return
    symbol = context.args[0].upper()
    db_remove_asset(user_id, symbol)
    await update.message.reply_text(f"🗑️ *{symbol}* portföyden silindi.", parse_mode="Markdown")

async def portfoy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    items = db_get_portfolio(user_id)
    if not items:
        await update.message.reply_text("📭 Portföyünüzde henüz varlık bulunmuyor. `/ekle` komutu ile ekleyebilirsiniz.", reply_markup=MAIN_KEYBOARD)
        return

    text = "💼 *PORTFÖY ÖZETİ*\n"
    text += "───────────────────────────\n"
    total_cost_sum = 0.0
    total_value_sum = 0.0

    for symbol, amount, avg_cost in items:
        cur_price = fetch_price(symbol)
        if cur_price <= 0:
            cur_price = avg_cost

        cost_val = amount * avg_cost
        curr_val = amount * cur_price
        pnl = curr_val - cost_val
        pnl_pct = (pnl / cost_val * 100) if cost_val > 0 else 0.0

        total_cost_sum += cost_val
        total_value_sum += curr_val

        pnl_str = f"+%{pnl_pct:.2f}" if pnl >= 0 else f"-%{abs(pnl_pct):.2f}"
        icon = "🟢" if pnl >= 0 else "🔴"

        text += f"🔹 *{symbol}*\n"
        text += f"   Adet: `{amount:,.2f}` | Ort. Maliyet: `{avg_cost:.2f} TL`\n"
        text += f"   Güncel: `{cur_price:.2f} TL` | Değer: `{curr_val:,.2f} TL`\n"
        text += f"   Durum: {icon} `{pnl:,.2f} TL` ({pnl_str})\n"
        text += "───────────────────────────\n"

    total_pnl = total_value_sum - total_cost_sum
    total_pnl_pct = (total_pnl / total_cost_sum * 100) if total_cost_sum > 0 else 0.0
    total_icon = "🚀" if total_pnl >= 0 else "🔻"

    text += f"💰 *Toplam Maliyet:* `{total_cost_sum:,.2f} TL`\n"
    text += f"📈 *Toplam Değer:* `{total_value_sum:,.2f} TL`\n"
    text += f"{total_icon} *Toplam Kar/Zarar:* `{total_pnl:,.2f} TL` (%{total_pnl_pct:.2f})\n"

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)

async def grafik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    items = db_get_portfolio(user_id)
    if not items:
        await update.message.reply_text("📭 Grafiğini oluşturmak için portföyünüzde en az 1 varlık olmalı.", reply_markup=MAIN_KEYBOARD)
        return

    labels = []
    sizes = []

    for symbol, amount, avg_cost in items:
        cur_price = fetch_price(symbol)
        if cur_price <= 0:
            cur_price = avg_cost
        curr_val = amount * cur_price
        labels.append(symbol)
        sizes.append(curr_val)

    fig, ax = plt.subplots(figsize=(6, 6))
    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2']
    
    ax.pie(
        sizes, 
        labels=labels, 
        autopct='%1.1f%%',
        startangle=140,
        colors=colors[:len(labels)],
        wedgeprops=dict(width=0.4, edgecolor='w')
    )
    ax.set_title("Portföy Varlık Dağılımı", fontsize=14, pad=20)

    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', dpi=150)
    buf.seek(0)
    plt.close()

    await update.message.reply_photo(photo=buf, caption="📊 Portföy Dağılım Grafiğiniz", reply_markup=MAIN_KEYBOARD)

async def fon_ekle_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("➕ Fon eklemek için lütfen şu formatta mesaj gönderin:\n`/ekle <sembol> <adet> <maliyet>`\n\nÖrnek: `/ekle TP2 1000 2.15`", parse_mode="Markdown")

async def fon_sil_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🗑️ Fon silmek için lütfen şu formatta mesaj gönderin:\n`/sil <sembol>`\n\nÖrnek: `/sil TP2`", parse_mode="Markdown")

async def genel_buton_cevap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🛠️ **{update.message.text}** özelliği yakında aktif edilecektir.", parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)

# Otomatik Çökme Bildirimi
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.error(f"Hata oluştu: {context.error}")

# Ana Döngü
def main():
    init_db()
    keep_alive()
    
    while True:
        try:
            logging.info("Bot başlatılıyor...")
            application = ApplicationBuilder().token(BOT_TOKEN).build()
            
            # Slash Komutları
            application.add_handler(CommandHandler("start", start))
            application.add_handler(CommandHandler("yardim", help_cmd))
            application.add_handler(CommandHandler("ekle", ekle))
            application.add_handler(CommandHandler("sil", sil))
            application.add_handler(CommandHandler("portfoy", portfoy))
            application.add_handler(CommandHandler("grafik", grafik))
            
            # Buton Yazı Yakalayıcıları (Text Filters)
            application.add_handler(MessageHandler(filters.Regex(r"^📊 Portföyüm$"), portfoy))
            application.add_handler(MessageHandler(filters.Regex(r"^📈 Grafik$"), grafik))
            application.add_handler(MessageHandler(filters.Regex(r"^➕ Fon Ekle$"), fon_ekle_prompt))
            application.add_handler(MessageHandler(filters.Regex(r"^🗑️ Fon Sil$"), fon_sil_prompt))
            
            # Diğer Butonlar İçin Genel Yakalayıcı
            application.add_handler(MessageHandler(
                filters.Regex(r"^(👀 Takip Listesi|💵 Nakit|📈 Ort\. Performans|🔍 Fon Ara|⏰ Alarmlar|📜 Geçmiş|📄 PDF Raporu|👁️ Tutarları Göster)$"),
                genel_buton_cevap
            ))
            
            application.add_error_handler(error_handler)
            application.run_polling(drop_pending_updates=True)
        except Exception as e:
            logging.error(f"Bot çöktü, 5 saniye içinde yeniden başlatılıyor: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
