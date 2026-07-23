import logging
import os
import re
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import numpy as np
from datetime import datetime, timedelta
from pytz import timezone
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from PIL import Image
import pytesseract

from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, 
    ContextTypes, ConversationHandler, filters
)
from tefas_service import tefas_service

class RenderHealthCheck(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot 7/24 Aktif!")

def start_render_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), RenderHealthCheck)
    server.serve_forever()

threading.Thread(target=start_render_server, daemon=True).start()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

BOT_TOKEN = "8758082460:AAF5LTiUUu19WxROr-JnDt7e8FLrBWqKSbo"
TURKEY_TZ = timezone("Europe/Istanbul")

TURKISH_MONTHS = {
    1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran",
    7: "Temmuz", 8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık"
}

ADD_CODE, ADD_UNITS, ADD_COST = range(3)
SELL_CODE, SELL_UNITS = range(3, 5)

DEFAULT_PORTFOLIO = {
    "AAL": {"units": 300, "unit_cost": 3.41},
    "AC4": {"units": 258, "unit_cost": 3.865039},
    "PRY": {"units": 657, "unit_cost": 3.043957},
    "TP2": {"units": 16,  "unit_cost": 2.082500}
}

DEFAULT_SUMMARY_HOUR = "15:30"
DEFAULT_TAX_RATE = 17.5

UPDATED_FUNDS_TODAY = {}
LAST_CHECK_DATE = {}
SUMMARY_SENT_TODAY = {}

EXCLUDED_WORDS = {"YTD", "USD", "TRY", "TL", "SAT", "BUY", "GIZ", "VER", "ING", "BEN", "AL", "POZISYONUM", "ADET", "ORTALAMA"}

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📈 Portföy Durumu", "📉 Kar/Zarar Grafiği"],
        ["📊 30 Günlük Analiz", "🔮 Gelecek Tahmini"],
        ["🔍 Fon Detay Sorgula", "💰 Bakiye İşlemleri"],
        ["⚙️ Ayarlar", "➕ Fon Ekle", "➖ Fon Sat"]
    ],
    resize_keyboard=True
)

def get_exact_days_for_timeframe(tf_label="1A"):
    if tf_label == "1G": return 2
    elif tf_label == "1H": return 7
    elif tf_label == "1A": return 30
    elif tf_label == "3A": return 90
    elif tf_label == "6A": return 180
    elif tf_label == "1Y": return 365
    return 30

def get_chart_timeframe_keyboard(active_tf="1G") -> InlineKeyboardMarkup:
    buttons = ["1G", "1H", "1A", "3A", "6A", "1Y"]
    row = [InlineKeyboardButton(f"• {tf} •" if tf == active_tf else tf, callback_data=f"tf_{tf}") for tf in buttons]
    return InlineKeyboardMarkup([row])

def get_prediction_keyboard(active_period="1A") -> InlineKeyboardMarkup:
    periods = [("1A", "1 Ay"), ("3A", "3 Ay"), ("6A", "6 Ay"), ("1Y", "1 Yıl")]
    row = [InlineKeyboardButton(f"• {label} •" if code == active_period else label, callback_data=f"pred_{code}") for code, label in periods]
    return InlineKeyboardMarkup([row])

def get_user_portfolio(context: ContextTypes.DEFAULT_TYPE, chat_id=None):
    user_data = context.user_data if context.user_data is not None else (
        context.application.user_data.get(chat_id, {}) if (chat_id and context.application) else {}
    )
    if not user_data:
        return DEFAULT_PORTFOLIO.copy()
    if "portfolio" not in user_data or user_data.get("reset_done") is not True:
        user_data["portfolio"] = DEFAULT_PORTFOLIO.copy()
        user_data["reset_done"] = True
    return user_data["portfolio"]

def get_user_cash(context: ContextTypes.DEFAULT_TYPE, chat_id=None) -> float:
    user_data = context.user_data if context.user_data is not None else (
        context.application.user_data.get(chat_id, {}) if (chat_id and context.application) else {}
    )
    return float(user_data.get("cash_balance", 0.0))

