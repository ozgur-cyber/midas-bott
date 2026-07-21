import os
import sys
import re
import logging
import threading
from datetime import datetime
from flask import Flask
import requests

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes
)

# ---------------------------------------------------------------------------
# LOGGING & RENDER KEEP-ALIVE SERVER
# ---------------------------------------------------------------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("MidasBot")

app = Flask(__name__)

@app.route('/')
def home():
    return "Midas TEFAS Botu Aktif", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ---------------------------------------------------------------------------
# FON VERİ ÇEKİCİ (İŞ YATIRIM + ALTERNATİF FALLBACK)
# ---------------------------------------------------------------------------
def fetch_fund_data(fon_kodu: str) -> dict:
    fon_kodu = fon_kodu.upper().strip()

    # 1. KATMAN: İŞ YATIRIM (Render Yurt Dışı IP Engeline Takılmaz)
    try:
        url = f"https://www.isyatirim.com.tr/tr-tr/analiz/fonlar/Sayfalar/fon-detay.aspx?fonk={fon_kodu}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"
        }
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            html = res.text
            
            # Fiyat ve Unvan regex aramaları
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
                    "kaynak": "İş Yatırım Altyapısı"
                }
    except Exception as e:
        logger.warning(f"İş Yatırım Hatası ({fon_kodu}): {e}")

    # 2. KATMAN: TEFAS YEDEK
    try:
        url = "https://www.tefas.gov.tr/api/DB/BindHistoryInfo"
        payload = {
            "fontip": "YAT",
            "fonkod": fon_kodu,
            "bastarih": datetime.now().strftime("%d.%m.%Y"),
            "bittarih": datetime.now().strftime("%d.%m.%Y")
        }
        res = requests.post(url, data=payload, timeout=5)
        if res.status_code == 200:
            data = res.json().get("data", [])
            if data:
                latest = data[-1]
                return {
                    "success": True,
                    "fon_kodu": fon_kodu,
                    "fon_adi": latest.get("FONUNVAN", f"{fon_kodu} Fonu"),
                    "fiyat": float(latest.get("FIYAT", 0.0)),
                    "kaynak": "TEFAS API"
                }
    except Exception as e:
        logger.warning(f"TEFAS API Hatası ({fon_kodu}): {e}")

    return {"success": False, "error": f"'{fon_kodu}' fonu bulunamadı veya veri çekilemedi."}

# ---------------------------------------------------------------------------
# TELEGRAM BOT HANDLERS
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
        await update.message.reply_text("⚠️ Lütfen bir fon kodu girin.\nÖrnek: `/fon AAL`", parse_mode="Markdown")
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
            f"📡 **Kaynak:** `{data['kaynak']}`"
        )
        await status_msg.edit_text(reply, parse_mode="Markdown")
    else:
        await status_msg.edit_text(f"❌ Veri alınamadı: {data.get('error')}", parse_mode="Markdown")

# ---------------------------------------------------------------------------
# MAIN EXECUTION
# ---------------------------------------------------------------------------
def main():
    token = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")
    if not token:
        logger.error("HATA: BOT_TOKEN çevre değişkeni bulunamadı!")
        sys.exit(1)

    threading.Thread(target=run_flask, daemon=True).start()

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(CommandHandler("fon", fon_command))

    logger.info("Bot çalışıyor...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
