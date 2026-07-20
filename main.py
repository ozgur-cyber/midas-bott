import os
import io
import time
import logging
import sqlite3
import asyncio
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

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "TELEGRAM_BOT_TOKEN_BURAYA")
ADMIN_ID = os.getenv("ADMIN_ID", "0")

# Flask Web Server
app = Flask(__name__)

@app.route('/')
def home():
    return "Midas Portfolio Bot 7/24 Aktif!"

def run_web():
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 8080)))

def keep_alive():
    t = Thread(target=run_web, daemon=True)
    t.start()

# Hassas Ondalık Formatlayıcı
def fmt(val, precision=4):
    if val is None:
        return "0"
    formatted = f"{val:,.{precision}f}"
    if '.' in formatted:
        formatted = formatted.rstrip('0').rstrip('.')
    return formatted

# SQLite Veritabanı Kurulumu
def init_db():
    conn = sqlite3.connect("portfolio.db")
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    
    # Portföy
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS portfolio (
            user_id INTEGER,
            symbol TEXT,
            amount REAL,
            avg_cost REAL,
            PRIMARY KEY (user_id, symbol)
        )
    """)
    # İşlem Geçmişi
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
    # Nakit
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cash (
            user_id INTEGER PRIMARY KEY,
            balance REAL DEFAULT 0.0
        )
    """)
    # Takip Listesi
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            user_id INTEGER,
            symbol TEXT,
            PRIMARY KEY (user_id, symbol)
        )
    """)
    # Son Fiyat Takibi (Bildirimler İçin)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS price_tracker (
            symbol TEXT PRIMARY KEY,
            last_price REAL
        )
    """)
    # Kullanıcı Ayarları (Tutarları Gizle/Göster)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            user_id INTEGER PRIMARY KEY,
            hide_amounts INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

# Fiyat Çekici (TEFAS / API)
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

# Dinamik Klavye Oluşturucu
def get_keyboard(user_id: int):
    conn = sqlite3.connect("portfolio.db")
    cursor = conn.cursor()
    cursor.execute("SELECT hide_amounts FROM settings WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    
    hide = row[0] if row else 0
    toggle_btn = "👁️ Tutarları Göster" if hide == 1 else "🙈 Tutarları Gizle"

    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📊 Portföyüm"), KeyboardButton("📈 Grafik")],
            [KeyboardButton("👀 Takip Listesi"), KeyboardButton("🔍 Fon Ara")],
            [KeyboardButton("➕ Fon Ekle"), KeyboardButton("🗑️ Fon Sil")],
            [KeyboardButton("💵 Nakit"), KeyboardButton("📈 Ort. Performans")],
            [KeyboardButton("📜 Geçmiş"), KeyboardButton("📄 PDF Raporu")],
            [KeyboardButton(toggle_btn)]
        ],
        resize_keyboard=True
    )

def is_hidden(user_id: int) -> bool:
    conn = sqlite3.connect("portfolio.db")
    cursor = conn.cursor()
    cursor.execute("SELECT hide_amounts FROM settings WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return bool(row[0]) if row else False

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
    if new_bal < 0:
        new_bal = 0.0
    cursor.execute("INSERT OR REPLACE INTO cash (user_id, balance) VALUES (?, ?)", (user_id, new_bal))
    cursor.execute("INSERT INTO history (user_id, symbol, action, amount, price) VALUES (?, 'NAKİT', 'NAKIT_GUNCELLE', ?, ?)", (user_id, new_bal, 0))
    conn.commit()
    conn.close()
    return new_bal

# --- BOT KOMUTLARI VE BUTON YÖNETİCİLERİ ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    msg = (
        "📊 *Midas & TEFAS Portföy Takip Botu*\n\n"
        "Aşağıdaki menü butonlarını kullanarak tüm işlemlerinizi yapabilirsiniz.\n"
        "🔹 Hızlı Fon Ekleme: `/ekle <sembol> <adet> <maliyet>`\n"
        "🔹 Hızlı Fon Silme: `/sil <sembol>`"
    )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_keyboard(user_id))