def get_user_tax_rate(context: ContextTypes.DEFAULT_TYPE, chat_id=None) -> float:
    user_data = context.user_data if context.user_data is not None else (
        context.application.user_data.get(chat_id, {}) if (chat_id and context.application) else {}
    )
    return float(user_data.get("tax_rate", DEFAULT_TAX_RATE))

def get_user_summary_hour(context: ContextTypes.DEFAULT_TYPE, chat_id=None) -> str:
    user_data = context.user_data if context.user_data is not None else (
        context.application.user_data.get(chat_id, {}) if (chat_id and context.application) else {}
    )
    return user_data.get("summary_hour", DEFAULT_SUMMARY_HOUR)

def calculate_portfolio(portfolio):
    total_val, total_cost, total_daily_pl = 0.0, 0.0, 0.0
    items = []

    for code, data in portfolio.items():
        info = tefas_service.get_fund_info(code)
        latest_price = info.price if info and info.price > 0 else data["unit_cost"]
        full_title = getattr(info, 'title', None) or code
        
        units, unit_cost = data["units"], data["unit_cost"]
        cost, val = units * unit_cost, units * latest_price
        
        daily_pct = info.daily_return if info else 0.0
        daily_pl = val * (daily_pct / 100)
        
        total_cost += cost
        total_val += val
        total_daily_pl += daily_pl
        
        total_p_loss = val - cost
        total_p_pct = (total_p_loss / cost * 100) if cost > 0 else 0.0
        
        items.append({
            "code": code, "title": full_title, "units": units, "price": latest_price,
            "cost": cost, "val": val, "total_p_loss": total_p_loss, "total_p_pct": total_p_pct,
            "daily_return": daily_pct, "daily_pl": daily_pl
        })

    total_pl = total_val - total_cost
    total_pl_pct = (total_pl / total_cost * 100) if total_cost > 0 else 0.0
    total_daily_pct = (total_daily_pl / total_val * 100) if total_val > 0 else 0.0

    return items, total_cost, total_val, total_pl, total_pl_pct, total_daily_pl, total_daily_pct

def generate_progress_bar(pct, max_pct):
    if max_pct <= 0: return "░░░░░░░░░░"
    filled = min(max(int(round((abs(pct) / abs(max_pct)) * 10)), 1), 10)
    return "█" * filled + "░" * (10 - filled)

def generate_midas_chart(period="1G", portfolio=None):
    if portfolio is None: portfolio = DEFAULT_PORTFOLIO
    items, total_cost, total_val, total_pl, total_pl_pct, _, _ = calculate_portfolio(portfolio)
    days = get_exact_days_for_timeframe(period)

    fund_histories = {}
    max_len = 0
    for code in portfolio.keys():
        hist = tefas_service.get_fund_history(code, days=days)
        prices = [h["price"] for h in hist] if hist else []
        fund_histories[code] = prices
        if len(prices) > max_len: max_len = len(prices)

    if max_len < 2:
        portfolio_values = np.linspace(total_cost, total_val, 20)
    else:
        portfolio_values = np.zeros(max_len)
        for code, data in portfolio.items():
            prices = fund_histories.get(code, [])
            if len(prices) > 0:
                if len(prices) < max_len: prices = [prices[0]] * (max_len - len(prices)) + prices
                portfolio_values += np.array(prices) * data["units"]
            else:
                portfolio_values += data["units"] * data["unit_cost"]

    pl_series = ((portfolio_values - total_cost) / total_cost) * 100 if total_cost > 0 else np.zeros(len(portfolio_values))
    x_data = np.arange(len(pl_series))

    fig = plt.figure(figsize=(8, 11), dpi=200)
    fig.patch.set_facecolor('#0B0E11')
    ax = fig.add_axes([0.06, 0.12, 0.88, 0.70])
    ax.set_facecolor('#0B0E11')

    theme_color = '#00E676' if total_pl_pct >= 0 else '#FF3D00'
    ax.plot(x_data, pl_series, color=theme_color, linewidth=2.5, zorder=3)
    ax.scatter(x_data[-1], pl_series[-1], color=theme_color, s=50, zorder=4)
    ax.axis('off')

    sign_str = "+" if total_pl_pct >= 0 else ""
    pct_text = f"%{sign_str}{total_pl_pct:.2f}".replace('.', ',')
    val_text = f"₺{total_pl:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')

    fig.text(0.08, 0.89, pct_text, fontsize=30, fontweight='bold', color='#FFFFFF')
    fig.text(0.48, 0.895, val_text, fontsize=16, fontweight='bold', color=theme_color)

    now = datetime.now(TURKEY_TZ)
    fig.text(0.08, 0.86, f"{now.day} {TURKISH_MONTHS.get(now.month, 'Temmuz')} {now.year}", fontsize=11, color='#848E9C')
    ax.axhline(pl_series[0], color='#1E2329', linestyle='--', linewidth=1, zorder=1)

    file_path = f"midas_chart_{period}.png"
    plt.savefig(file_path, format='png', facecolor=fig.get_facecolor(), edgecolor='none', dpi=200)
    plt.close(fig)
    return file_path

