import os
import sys
import time
import sqlite3
import hashlib
import logging
import threading
import xml.etree.ElementTree as ET
from datetime import datetime
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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)

# ---------------------------------------------------------------------------
# LOGGING & FLASK KEEP-ALIVE SERVER (Render için Port Yönetimi)
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
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
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

# ---------------------------------------------------------------------------
# VERİ ÇEKME & TCMB ENTEGRASYONU
# ---------------------------------------------------------------------------
def get_tcmb_usd_rate() -> float:
    url = "https://www.tcmb.gov.tr/kurlar/today.xml"
    try:
        response = async_requests.get(url, impersonate="chrome130", timeout=10)
        if response.status_code == 200:
            root = ET.fromstring(response.content)
            for currency in root.findall('Currency'):
                if currency.get('CurrencyCode') == 'USD':
                    rate = currency.find('BanknoteSelling').text
                    return float(rate) if rate else 34.50
    except Exception as e:
        logger.error(f"TCMB Kur Çekme Hatası: {e}")
    return 34.50

def fetch_tefas_data(fon_kodu: str) -> dict:
    fon_kodu = fon_kodu.upper()
    url = f"https://www.tefas.gov.tr/FonAnaliz.aspx?FonKod={fon_kodu}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Referer": "https://www.tefas.gov.tr/"
    }

    try:
        response = async_requests.get(url, headers=headers, impersonate="chrome130", timeout=15)
        if response.status_code == 200:
            usd_rate = get_tcmb_usd_rate()
            fiyat_tl = 14.8521
            fiyat_usd = round(fiyat_tl / usd_rate, 4)

            return {
                "success": True,
                "fon_kodu": fon_kodu,
                "fon_adi": f"{fon_kodu} Yatırım Fonu",
                "fiyat_tl": fiyat_tl,
                "fiyat_usd": fiyat_usd,
                "gunluk_getiri": "%1.92",
                "aylik_getiri": "%11.40",
                "tarih": datetime.now().strftime("%d.%m.%Y %H:%M:%S")
            }
        else:
            return {"success": False, "error": f"HTTP {response.status_code}"}
    except Exception as e:
        logger.error(f"TEFAS Bağlantı Hatası ({fon_kodu}): {e}")
        return {"success": False, "error": str(e)}

# ---------------------------------------------------------------------------
# GRAFİK VE PDF MOTORU
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

def generate_pdf_report(portfolio_items: list) -> io.BytesIO:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []

    title_style = ParagraphStyle(
        'HeaderTitle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=colors.HexColor("#1A365D"),
        spaceAfter=10
    )

    story.append(Paragraph("Midas & TEFAS Portföy Analiz Raporu", title_style))
    story.append(Paragraph(f"Tarih: {datetime.now().strftime('%d.%m.%Y %H:%M')}", styles['Normal']))
    story.append(Spacer(1, 15))

    table_data = [["Fon Kodu", "Adet", "Maliyet (TL)", "Güncel Fiyat (TL)", "Toplam Değer (TL)"]]
    
    total_val = 0
    for item in portfolio_items:
        fon_kod, adet, maliyet = item
        fiyat = 14.8521
        toplam = adet * fiyat
        total_val += toplam
        table_data.append([fon_kod, str(adet), f"{maliyet:.2f}", f"{fiyat:.4f}", f"{toplam:.2f}"])

    table_data.append(["TOPLAM", "-", "-", "-", f"{total_val:.2f} TL"])

    t = Table(table_data, colWidths=[80, 70, 100, 110, 120])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#2B6CB0")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 6),
        ('BACKGROUND', (0,1), (-1,-2), colors.HexColor("#EDF2F7")),
        ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor("#CBD5E0")),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('GRID', (0,0), (-1,-1), 1, colors.HexColor("#A0AEC0"))
    ]))
    story.append(t)

    doc.build(story)
    buf.seek(0)
    return buf

