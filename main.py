import os
import sys
import re
import logging
import threading
from flask import Flask
import requests
from bs4 import BeautifulSoup

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
# FON VERİ ÇEKİCİ (GOOGLE FINANCE + FONBUL + TEFAS ENGINE)
# ---------------------------------------------------------------------------
def fetch_fund_data(fon_kodu: str) -> dict:
    fon_kodu = fon_kodu.upper().strip()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"
    }

    # 1. YÖNTEM: GOOGLE FINANCE (Yurt Dışı IP Engeli Kesinlikle Yoktur)
    try:
        url = f"https://www.google.com/finance/quote/{fon_kodu}:MUTUALFUND_TR"
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            price_div = soup.find("div", {"class": "YMlBx"}) or soup.find("div", {"class": "fxHfh"})
            title_div = soup.find("div", {"class": "zz2A2"}) or soup.find("h1")

            if price_div:
                raw_price = price_div.text.replace("₺", "").replace(".", "").replace(",", ".").strip()
                # Temiz sayı ayıklama
                p_match = re.search(r'([0-9]+\.[0-9]+)', raw_price)
                if p_match:
                    fiyat = float(p_match.group(1))
                    fon_adi = title_div.text.strip() if title_div else f"{fon_kodu} Fonu"
                    return {
                        "success": True,
                        "fon_kodu": fon_kodu,
                        "fon_adi": fon_adi,
                        "fiyat": fiyat,
                        "kaynak": "Google Finance"
                    }
    except Exception as e:
        logger.warning(f"Google Finance Hatası ({fon_kodu}): {e}")

    # 2. YÖNTEM: FONBUL (Yedek Kaynak)
    try:
        url = f"https://www.fonbul.com/FonBulPlus/YatirimFonlari/FonProfilleri/FonAnaliz/{fon_kodu}"
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            
            # Fiyat ve Unvan parse
            price_elem = soup.find(id="ContentPlaceHolder1_lblFiyat") or soup.find("span", string=re.compile(r'Fiyat'))
            title_elem = soup.find("h1") or soup.find("title")

            if price_elem:
                price_text = price_elem.text.replace(".", "").replace(",", ".").strip()
                p_match = re.search(r'([0-9]+\.[0-9]+)', price_text)
                if p_match:
                    fiyat = float(p_match.group(1))
                    fon_adi = title_elem.text.strip() if title_elem else f"{fon_kodu} Fonu"
                    return {
                        "success": True,
                        "fon_kodu": fon_kodu,
                        "fon_adi": fon_adi,
                        "fiyat": fiyat,
                        "kaynak": "Fonbul Altyapısı"
                    }
    except Exception as e:
        logger.warning(f"Fonbul Hatası ({fon_kodu}): {e}")

    # 3. YÖNTEM: TEFAS GENEL LISTE (FALLBACK)
    try:
        url = "https://www.tefas.gov.tr/api/funds/fonGnlBlgSiraliGetir"
        res = requests.post(url, json={"kind": "YAT"}, headers=headers, timeout=5)
        if res.status_code == 200:
            for item in res.json():
                code = item.get("FONKOD") or item.get("fon_kod") or item.get("fund_code")
                if code and str(code).strip().upper() == fon_kodu:
                    fiyat = float(item.get("FIYAT") or item.get("fiyat") or 0.0)
                    fon_adi = item.get("FONUNVAN") or item.get("fon_unvan") or f"{fon_kodu} Fonu"
                    return {
                        "success": True,
                        "fon_kodu": fon_kodu,
                        "fon_adi": fon_adi,
                        "fiyat": fiyat,
                        "kaynak": "TEFAS API"
                    }
    except Exception as e:
        logger.warning(f"TEFAS Direct Hatası ({fon_kodu}): {e}")

    return {"success": False, "error": f"'{fon_kodu}' fonu veritabanlarında bulunamadı."}

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