def generate_prediction_text(portfolio, tax_rate, period_code="1A"):
    items, _, total_val, _, _, _, _ = calculate_portfolio(portfolio)
    days_map = {"1A": (30, "1 AY"), "3A": (90, "3 AY"), "6A": (180, "6 AY"), "1Y": (365, "1 YIL")}
    days, period_title = days_map.get(period_code, (30, "1 AY"))

    weighted_daily_returns = []
    for item in items:
        weight = item["val"] / total_val if total_val > 0 else 0
        hist = tefas_service.get_fund_history(item["code"], days=days)
        if hist and len(hist) > 1 and hist[0]["price"] > 0:
            avg_daily = (((hist[-1]["price"] - hist[0]["price"]) / hist[0]["price"]) * 100) / len(hist)
        else:
            avg_daily = item["daily_return"]
        weighted_daily_returns.append(avg_daily * weight)

    avg_daily_rate = sum(weighted_daily_returns) if weighted_daily_returns else 0.12
    net_daily_rate = avg_daily_rate * (1 - (tax_rate / 100))

    gross_profit = total_val * ((avg_daily_rate * days) / 100)
    tax_amount = gross_profit * (tax_rate / 100)
    net_profit = gross_profit - tax_amount
    target_val = total_val + net_profit

    target_date_str = (datetime.now(TURKEY_TZ) + timedelta(days=days)).strftime("%d.%m.%Y • %H:%M")

    return (
        f"🔮 **{period_title} SONRAKİ TAHMİN**\n\n"
        f"💼 Mevcut Varlık: `₺{total_val:,.2f}`\n"
        f"💰 Tahmini Net Kâr: `+₺{net_profit:,.2f}`\n"
        f"🏛️ Tahmini Vergi Kesintisi: `-₺{tax_amount:,.2f}`\n"
        "───────────────────\n"
        f"🎯 Hedef Bakiye: `₺{target_val:,.2f}`\n"
        f"⏰ `{target_date_str}`\n\n"
        f"📊 **Son {days} Günlük Veriyle Ort. Brüt Günlük:** %{avg_daily_rate:.3f}\n"
        f"ℹ️ **Net Günlük Getiri (%{tax_rate:.1f} Vergi Düşülmüş):** %{net_daily_rate:.3f}"
    )