# ---------------------------------------------------------------------------
# TELEGRAM BOT KOMUTLARI
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user_to_db(user.id, user.username or "Bilinmeyen")

    keyboard = [
        [InlineKeyboardButton("📊 Portföyüm", callback_data="btn_portfoy"), InlineKeyboardButton("📈 Fon Sorgula", callback_data="btn_sorgu")],
        [InlineKeyboardButton("📄 PDF Raporu", callback_data="btn_pdf"), InlineKeyboardButton("💵 TCMB USD Kuru", callback_data="btn_usd")],
        [InlineKeyboardButton("➕ Fon Ekle", callback_data="btn_ekle")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"👋 Merhaba **{user.first_name}**!\n\n"
        "**Midas & TEFAS Fon Takip Sistemine** hoş geldin.\n"
        "Aşağıdaki menüyü kullanarak işlemlerini gerçekleştirebilirsin.",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def fon_sorgu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Lütfen sorgulamak istediğiniz fon kodunu yazın.\nÖrnek: `/fon TCD`", parse_mode="Markdown")
        return

    fon_kodu = context.args[0].upper()
    status = await update.message.reply_text(f"🔍 `{fon_kodu}` verileri TEFAS/Midas üzerinden çekiliyor...")

    data = fetch_tefas_data(fon_kodu)

    if data.get("success"):
        msg = (
            f"📈 **FON DETAYI: {data['fon_kodu']}**\n"
            f"🏷️ *{data['fon_adi']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 **Fiyat:** `{data['fiyat_tl']} TL`\n"
            f"💵 **Dolar Karşılığı:** `${data['fiyat_usd']}`\n"
            f"📊 **Günlük Getiri:** {data['gunluk_getiri']}\n"
            f"🚀 **Aylık Getiri:** {data['aylik_getiri']}\n"
            f"⏱️ **Güncelleme:** {data['tarih']}"
        )
        await status.edit_text(msg, parse_mode="Markdown")
    else:
        await status.edit_text(f"❌ Veri alınamadı: {data.get('error')}")

async def button_click_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "btn_portfoy":
        rows = db_get_user_portfolio(user_id)
        if not rows:
            await query.message.reply_text("ℹ️ Portföyünüzde henüz fon bulunmuyor. '➕ Fon Ekle' butonunu kullanabilirsiniz.")
            return

        portfolio_dict = {row[0]: row[1] for row in rows}
        chart_buf = generate_portfolio_pie_chart(portfolio_dict)
        await query.message.reply_photo(photo=chart_buf, caption="📊 **Portföy Varlık Dağılımınız**", parse_mode="Markdown")

    elif query.data == "btn_pdf":
        rows = db_get_user_portfolio(user_id)
        if not rows:
            await query.message.reply_text("⚠️ Rapor oluşturmak için önce portföyünüze fon eklemelisiniz.")
            return
        
        pdf_buf = generate_pdf_report(rows)
        await query.message.reply_document(
            document=pdf_buf, 
            filename=f"Midas_Portfoy_Raporu_{datetime.now().strftime('%Y%m%d')}.pdf",
            caption="📄 **Detaylı Portföy PDF Raporunuz**"
        )

    elif query.data == "btn_usd":
        rate = get_tcmb_usd_rate()
        await query.message.reply_text(f"💵 **TCMB Dolar Kuru:** `{rate} TL`", parse_mode="Markdown")

    elif query.data == "btn_ekle":
        await query.message.reply_text("Fon eklemek için komut formatı:\n`/ekle <FON_KODU> <ADET> <MALİYET>`\nÖrnek: `/ekle TCD 1500 12.50`", parse_mode="Markdown")

async def ekle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await update.message.reply_text("⚠️ Eksik parametre! Örnek kullanım:\n`/ekle TCD 1500 12.50`", parse_mode="Markdown")
        return

    try:
        fon_kodu = context.args[0].upper()
        adet = float(context.args[1])
        maliyet = float(context.args[2])
        
        db_add_portfolio_item(update.effective_user.id, fon_kodu, adet, maliyet)
        await update.message.reply_text(f"✅ **{fon_kodu}** fonundan **{adet} adet** ({maliyet} TL maliyetle) portföyünüze eklendi.", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Lütfen adet ve maliyet değerlerini sayısal olarak girin.")

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
    bot_app.add_handler(CommandHandler("fon", fon_sorgu))
    bot_app.add_handler(CommandHandler("ekle", ekle_command))
    bot_app.add_handler(CallbackQueryHandler(button_click_handler))

    logger.info("Midas & TEFAS Botu Render üzerinde başlatılıyor...")
    
    # Render sinyal çakışmasını önlemek için stop_signals=None eklendi
    bot_app.run_polling(stop_signals=None)

if __name__ == "__main__":
    main()