async def portfoy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    items = db_get_portfolio(user_id)
    cash_bal = db_get_cash(user_id)
    hidden = is_hidden(user_id)

    if not items and cash_bal <= 0:
        await update.message.reply_text("📭 Portföyünüz boş. `/ekle` veya **💵 Nakit** butonu ile varlık ekleyebilirsiniz.", reply_markup=get_keyboard(user_id))
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

        if hidden:
            text += f"🔹 *{symbol}*\n   Adet: `***` | Maliyet: `*** TL`\n   Güncel: `{fmt(cur_price, 4)} TL` | Değer: `*** TL`\n"
        else:
            text += f"🔹 *{symbol}*\n"
            text += f"   Adet: `{fmt(amount, 2)}` | Ort. Maliyet: `{fmt(avg_cost, 6)} TL`\n"
            text += f"   Güncel: `{fmt(cur_price, 6)} TL` | Değer: `{fmt(curr_val, 2)} TL`\n"
            text += f"   Durum: {icon} `{fmt(pnl, 2)} TL` ({pnl_str})\n"
        text += "───────────────────────────\n"

    total_value_with_cash = total_value_sum + cash_bal
    total_pnl = total_value_sum - total_cost_sum
    total_pnl_pct = (total_pnl / total_cost_sum * 100) if total_cost_sum > 0 else 0.0
    total_icon = "🚀" if total_pnl >= 0 else "🔻"

    if hidden:
        text += f"💵 *Nakit Bakiye:* `*** TL`\n"
        text += f"💰 *Toplam Yatırım:* `*** TL`\n"
        text += f"📈 *Genel Toplam Bakiye:* `*** TL`\n"
    else:
        text += f"💵 *Nakit Bakiye:* `{fmt(cash_bal, 2)} TL`\n"
        text += f"💰 *Toplam Fon Maliyeti:* `{fmt(total_cost_sum, 2)} TL`\n"
        text += f"📈 *Toplam Fon Değeri:* `{fmt(total_value_sum, 2)} TL`\n"
        text += f"📊 *Genel Portföy Bakiye:* `{fmt(total_value_with_cash, 2)} TL`\n"
        text += f"{total_icon} *Toplam Kar/Zarar:* `{fmt(total_pnl, 2)} TL` (%{total_pnl_pct:.2f})\n"

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_keyboard(user_id))

async def grafik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    items = db_get_portfolio(user_id)
    cash_bal = db_get_cash(user_id)

    if not items and cash_bal <= 0:
        await update.message.reply_text("📭 Grafiğini oluşturmak için portföyünüzde varlık olmalı.", reply_markup=get_keyboard(user_id))
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

    if cash_bal > 0:
        labels.append("NAKİT")
        sizes.append(cash_bal)

    fig, ax = plt.subplots(figsize=(6, 6))
    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#17becf']
    
    ax.pie(
        sizes, 
        labels=labels, 
        autopct='%1.1f%%',
        startangle=140,
        colors=colors[:len(labels)],
        wedgeprops=dict(width=0.4, edgecolor='w')
    )
    ax.set_title("Portföy & Nakit Dağılımı", fontsize=14, pad=20)

    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', dpi=150)
    buf.seek(0)
    plt.close()

    await update.message.reply_photo(photo=buf, caption="📊 Portföy Varlık Dağılım Grafiğiniz", reply_markup=get_keyboard(user_id))

async def nakit_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bal = db_get_cash(user_id)
    hidden = is_hidden(user_id)

    bal_str = "*** TL" if hidden else f"{fmt(bal, 2)} TL"
    text = (
        f"💵 *NAKİT PORTFÖYÜ*\n\n"
        f"Mevcut Nakit Bakiyeniz: `{bal_str}`\n\n"
        f"💡 *Nakit Güncelleme Komutları:*\n"
        f"🔹 Nakit Ekle: `/nakitekle <tutar>` (Örn: `/nakitekle 5000`)\n"
        f"🔹 Nakit Çıkar: `/nakitcikar <tutar>` (Örn: `/nakitcikar 1500`)\n"
        f"🔹 Bakiyeyi Sıfırla/Ayarla: `/nakitset <tutar>`"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_keyboard(user_id))

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
        await update.message.reply_text("📭 Performans analizi için portföyünüzde fon olmalıdır.", reply_markup=get_keyboard(user_id))
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
        f"💡 Performans, portföydeki fonların mevcut piyasa fiyatlarına göre hesaplanır."
    )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_keyboard(user_id))

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
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_keyboard(user_id))
        return

    msg = "👀 *TAKİP ETTİĞİNİZ FONLAR*\n───────────────────────────\n"
    for (sym,) in rows:
        price = fetch_price(sym)
        msg += f"🔹 *{sym}*: `{fmt(price, 6)} TL`\n"
    msg += "───────────────────────────\n💡 `/takip <sembol>` veya `/takipsil <sembol>` yazarak yönetebilirsiniz."
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_keyboard(user_id))

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
    if price > 0:
        await update.message.reply_text(f"🔍 *{sym} Fon Fiyatı:*\n💵 Anlık Birim Fiyat: `{fmt(price, 6)} TL`", parse_mode="Markdown")
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
        await update.message.reply_text("📜 Henüz işlem geçmişiniz bulunmuyor.", reply_markup=get_keyboard(user_id))
        return

    msg = "📜 *SON İŞLEM GEÇMİŞİ*\n───────────────────────────\n"
    for sym, act, amt, prc, ts in rows:
        msg += f"🗓 `{ts}` | *{act}*\n   Fon: `{sym}` | Adet: `{fmt(amt, 2)}` | Fiyat: `{fmt(prc, 4)} TL`\n"
        msg += "───────────────────────────\n"
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_keyboard(user_id))