def build_portfolio_text(portfolio, cash):
    items, total_cost, total_val, total_pl, total_pl_pct, total_daily_pl, total_daily_pct = calculate_portfolio(portfolio)
    text_lines = ["💼 **GÜNCEL PORTFÖY DURUMU & KÂR/ZARAR**\n"]

    for item in items:
        icon = "🟢" if item["total_p_loss"] >= 0 else "🔴"
        text_lines.append(f"{icon} **{item['code']} - {item['title']}**")
        text_lines.append(f"   ├ Adet: {item['units']:.0f} | Canlı Fiyat: ₺{item['price']:.4f}")
        text_lines.append(f"   ├ Değer: ₺{item['val']:,.2f} (Maliyet: ₺{item['cost']:,.2f})")
        text_lines.append(f"   ├ **Günlük:** %{item['daily_return']:+.2f} (₺{item['daily_pl']:+,.2f})")
        text_lines.append(f"   └ **Toplam Kâr:** %{item['total_p_pct']:+.2f} (₺{item['total_p_loss']:+,.2f})\n")

    overall_icon = "🚀" if total_pl >= 0 else "🔻"
    text_lines.append("───────────────────")
    text_lines.append(f"🏛️ **Fon Değeri:** ₺{total_val:,.2f}")
    text_lines.append(f"💵 **Nakit Bakiye:** ₺{cash:,.2f}")
    text_lines.append(f"💎 **Toplam Varlık:** ₺{total_val + cash:,.2f}")
    text_lines.append("───────────────────")
    text_lines.append(f"💰 **Fon Alış Maliyeti:** ₺{total_cost:,.2f}")
    text_lines.append(f"📅 **Bugünkü Değişim:** %{total_daily_pct:+.2f} (₺{total_daily_pl:+,.2f})")
    text_lines.append(f"{overall_icon} **Toplam Kâr/Zarar:** %{total_pl_pct:+.2f} (₺{total_pl:+,.2f})")

    return "\n".join(text_lines)

def process_ocr_image(image_path):
    try:
        img = Image.open(image_path)
        raw_text = pytesseract.image_to_string(img)
        
        detected_code = None
        for word in re.findall(r'\b[A-Z0-9]{3}\b', raw_text.upper()):
            if word not in EXCLUDED_WORDS:
                info = tefas_service.get_fund_info(word)
                if info and info.price > 0:
                    detected_code = word
                    break

        if not detected_code: return {}

        detected_units, detected_cost = None, None
        lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
        
        for i, line in enumerate(lines):
            line_clean = line.upper().replace(' ', '')
            if "ADET" in line_clean and not detected_units:
                for j in range(i, min(i+3, len(lines))):
                    for n in re.findall(r'\b\d+\b', lines[j]):
                        val = float(n)
                        if val > 0 and val != 3:
                            detected_units = val
                            break
                    if detected_units: break

            if ("ORTALAMA" in line_clean or "MALIYET" in line_clean or "FIYAT" in line_clean) and not detected_cost:
                for j in range(i, min(i+3, len(lines))):
                    prices = re.findall(r'\d+[\.,]\d+', lines[j])
                    if prices:
                        p_val = float(prices[0].replace(',', '.'))
                        if 0.001 < p_val < 1000:
                            detected_cost = p_val
                            break
                    if detected_cost: break

        if not detected_units: detected_units = 1.0
        if not detected_cost:
            info = tefas_service.get_fund_info(detected_code)
            detected_cost = info.price if info else 1.0

        return {detected_code: {"units": detected_units, "unit_cost": detected_cost}}
    except Exception as e:
        logging.error(f"OCR hatası: {e}")
        return {}

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 **Midas ekran görüntüsü taranıyor...**")
    photo_file = await update.message.photo[-1].get_file()
    img_path = "temp_ocr.jpg"
    await photo_file.download_to_drive(img_path)

    detected_funds = process_ocr_image(img_path)
    if os.path.exists(img_path): os.remove(img_path)

    if not detected_funds:
        await msg.edit_text("❌ **Görselde geçerli fon verisi tespit edilemedi.**")
        return

    context.user_data["pending_ocr_funds"] = detected_funds
    text_lines = ["🔍 **Görselden Okunan Bilgiler:**\n"]
    for code, data in detected_funds.items():
        text_lines.append(f"📌 **Fon Kodu:** `{code}`")
        text_lines.append(f"🔢 **Adet:** `{data['units']:.0f}`")
        text_lines.append(f"💰 **Ortalama Maliyet:** `₺{data['unit_cost']:.4f}`\n")
    
    text_lines.append("❓ **Bu veriler doğru mu? Portföyüne eklensin mi?**")
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Onayla & Ekle", callback_data="ocr_confirm"),
        InlineKeyboardButton("❌ İptal Et", callback_data="ocr_cancel")
    ]])
    await msg.edit_text("\n".join(text_lines), reply_markup=keyboard, parse_mode="Markdown")

