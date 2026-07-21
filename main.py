import os
import sys
import re
import json
import logging
import threading
from datetime import datetime
from flask import Flask

from curl_cffi import requests as async_requests
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters
)

# ---------------------------------------------------------------------------
# LOGGING & KEEP-ALIVE SERVER (RENDER 7/24)
# ---------------------------------------------------------------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("MidasBot")

app = Flask(__name__)

@app.route('/')
def home():
    return "Midas & TEFAS Fon Botu Aktif", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ---------------------------------------------------------------------------
# FON VERİ ÇEKİCİ (FINTABLES + İŞ YATIRIM + TEFAS ENGINE)
# ---------------------------------------------------------------------------
def fetch_fund_data(fon_kodu: str) -> dict:
    fon_kodu = fon_kodu.upper().strip()

    # 1. KATMAN: FINTABLES (Yurt Dışı IP Engeli Yoktur, AAL Dâhil Bütün Fonlar Vardır)
    try:
        url = f"https://fintables.com/fonlar/{fon_kodu}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        res = async_requests.get(url, headers=headers, timeout=10, impersonate="chrome")
        if res.status_code == 200:
            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', res.text, re.DOTALL)
            if match:
                json_data = json.loads(match.group(1))
                page_props = json_data.get("props", {}).get("pageProps", {})
                fund_info = page_props.get("fund", {}) or page_props.get("data", {})
                
                if fund_info:
                    fiyat = float(fund_info.get("price") or fund_info.get("latest_price") or 0.0)
                    fon_adi = fund_info.get("title") or fund_info.get("name") or f"{fon_kodu} Fonu"
                    daily_ret = fund_info.get("daily_return") or fund_info.get("day_return") or 0.0
                    
                    if fiyat > 0:
                        return {
                            "success": True,
                            "fon_kodu": fon_kodu,
                            "fon_adi": fon_adi,
                            "fiyat": fiyat,
                            "gunluk_getiri": f"%{float(daily_ret):+.2f}",
                            "kaynak": "Fintables"
                        }
    except Exception as e:
        logger.warning(f"Fintables Hatası ({fon_kodu}): {e}")

    # 2. KATMAN: İŞ YATIRIM (Yedek Kaynak)
    try:
        iy_url = f"https://www.isyatirim.com.tr/tr-tr/analiz/fonlar/Sayfalar/fon-detay.aspx?fonk={fon_kodu}"
        res = async_requests.get(iy_url, timeout=10, impersonate="chrome")
        if res.status_code == 200:
            html = res.text
            price_match = re.search(r'Son Fiyat[^\d]*([0-9.,]+)', html, re.IGNORECASE)
            title_match = re.search(r'<h1[^>]*>\s*([^<]+)\s*</h1>', html)
            
            if price_match:
                raw_p = price_match.group(1).replace(".", "").replace(",", ".")
                fiyat = float(raw_p)
                fon_adi = title_match.group(1).strip() if title_match else f"{fon_kodu} Fonu"
                return {
                    "success": True,
                    "fon_kodu": fon_kodu,
                    "fon_adi": fon_adi,
                    "fiyat": fiyat,
                    "gunluk_getiri": "%0.00",
                    "kaynak": "İş Yatırım"
                }
    except Exception as e:
        logger.warning(f"İş Yatırım Hatası ({fon_kodu}): {e}")

    return {"success": False, "error": f"'{fon_kodu}' kodu hiçbir veritabanında bulunamadı."}

# ---------------------------------------------------------------------------
# TELEGRAM BOT KOMUTLARI
# ---------------------------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 **Midas & TEFAS Fon Takip Botuna Hoş Geldiniz!**\n\n"
        "Fon fiyatlarını anlık sorgulamak için:\n"
        "`/fon AAL` veya `/fon aal` yazabilirsiniz."
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def fon_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Lütfen sorgulamak istediğiniz fon kodunu girin.\nÖrnek: `/fon AAL`", parse_mode="Markdown")
        return

    fon_kodu = context.args[0].strip().upper()
    status_msg = await update.message.reply_text(f"🔍 `{fon_kodu}` fonu sorgulanıyor...", parse_mode="Markdown")

    data = fetch_fund_data(fon_kodu)

    if data.get("success"):
        reply = (
            f"📊 **FON DETAYI: {data['fon_kodu']}**\n"
            f"🏷️ *{data['fon_adi']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 **Birim Fiyat:** `{data['fiyat']:.6f} TL`\n"
            f"📈 **Günlük Getiri:** `{data['gunluk_getiri']}`\n"
            f"📡 **Kaynak:** `{data['kaynak']}`"
        )
        await status_msg.edit_text(reply, parse_mode="Markdown")
    else:
        await status_msg.edit_text(f"❌ Veri alınamadı: {data.get('error')}", parse_mode="Markdown")

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    token = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")
    if not token:
        logger.error("HATA: BOT_TOKEN bulunamadı!")
        sys.exit(1)

    # Flask Sunucusunu Arka Planda Başlat (Render Port İhtiyacı İçin)
    threading.Thread(target=run_flask, daemon=True).start()

    # Telegram Bot
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(CommandHandler("fon", fon_command))

    logger.info("Bot başlatıldı...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