async def pdf_raporu_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    items = db_get_portfolio(user_id)
    cash_bal = db_get_cash(user_id)

    fig, ax = plt.subplots(figsize=(8.5, 11))
    ax.axis('off')

    report_text = "MIDAS & TEFAS PORTFOY DETAYLI RAPORU\n"
    report_text += "="*45 + "\n\n"

    tot_val = 0.0
    tot_cost = 0.0

    for sym, amt, cost in items:
        p = fetch_price(sym)
        if p <= 0: p = cost
        c_val = amt * cost
        v_val = amt * p
        tot_cost += c_val
        tot_val += v_val
        report_text += f"Fon: {sym:<6} | Adet: {amt:<10.2f} | Mal: {cost:<8.4f} | Guncel: {p:<8.4f} | Deger: {v_val:<10.2f}\n"

    report_text += "-"*45 + "\n"
    report_text += f"Nakit Bakiye     : {cash_bal:.2f} TL\n"
    report_text += f"Toplam Fon Maliyet: {tot_cost:.2f} TL\n"
    report_text += f"Toplam Fon Deger  : {tot_val:.2f} TL\n"
    report_text += f"Genel Portfoy   : {tot_val + cash_bal:.2f} TL\n"
    report_text += f"Toplam Kar/Zarar : {tot_val - tot_cost:.2f} TL\n"

    ax.text(0.05, 0.95, report_text, transform=ax.transAxes, fontsize=10, verticalalignment='top', fontfamily='monospace')

    buf = io.BytesIO()
    plt.savefig(buf, format='pdf', bbox_inches='tight')
    buf.seek(0)
    plt.close()

    await update.message.reply_document(
        document=buf,
        filename="Portfoy_Raporu.pdf",
        caption="📄 Detaylı Portföy PDF Raporunuz Hazırlandı.",
        reply_markup=get_keyboard(user_id)
    )

async def toggle_tutarlar_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    hidden = is_hidden(user_id)
    new_state = 0 if hidden else 1

    conn = sqlite3.connect("portfolio.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (user_id, hide_amounts) VALUES (?, ?)", (user_id, new_state))
    conn.commit()
    conn.close()

    status_msg = "🙈 Tutarlar gizlendi!" if new_state == 1 else "👁️ Tutarlar görünür yapıldı!"
    await update.message.reply_text(status_msg, reply_markup=get_keyboard(user_id))

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

# --- OTOMATİK FİYAT ARTIŞ BİLDİRİMİ SİSTEMİ ---
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

                            # Bu fonu tutan tüm kullanıcılara bildirim gönder
                            cursor.execute("SELECT user_id, amount FROM portfolio WHERE symbol = ?", (sym,))
                            holders = cursor.fetchall()
                            for u_id, amt in holders:
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
            await asyncio.sleep(600) # 10 Dakikada bir kontrol eder

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
            application.add_handler(MessageHandler(filters.Regex(r"^(👁️ Tutarları Göster|🙈 Tutarları Gizle)$"), toggle_tutarlar_view))

            application.run_polling(drop_pending_updates=True)
        except Exception as e:
            logging.error(f"Hata oluştu, yeniden başlatılıyor: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
