import os
import sys
import logging
import threading
from datetime import datetime, timedelta
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
# TEFAS API ENGINE (GÜNCEL TARİH VE POST PAYLOAD İLE)
# ---------------------------------------------------------------------------
def fetch_tefas_fund(fon_kodu: str) -> dict:
    fon_kodu = fon_kodu.upper().strip()
    
    # TEFAS API hafta sonu veya tatillerde veri vermediği için son 5 günü sorguluyoruz
    today = datetime.now()
    start_date = (today - timedelta(days=5)).strftime("%d.%m.%Y")
    end_date = today.strftime("%d.%m.%Y")

    url = "https://www.tefas.gov.tr/api/DB/BindHistoryInfo"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://www.tefas.gov.tr",
        "Referer": "https://www.tefas.gov.tr/TarihselVeriler.aspx"
    }
    
    payload = {
        "fontip": "YAT",
        "fonkod": fon_kodu,
        "bastarih": start_date,
        "bittarih": end_date
    }

    try:
        response = requests.post(url, data=payload, headers=headers, timeout=10)
        
        if response.status_code == 200:
            json_resp = response.json()
            data_list = json_resp.get("data", [])
            
            if data_list and len(data_list) > 0:
                # En son günün verisini alıyoruz
                latest_data = data_list[-1]
                
                fiyat = float(latest_data.get("FIYAT", 0.0))
                unvan = latest_data.get("FONUNVAN", f"{fon_kodu} Fonu")
                tarih_str = latest_data.get("TARIH_STR", end_date)
                
                # Eğer listede birden fazla gün varsa günlük değişimi hesaplayalım
                gunluk_getiri = "%0.00"
                if len(data_list) >= 2:
                    prev_fiyat = float(data_list[-2].get("FIYAT", 0.0))
                    if prev_fiyat > 0:
                        change = ((fiyat - prev_fiyat) / prev_fiyat) * 100
                        gunluk_getiri = f"%{change:+.2f}"

                return {
                    "success": True,
                    "fon_kodu": fon_kodu,
                    "fon_adi": unvan,
                    "fiyat": fiyat,
                    "tarih": tarih_str,
                    "gunluk_getiri": gunluk_getiri
                }
    except Exception as e:
        logger.error(f"TEFAS API Istek Hatasi ({fon_kodu}): {e}")

    return {"success": False, "error": f"'{fon_kodu}' TEFAS veritabanında bulunamadı veya sunucudan yanıt alınamadı."}

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
    status_msg = await update.message.reply_text(f"🔍 `{fon_kodu}` fonu TEFAS'tan sorgulanıyor...", parse_mode="Markdown")

    data = fetch_tefas_fund(fon_kodu)

    if data.get("success"):
        reply = (
            f"📊 **FON DETAYI: {data['fon_kodu']}**\n"
            f"🏷️ *{data['fon_adi']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 **Birim Fiyat:** `{data['fiyat']:.6f} TL`\n"
            f"📈 **Son Getiri:** `{data['gunluk_getiri']}`\n"
            f"📅 **Son Veri Tarihi:** `{data['tarih']}`\n"
            f"📡 **Kaynak:** `TEFAS Resmî API`"
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

    # Web Server'ı arka planda başlat
    threading.Thread(target=run_flask, daemon=True).start()

    # Telegram Bot
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(CommandHandler("fon", fon_command))

    logger.info("Bot başlatılıyor...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
