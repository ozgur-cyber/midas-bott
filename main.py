import os
import logging
import asyncio
import aiohttp
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Logging ayarları
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.tefas.gov.tr/FonAnaliz.aspx",
    "Accept": "application/json, text/plain, */*"
}

async def fetch_tefas_direct(session, code: str):
    """1. Adım: Doğrudan TEFAS API Sorgusu"""
    url = "https://www.tefas.gov.tr/api/funds/fonGnlBlgSiraliGetir"
    try:
        async with session.post(url, json={"kind": "YAT"}, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                data = await resp.json()
                for item in data:
                    item_code = item.get("FONKOD") or item.get("fon_kod") or item.get("fund_code")
                    if item_code and str(item_code).strip().upper() == code:
                        return {
                            "code": code,
                            "title": item.get("FONUNVAN") or item.get("fon_unvan") or code,
                            "price": item.get("FIYAT") or item.get("fiyat") or "N/A",
                            "daily_return": item.get("GUNLUKGETIRI") or item.get("gunluk_getiri") or 0.0
                        }
    except Exception as e:
        logger.warning(f"TEFAS Direct Error: {e}")
    return None

async def fetch_tefas_proxy(session, code: str):
    """2. Adım: Render Yurt Dışı IP Engeline Karşı Proxy Üzerinden TEFAS Sorgusu"""
    url = "https://corsproxy.io/?" + "https://www.tefas.gov.tr/api/funds/fonGnlBlgSiraliGetir"
    try:
        async with session.post(url, json={"kind": "YAT"}, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=7)) as resp:
            if resp.status == 200:
                data = await resp.json()
                for item in data:
                    item_code = item.get("FONKOD") or item.get("fon_kod") or item.get("fund_code")
                    if item_code and str(item_code).strip().upper() == code:
                        return {
                            "code": code,
                            "title": item.get("FONUNVAN") or item.get("fon_unvan") or code,
                            "price": item.get("FIYAT") or item.get("fiyat") or "N/A",
                            "daily_return": item.get("GUNLUKGETIRI") or item.get("gunluk_getiri") or 0.0
                        }
    except Exception as e:
        logger.warning(f"TEFAS Proxy Error: {e}")
    return None

async def fetch_fonbul_fallback(session, code: str):
    """3. Adım: Alternatif Kaynak Scraping"""
    url = f"https://www.fonbul.com/FonBulPlus/YatirimFonlari/FonProfilleri/FonAnaliz/{code}"
    try:
        async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=6)) as resp:
            if resp.status == 200:
                html = await resp.text()
                soup = BeautifulSoup(html, "html.parser")
                
                title_elem = soup.find("h1") or soup.find("title")
                title = title_elem.text.strip() if title_elem else code
                
                price_elem = soup.select_one(".fon-fiyat, .price, .fiyat")
                price = price_elem.text.strip() if price_elem else "Bulunamadı"
                
                return {
                    "code": code,
                    "title": title,
                    "price": price,
                    "daily_return": "N/A"
                }
    except Exception as e:
        logger.warning(f"Fonbul Fallback Error: {e}")
    return None

async def get_fund_data(code: str):
    code = code.strip().upper()
    async with aiohttp.ClientSession() as session:
        # 1. Doğrudan TEFAS
        res = await fetch_tefas_direct(session, code)
        if res:
            return res
            
        # 2. Proxy Üzerinden TEFAS (Render IP Engelini Aşar)
        res = await fetch_tefas_proxy(session, code)
        if res:
            return res
            
        # 3. Alternatif Kaynak
        res = await fetch_fonbul_fallback(session, code)
        if res:
            return res
            
    return None

def format_fund_message(data: dict) -> str:
    code = data.get("code")
    title = data.get("title")
    price = data.get("price")
    daily = data.get("daily_return")
    
    if isinstance(price, (int, float)):
        price_str = f"{price:.6f} TL"
    else:
        price_str = f"{price} TL" if "TL" not in str(price) else str(price)
        
    if isinstance(daily, (int, float)):
        daily_str = f"%{daily:+.2f}"
    else:
        daily_str = str(daily)

    msg = (
        f"📊 *Fon Detayı: {code}*\n\n"
        f"🔹 *Fon Adı:* {title}\n"
        f"💵 *Birim Fiyat:* `{price_str}`\n"
        f"📈 *Günlük Getiri:* `{daily_str}`\n"
    )
    return msg

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 *Midas Portföy Takip Botuna Hoş Geldiniz!*\n\n"
        "Fon sorgulamak için komutu şu şekilde kullanabilirsiniz:\n"
        "`/fon AAL` veya `/fon aal`"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def fon_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Lütfen bir fon kodu girin.\nÖrnek: `/fon AAL`", parse_mode="Markdown")
        return
        
    raw_code = context.args[0]
    clean_code = raw_code.strip().upper()
    
    status_msg = await update.message.reply_text(f"🔍 `{clean_code}` fonu sorgulanıyor...", parse_mode="Markdown")
    
    data = await get_fund_data(clean_code)
    
    if data and data.get("price") != "Bulunamadı":
        reply_text = format_fund_message(data)
        await status_msg.edit_text(reply_text, parse_mode="Markdown")
    else:
        await status_msg.edit_text(
            f"❌ *Veri alınamadı:* `{clean_code}` TEFAS veritabanında bulunamadı veya sunuculardan yanıt alınamadı.",
            parse_mode="Markdown"
        )

def main():
    token = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    if not token:
        logger.error("HATA: BOT_TOKEN çevre değişkeni (Environment Variable) tanımlı değil!")
        return

    app = ApplicationBuilder().token(token).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(CommandHandler("fon", fon_command))
    
    logger.info("Bot çalışıyor...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