async def handle_ocr_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "ocr_confirm":
        pending_funds = context.user_data.get("pending_ocr_funds", {})
        if not pending_funds: return
        portfolio = get_user_portfolio(context)
        summary_text = ["✅ **Fon Portföyüne Başarıyla Eklendi:**\n"]
        for code, data in pending_funds.items():
            portfolio[code] = {"units": data["units"], "unit_cost": data["unit_cost"]}
            summary_text.append(f"📌 **{code}:** {data['units']:.0f} Adet | Maliyet: ₺{data['unit_cost']:.4f}")
        context.user_data["pending_ocr_funds"] = {}
        await query.edit_message_text("\n".join(summary_text), parse_mode="Markdown")
    elif query.data == "ocr_cancel":
        context.user_data["pending_ocr_funds"] = {}
        await query.edit_message_text("❌ **İşlem iptal edildi.**")

async def cmd_set_summary_hour(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Örnek: `/ozetsaati 12:38`", parse_mode="Markdown")
        return
    new_hour = context.args[0].strip()
    if re.match(r"^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$", new_hour):
        context.user_data["summary_hour"] = new_hour
        await update.message.reply_text(f"✅ **Günlük özet saati `{new_hour}` olarak güncellendi!**", parse_mode="Markdown")
    else:
        await update.message.reply_text("⚠️ Geçersiz saat formatı!")

async def cmd_set_cash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return
    try:
        cash_val = float(context.args[0].replace(',', '.'))
        context.user_data["cash_balance"] = cash_val
        await update.message.reply_text(f"✅ **Nakit bakiyeniz ₺{cash_val:,.2f} olarak güncellendi!**", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("⚠️ Geçersiz tutar!")

async def dynamic_price_checker(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    now = datetime.now(TURKEY_TZ)
    today_str = now.strftime("%Y-%m-%d")

    if now.weekday() >= 5: return

    portfolio = get_user_portfolio(context, chat_id=chat_id)
    cash = get_user_cash(context, chat_id=chat_id)
    summary_hour = get_user_summary_hour(context, chat_id=chat_id)

    if LAST_CHECK_DATE.get(chat_id) != today_str:
        LAST_CHECK_DATE[chat_id] = today_str
        UPDATED_FUNDS_TODAY[chat_id] = set()
        SUMMARY_SENT_TODAY[chat_id] = False

    try:
        sh_hour, sh_min = map(int, summary_hour.split(":"))
        target_time = now.replace(hour=sh_hour, minute=sh_min, second=0, microsecond=0)
        is_time_passed = now >= target_time
    except Exception:
        is_time_passed = False

    if is_time_passed and not SUMMARY_SENT_TODAY.get(chat_id, False):
        SUMMARY_SENT_TODAY[chat_id] = True
        summary_text = f"📊 **GÜNLÜK KÂR / ZARAR ÖZETİ ({now.strftime('%d.%m.%Y')})**\n\n" + build_portfolio_text(portfolio, cash)
        await context.bot.send_message(chat_id=chat_id, text=summary_text, parse_mode="Markdown")

    updated_set = UPDATED_FUNDS_TODAY.get(chat_id, set())
    all_funds = set(portfolio.keys())

    if all_funds.issubset(updated_set) or now.hour < 9: return

    now_str = now.strftime("%d.%m.%Y • %H:%M")
    for code, data in portfolio.items():
        if code in updated_set: continue
        info = tefas_service.get_fund_info(code)
        if not info or info.price == 0: continue

        if getattr(info, 'daily_return', 0.0) != 0.0:
            updated_set.add(code)
            UPDATED_FUNDS_TODAY[chat_id] = updated_set
            units = data.get("units", 0)
            daily_pct = info.daily_return
            price = info.price
            net_change = (price * units) * (daily_pct / 100)
            
            icon = "🚀" if daily_pct > 0 else "🔻"
            sign = "+" if daily_pct > 0 else ""
            msg_text = (
                f"{icon} **FON FİYATI GÜNCELLENDİ**\n\n"
                f"💼 **{code}** - _{getattr(info, 'title', code)}_\n"
                f"📈 Değişim: `%{sign}{daily_pct:.2f}`\n"
                f"💰 Portföy Etkisi: `{sign}₺{net_change:,.2f}`\n"
                f"💵 Fiyat: `₺{price:.6f}` | 🕒 `{now_str}`"
            )
            await context.bot.send_message(chat_id=chat_id, text=msg_text, parse_mode="Markdown")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["reset_done"] = False
    get_user_portfolio(context)
    chat_id = update.effective_chat.id
    if context.job_queue and not context.job_queue.get_jobs_by_name(str(chat_id)):
        context.job_queue.run_repeating(dynamic_price_checker, interval=900, first=5, chat_id=chat_id, name=str(chat_id))
    await update.message.reply_text("🤖 **Midas Pro Fon Takip Botu**\n\n✨ Bot aktif edildi!", reply_markup=MAIN_KEYBOARD, parse_mode="Markdown")

async def send_portfolio_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_portfolio_text(get_user_portfolio(context), get_user_cash(context)), parse_mode="Markdown")

async def send_chart_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("📊 **Grafik Oluşturuluyor...**")
    try:
        chart_file = generate_midas_chart("1G", get_user_portfolio(context))
        with open(chart_file, 'rb') as photo:
            await update.message.reply_photo(photo=photo, caption="📈 **PORTFÖY KÂR ZARAR GRAFİĞİ (1G)**", reply_markup=get_chart_timeframe_keyboard("1G"), parse_mode="Markdown")
        await msg.delete()
        if os.path.exists(chart_file): os.remove(chart_file)
    except Exception as e: logging.error(f"Grafik hatası: {e}")

async def handle_chart_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tf = query.data.replace("tf_", "")
    chart_file = generate_midas_chart(tf, get_user_portfolio(context))
    with open(chart_file, 'rb') as photo:
        await query.edit_message_media(media=InputMediaPhoto(media=photo, caption=f"📈 **PORTFÖY KÂR ZARAR GRAFİĞİ ({tf})**"), reply_markup=get_chart_timeframe_keyboard(tf))
    if os.path.exists(chart_file): os.remove(chart_file)

async def send_monthly_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ **TEFAS Verileri Hesaplanıyor...**")
    portfolio = get_user_portfolio(context)
    fund_results, total_val_now, total_val_30_ago = [], 0.0, 0.0

    for code, data in portfolio.items():
        units = data["units"]
        hist = tefas_service.get_fund_history(code, days=30)
        info = tefas_service.get_fund_info(code)
        curr_price = info.price if info and info.price > 0 else data["unit_cost"]

        if hist and len(hist) > 1:
            price_30_ago = hist[0]["price"]
            pct_change = ((curr_price - price_30_ago) / price_30_ago) * 100
        else:
            pct_change = getattr(info, 'monthly_return', 0.0) if info else 0.0
            price_30_ago = curr_price / (1 + (pct_change / 100)) if pct_change != -100 else curr_price

        val_now, val_30_ago = units * curr_price, units * price_30_ago
        total_val_now += val_now
        total_val_30_ago += val_30_ago
        fund_results.append({"code": code, "pct": pct_change, "profit_tl": val_now - val_30_ago})

    fund_results.sort(key=lambda x: x["pct"], reverse=True)
    max_pct = max([abs(f["pct"]) for f in fund_results]) if fund_results else 1.0

    text_lines = ["📊 **PORTFÖY 30 GÜNLÜK PERFORMANS ANALİZİ**\n"]
    if fund_results:
        text_lines.append(f"🏆 **Aylık Şampiyon:** `{fund_results[0]['code']}` (%{fund_results[0]['pct']:+.2f})\n")

    text_lines.append("💼 **Fon Detayları:**")
    for item in fund_results:
        bar = generate_progress_bar(item["pct"], max_pct)
        icon = "🟢" if item["pct"] >= 0 else "🔴"
        text_lines.append(f"{icon} **{item['code']}:** %{item['pct']:+.2f} `(₺{item['profit_tl']:+,.2f})`\n   └ `[{bar}]`")

    await msg.edit_text("\n".join(text_lines), parse_mode="Markdown")

async def send_prediction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = generate_prediction_text(get_user_portfolio(context), get_user_tax_rate(context), "1A")
    await update.message.reply_text(text, reply_markup=get_prediction_keyboard("1A"), parse_mode="Markdown")

async def handle_prediction_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    period_code = query.data.replace("pred_", "")
    text = generate_prediction_text(get_user_portfolio(context), get_user_tax_rate(context), period_code)
    await query.edit_message_text(text, reply_markup=get_prediction_keyboard(period_code), parse_mode="Markdown")

async def send_fund_detail_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ["🔍 **Mevcut Fonların Detayları:**\n"]
    for code in get_user_portfolio(context).keys():
        info = tefas_service.get_fund_info(code)
        if info:
            text.append(f"📌 **{code}** - _{info.title}_")
            text.append(f"   ├ Günlük: %{info.daily_return:+.2f}")
            text.append(f"   └ Fiyat: ₺{info.price:.6f}\n")
    await update.message.reply_text("\n".join(text), parse_mode="Markdown")

async def send_balance_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, total_cost, total_val, _, _, _, _ = calculate_portfolio(get_user_portfolio(context))
    cash = get_user_cash(context)
    await update.message.reply_text(
        f"💰 **BAKİYE İŞLEMLERİ**\n\n🏛️ **Fonlardaki Tutar:** ₺{total_val:,.2f}\n💵 **Kullanılabilir Nakit:** ₺{cash:,.2f}\n💎 **Toplam Varlık:** ₺{total_val + cash:,.2f}\n🧾 **Toplam Maliyet:** ₺{total_cost:,.2f}\n\n💡 *Nakit değiştirmek için: `/nakit 1500`*",
        parse_mode="Markdown"
    )

async def send_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"⚙️ **BOT AYARLARI**\n\n🏛️ **Stopaj Vergi Oranı:** %{get_user_tax_rate(context):.1f}\n💵 **Nakit Bakiye:** ₺{get_user_cash(context):,.2f}\n📊 **Günlük Özet Saati:** `{get_user_summary_hour(context)}`\n\n💡 *Özet saatini değiştirmek için: `/ozetsaati 15:30`*",
        parse_mode="Markdown"
    )

async def start_add_fund(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("➕ Eklemek istediğiniz **Fon Kodunu** girin (Örn: `EKF`):", parse_mode="Markdown")
    return ADD_CODE

async def add_fund_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["temp_code"] = update.message.text.strip().upper()
    await update.message.reply_text(f"🔢 Kaç adet **{context.user_data['temp_code']}** aldınız?:", parse_mode="Markdown")
    return ADD_UNITS

async def add_fund_units(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["temp_units"] = float(update.message.text.replace(',', '.'))
        await update.message.reply_text("💰 **Alış Birim Fiyatınızı (₺)** girin (Örn: `0.132234`):", parse_mode="Markdown")
        return ADD_COST
    except ValueError:
        await update.message.reply_text("⚠️ Lütfen geçerli bir sayı girin.")
        return ADD_UNITS

async def add_fund_cost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        unit_cost = float(update.message.text.replace(',', '.'))
        code, units = context.user_data["temp_code"], context.user_data["temp_units"]
        portfolio = get_user_portfolio(context)
        if code in portfolio:
            old_units, old_cost = portfolio[code]["units"], portfolio[code]["unit_cost"]
            new_units = old_units + units
            portfolio[code]["units"] = new_units
            portfolio[code]["unit_cost"] = ((old_units * old_cost) + (units * unit_cost)) / new_units
        else:
            portfolio[code] = {"units": units, "unit_cost": unit_cost}
        await update.message.reply_text(f"✅ **{code}** fonu portföyünüze eklendi!", reply_markup=MAIN_KEYBOARD, parse_mode="Markdown")
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("⚠️ Lütfen geçerli bir tutar girin.")
        return ADD_COST

async def start_sell_fund(update: Update, context: ContextTypes.DEFAULT_TYPE):
    portfolio = get_user_portfolio(context)
    if not portfolio:
        await update.message.reply_text("❌ Satılabilecek fonunuz bulunmuyor.")
        return ConversationHandler.END
    await update.message.reply_text("➖ Satmak istediğiniz **Fon Kodunu** girin (Örn: `DTZ`):", parse_mode="Markdown")
    return SELL_CODE

async def sell_fund_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip().upper()
    portfolio = get_user_portfolio(context)
    if code not in portfolio:
        await update.message.reply_text(f"⚠️ **{code}** fonu bulunamadı.", reply_markup=MAIN_KEYBOARD, parse_mode="Markdown")
        return ConversationHandler.END
    context.user_data["temp_sell_code"] = code
    await update.message.reply_text(f"🔢 Kaç adet **{code}** satmak istiyorsunuz? (Mevcut: {portfolio[code]['units']:.0f}):", parse_mode="Markdown")
    return SELL_UNITS

async def sell_fund_units(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        units = float(update.message.text.replace(',', '.'))
        code = context.user_data["temp_sell_code"]
        portfolio = get_user_portfolio(context)
        if units >= portfolio[code]["units"]: del portfolio[code]
        else: portfolio[code]["units"] -= units
        await update.message.reply_text(f"✅ **{code}** fon satışı işlendi!", reply_markup=MAIN_KEYBOARD, parse_mode="Markdown")
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("⚠️ Lütfen geçerli bir miktar girin.")
        return SELL_UNITS

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("İşlem iptal edildi.", reply_markup=MAIN_KEYBOARD)
    return ConversationHandler.END

async def handle_general_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if "Portföy Durumu" in text: await send_portfolio_status(update, context)
    elif "Kar/Zarar Grafiği" in text: await send_chart_report(update, context)
    elif "30 Günlük Analiz" in text: await send_monthly_analysis(update, context)
    elif "Gelecek Tahmini" in text: await send_prediction(update, context)
    elif "Fon Detay Sorgula" in text: await send_fund_detail_query(update, context)
    elif "Bakiye İşlemleri" in text: await send_balance_info(update, context)
    elif "Ayarlar" in text: await send_settings(update, context)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    menu_filter = filters.Regex("^(📈 Portföy Durumu|📉 Kar/Zarar Grafiği|📊 30 Günlük Analiz|🔮 Gelecek Tahmini|🔍 Fon Detay Sorgula|💰 Bakiye İşlemleri|⚙️ Ayarlar)$")

    add_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(➕ Fon Ekle|Fon Ekle)$"), start_add_fund)],
        states={
            ADD_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~menu_filter, add_fund_code)],
            ADD_UNITS: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~menu_filter, add_fund_units)],
            ADD_COST: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~menu_filter, add_fund_cost)]
        },
        fallbacks=[CommandHandler("cancel", cancel_conv), MessageHandler(menu_filter, cancel_conv)]
    )

    sell_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(➖ Fon Sat|Fon Sat)$"), start_sell_fund)],
        states={
            SELL_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~menu_filter, sell_fund_code)],
            SELL_UNITS: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~menu_filter, sell_fund_units)]
        },
        fallbacks=[CommandHandler("cancel", cancel_conv), MessageHandler(menu_filter, cancel_conv)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ozetsaati", cmd_set_summary_hour))
    app.add_handler(CommandHandler("ozetsaat", cmd_set_summary_hour))
    app.add_handler(CommandHandler("nakit", cmd_set_cash))

    app.add_handler(CallbackQueryHandler(handle_chart_callback, pattern="^tf_"))
    app.add_handler(CallbackQueryHandler(handle_prediction_callback, pattern="^pred_"))
    app.add_handler(CallbackQueryHandler(handle_ocr_confirm_callback, pattern="^ocr_"))
    
    app.add_handler(add_conv)
    app.add_handler(sell_conv)
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_general_messages))
    
    print("🚀 Bot Başlatıldı!")
    app.run_polling()

if __name__ == "__main__":
    main()
