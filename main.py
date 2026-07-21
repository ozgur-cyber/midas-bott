import os
import sys
import re
import time
import sqlite3
import logging
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import io

from flask import Flask
from curl_cffi import requests as async_requests

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters
)

# ---------------------------------------------------------------------------
# LOGGING & FLASK KEEP-ALIVE SERVER
# ---------------------------------------------------------------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("MidasBot")

app = Flask(__name__)

@app.route('/')
def home():
    return "Midas & TEFAS Fon Takip Botu 7/24 Aktif", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ---------------------------------------------------------------------------
# VERİTABANI YÖNETİMİ
# ---------------------------------------------------------------------------
DB_NAME = "midas_bot.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            nakit_tl REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            fon_kodu TEXT,
            adet REAL,
            maliyet REAL
        )
    ''')
    conn.commit()
    conn.close()

def add_user_to_db(user_id: int, username: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, nakit_tl) VALUES (?, ?, 0)", (user_id, username))
    conn.commit()
    conn.close()

def db_add_portfolio_item(user_id: int, fon_kodu: str, adet: float, maliyet: float):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO portfolio (user_id, fon_kodu, adet, maliyet) VALUES (?, ?, ?, ?)",
                   (user_id, fon_kodu.upper(), adet, maliyet))
    conn.commit()
    conn.close()

def db_get_user_portfolio(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT fon_kodu, adet, maliyet FROM portfolio WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def db_get_nakit(user_id: int) -> float:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT nakit_tl FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row and row[0] else 0.0

def db_set_nakit(user_id: int, miktar: float):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET nakit_tl = ? WHERE user_id = ?", (miktar, user_id))
    conn.commit()
    conn.close()

# ---------------------------------------------------------------------------
# RESMİ TEFAS JSON API PARSER & TCMB KUR ENTEGRASYONU
# ---------------------------------------------------------------------------
def get_tcmb_usd_rate() -> float:
    url = "https://www.tcmb.gov.tr/kurlar/today.xml"
    try:
        response = async_requests.get(url, impersonate="chrome", timeout=10)
        if response.status_code == 200:
            root = ET.fromstring(response.content)
            for currency in root.findall('Currency'):
                if currency.get('CurrencyCode') == 'USD':
                    rate = currency.find('BanknoteSelling').text
                    return float(rate) if rate else 34.50
    except Exception as e:
        logger.error(f"TCMB Kur Hatası: {e}")
    return 34.50

def fetch_tefas_data(fon_kodu: str) -> dict:
    fon_kodu = fon_kodu.upper()
    url = "https://www.tefas.gov.tr/api/DB/BindHistoryInfo"
    
    # Hafta sonu veya tatillere takılmamak için son 7 günlük pencere sorgulanır
    end_date = datetime.now()
    start_date = end_date - timedelta(days=7)

    payload = {
        "fontip": "YAT",
        "sorgutipi": "1",
        "bastarih": start_date.strftime("%d.%m.%Y"),
        "bittarih": end_date.strftime("%d.%m.%Y"),
        "fonkod": fon_kodu
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://www.tefas.gov.tr",
        "Referer": "https://www.tefas.gov.tr/FonAnaliz.aspx"
    }

    try:
        response = async_requests.post(url, data=payload, headers=headers, impersonate="chrome", timeout=15)
        if response.status_code == 200:
            res_json = response.json()
            data_list = res_json.get("data", [])

            if data_list:
                # En güncel kayıt listenin son elemanıdır
                latest = data_list[-1]

                fon_adi = latest.get("FONUNVAN", f"{fon_kodu} Yatırım Fonu")
                fiyat_tl = float(latest.get("FIYAT", 0.0))

                # Günlük değişim hesaplama
                gunluk_getiri = "%0.00"
                if len(data_list) >= 2:
                    prev_fiyat = float(data_list[-2].get("FIYAT", fiyat_tl))
                    if prev_fiyat > 0:
                        change = ((fiyat_tl - prev_fiyat) / prev_fiyat) * 100
                        gunluk_getiri = f"%{change:+.2f}"

                usd_rate = get_tcmb_usd_rate()
                fiyat_usd = round(fiyat_tl / usd_rate, 6) if usd_rate > 0 else 0.0

                return {
                    "success": True,
                    "fon_kodu": fon_kodu,
                    "fon_adi": fon_adi,
                    "fiyat_tl": fiyat_tl,
                    "fiyat_usd": fiyat_usd,
                    "gunluk_getiri": gunluk_getiri,
                    "aylik_getiri": "%--",
                    "tarih": datetime.now().strftime("%d.%m.%Y %H:%M:%S")
                }
            else:
                return {"success": False, "error": f"'{fon_kodu}' TEFAS veritabanında bulunamadı."}
        else:
            return {"success": False, "error": f"TEFAS API Yanıt Vermedi (HTTP {response.status_code})"}
    except Exception as e:
        logger.error(f"TEFAS API Hatası ({fon_kodu}): {e}")
        return {"success": False, "error": "TEFAS bağlantı hatası."}

# ---------------------------------------------------------------------------
# PORTFÖY ÖZETİ TASARIMI
# ---------------------------------------------------------------------------
def build_portfolio_summary_text(user_id: int) -> str:
    rows = db_get_user_portfolio(user_id)
    nakit_tl = db_get_nakit(user_id)
    usd_rate = get_tcmb_usd_rate()

    if not rows and nakit_tl == 0:
        return "ℹ️ Portföyünüzde henüz kayıtlı fon veya nakit bulunmuyor.\nEklenti yapmak için `/ekle <KOD> <ADET> <MALİYET>` yazabilirsiniz."

    lines = [f"💼 **PORTFÖY ÖZETİ** (1 $ = {usd_rate:.2f} TL)", "───────────────────────────"]
    
    toplam_portfoy_tl = nakit_tl
    toplam_maliyet_tl = 0.0

    for fon_kod, adet, maliyet in rows:
        data = fetch_tefas_data(fon_kod)
        guncel_fiyat = data.get("fiyat_tl", maliyet) if data.get("success") else maliyet
        
        toplam_val_tl = adet * guncel_fiyat
        toplam_val_usd = toplam_val_tl / usd_rate
        maliyet_val_tl = adet * maliyet

        toplam_portfoy_tl += toplam_val_tl
        toplam_maliyet_tl += maliyet_val_tl

        kar_zarar_tl = toplam_val_tl - maliyet_val_tl
        kar_zarar_yuzde = ((guncel_fiyat - maliyet) / maliyet * 100) if maliyet > 0 else 0.0
        emoji = "🟢" if kar_zarar_tl >= 0 else "🔴"

        lines.append(f"🔹 **{fon_kod}**: {toplam_val_tl:.2f} TL | ${toplam_val_usd:.2f} ({emoji} {kar_zarar_yuzde:+.2f}%)")

    lines.append("───────────────────────────")
    nakit_usd = nakit_tl / usd_rate
    toplam_portfoy_usd = toplam_portfoy_tl / usd_rate

    toplam_kz_tl = (toplam_portfoy_tl - nakit_tl) - toplam_maliyet_tl
    toplam_kz_usd = toplam_kz_tl / usd_rate
    toplam_kz_yuzde = (toplam_kz_tl / toplam_maliyet_tl * 100) if toplam_maliyet_tl > 0 else 0.0

    lines.append(f"💵 **Nakit:** {nakit_tl:.0f} TL (${nakit_usd:.2f})")
    lines.append(f"📊 **Toplam Portföy:** {toplam_portfoy_tl:.2f} TL (${toplam_portfoy_usd:.2f})")
    lines.append(f"🚀 **K/Z:** {toplam_kz_tl:.2f} TL / ${toplam_kz_usd:.2f} (%{toplam_kz_yuzde:.2f})")

    return "\n".join(lines)

# ---------------------------------------------------------------------------
# GRAFİK MOTORU
# ---------------------------------------------------------------------------
def generate_portfolio_pie_chart(portfolio_data: dict) -> io.BytesIO:
    labels = list(portfolio_data.keys())
    sizes = list(portfolio_data.values())
    colors_list = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd']

    fig, ax = plt.subplots(figsize=(6, 6))
    wedges, texts, autotexts = ax.pie(
        sizes, 
        labels=labels, 
        autopct='%1.1f%%',
        startangle=140,
        colors=colors_list[:len(labels)],
        textprops=dict(color="w", weight="bold")
    )
    ax.legend(wedges, labels, title="Fonlar", loc="center left", bbox_to_anchor=(1, 0, 0.5, 1))
    plt.setp(autotexts, size=9, weight="bold")
    plt.title("Portföy Varlık Dağılımı", fontsize=13, pad=15)

    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', dpi=150)
    buf.seek(0)
    plt.close(fig)
    return buf

# ---------------------------------------------------------------------------
# BOT KOMUTLARI VE BUTON YÖNETİMİ
# ---------------------------------------------------------------------------
def get_main_keyboard():
    keyboard = [
        ["📊 Portföyüm", "📈 Grafik"],
        ["👀 Takip Listesi", "🔍 Fon Ara"],
        ["➕ Fon Ekle", "🗑️ Fon Sil"],
        ["💵 Nakit", "📈 Ort. Performans"],
        ["📜 Geçmiş", "📄 PDF Raporu"],
        ["⚙️ Ayarlar"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user_to_db(user.id, user.username or "Bilinmeyen")

    msg = (
        f"👋 Merhaba **{user.first_name}**!\n\n"
        "**Midas & TEFAS Fon Takip Sistemine** hoş geldin.\n"
        "Aşağıdaki menüyü kullanarak işlemlerini gerçekleştirebilirsin."
    )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_main_keyboard())

async def portfoy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text_summary = build_portfolio_summary_text(update.effective_user.id)
    await update.message.reply_text(text_summary, parse_mode="Markdown")

async def fon_sorgu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Lütfen sorgulamak istediğiniz fon kodunu yazın.\nÖrnek: `/fon TP2`", parse_mode="Markdown")
        return

    fon_kodu = context.args[0].upper()
    data = fetch_tefas_data(fon_kodu)

    if data.get("success"):
        msg = (
            f"📈 **FON DETAYI: {data['fon_kodu']}**\n"
            f"🏷️ *{data['fon_adi']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 **Fiyat:** `{data['fiyat_tl']} TL`\n"
            f"💵 **Dolar Karşılığı:** `${data['fiyat_usd']}`\n"
            f"📊 **Günlük Getiri:** {data['gunluk_getiri']}\n"
            f"⏱️ **Güncelleme:** {data['tarih']}"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Veri alınamadı: {data.get('error')}")

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id

    if "Portföyüm" in text:
        text_summary = build_portfolio_summary_text(user_id)
        await update.message.reply_text(text_summary, parse_mode="Markdown")

    elif "Grafik" in text:
        rows = db_get_user_portfolio(user_id)
        if not rows:
            await update.message.reply_text("ℹ️ Grafik oluşturmak için önce portföyünüze fon eklemelisiniz.")
            return
        portfolio_dict = {row[0]: row[1] for row in rows}
        chart_buf = generate_portfolio_pie_chart(portfolio_dict)
        await update.message.reply_photo(photo=chart_buf, caption="📊 **Portföy Varlık Dağılımınız**", parse_mode="Markdown")

    elif "Nakit" in text:
        nakit = db_get_nakit(user_id)
        usd = nakit / get_tcmb_usd_rate()
        await update.message.reply_text(f"💵 **Mevcut Nakit:** {nakit:.2f} TL (${usd:.2f})\n\nGüncellemek için: `/nakit <MIKTAR>`", parse_mode="Markdown")

    elif "Fon Ara" in text:
        await update.message.reply_text("🔍 Fon aramak için: `/fon <KOD>` (Örn: `/fon TP2`)", parse_mode="Markdown")

    elif "Fon Ekle" in text:
        await update.message.reply_text("➕ Fon eklemek için: `/ekle <KOD> <ADET> <MALİYET>`\nÖrnek: `/ekle TP2 100 2.084`", parse_mode="Markdown")

    elif "Fon Sil" in text:
        await update.message.reply_text("🗑️ Fon silmek için: `/sil <KOD>`", parse_mode="Markdown")

    else:
        await update.message.reply_text("ℹ️ Komut anlaşılamadı. Menü tuşlarını kullanabilirsiniz.", reply_markup=get_main_keyboard())

async def ekle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await update.message.reply_text("⚠️ Örnek kullanım:\n`/ekle TP2 100 2.084`", parse_mode="Markdown")
        return

    try:
        fon_kodu = context.args[0].upper()
        adet = float(context.args[1])
        maliyet = float(context.args[2])
        
        db_add_portfolio_item(update.effective_user.id, fon_kodu, adet, maliyet)
        await update.message.reply_text(f"✅ **{fon_kodu}** fonundan **{adet} adet** ({maliyet} TL maliyetle) portföyünüze eklendi.", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Adet ve maliyet değerlerini sayısal girin.")

# ---------------------------------------------------------------------------
# ANA UYGULAMA BAŞLATICI
# ---------------------------------------------------------------------------
def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        print("❌ HATA: BOT_TOKEN ortam değişkeni eksik!")
        sys.exit(1)

    init_db()

    # Flask Sunucusunu Arka Planda Başlat
    threading.Thread(target=run_flask, daemon=True).start()

    # Telegram Bot Yapılandırması
    bot_app = ApplicationBuilder().token(token).build()

    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("portfoy", portfoy_command))
    bot_app.add_handler(CommandHandler("fon", fon_sorgu))
    bot_app.add_handler(CommandHandler("ekle", ekle_command))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

    logger.info("Midas & TEFAS Botu başlatılıyor...")
    bot_app.run_polling(stop_signals=None)

if __name__ == "__main__":
    main()
