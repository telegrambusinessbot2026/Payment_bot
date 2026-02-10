import os, asyncio, json, uvicorn, hmac, hashlib, threading
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ChatMemberHandler

# --- CONFIGURATION (നേരിട്ട് വാല്യൂസ് നൽകുന്നു) ---
TOKEN = '8508093915:AAHj907oq1YmCiHfQoaxeaqDSothKpAjXEM'
OWNER_ID = 7639633018 # ശ്രദ്ധിക്കുക: ഇത് നിങ്ങളുടെ ശരിയായ പോസിറ്റീവ് ഐഡി ആയിരിക്കണം
ZAPUPI_API_KEY = '02d5cd30e3951561c542a2ff1390710f'
ZAPUPI_SECRET = '13e39d62060cea32ec2d44cba10dafa8'
PREMIUM_GROUP_ID = -1005162246120

PAYMENT_LOG_ID = -1005235631263
ACTIVITY_LOG_ID = -1003612737572
DATABASE_CHANNEL = -1005269535383

# FastAPI & Bot Setup
app = FastAPI()
bot_instance = Bot(token=TOKEN)

# ബോട്ട് ഡാറ്റ
data = {
    "products": {}, 
    "support_user": "@admin",
    "welcome_text": "Mallu-ലേക്ക് സ്വാഗതം! Anyone who wants this Mallu product, DM me as soon as possible.",
    "welcome_photo": None,
    "broadcast_msg": "Join our premium group now!",
    "active_groups": []
}

# --- WEBHOOK ---
@app.post("/webhook/zapupi")
async def zapupi_webhook(request: Request):
    signature = request.headers.get("X-Zapupi-Signature")
    body = await request.body()
    if signature:
        expected = hmac.new(ZAPUPI_SECRET.encode(), body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(signature, expected):
            payload = await request.json()
            if payload.get("status") == "completed":
                user_id = payload.get("external_id")
                try:
                    invite = await bot_instance.create_chat_invite_link(chat_id=PREMIUM_GROUP_ID, member_limit=1)
                    await bot_instance.send_message(chat_id=user_id, text=f"✅ പേയ്‌മെന്റ് വിജയിച്ചു! ലിങ്ക്: {invite.invite_link}")
                except: pass
    return {"status": "ok"}

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    for pid, pinfo in data["products"].items():
        keyboard.append([InlineKeyboardButton(f"Buy {pinfo['name']} - ₹{pinfo['price']}", callback_data=f"buy_{pid}")])
    
    markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(data["welcome_text"], reply_markup=markup)

async def add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == OWNER_ID:
        try:
            args = context.args
            data["products"][args[0]] = {"name": args[1], "price": args[2]}
            await update.message.reply_text(f"✅ {args[1]} Added!")
        except: await update.message.reply_text("Usage: /addproduct 1 Mallu_Product 200")

async def handle_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("buy_"):
        pid = query.data.split("_")[1]
        p = data["products"].get(pid)
        if p:
            pay_url = f"https://zapupi.com/pay?api={ZAPUPI_API_KEY}&amount={p['price']}&external_id={update.effective_user.id}"
            await query.edit_message_text(f"🛍 {p['name']}\n💰 ₹{p['price']}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Pay Now", url=pay_url)]]))

# --- RUNNERS ---
def run_bot():
    """ബോട്ട് റൺ ചെയ്യുന്ന ഫങ്ക്ഷൻ"""
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addproduct", add_product))
    application.add_handler(CallbackQueryHandler(handle_click))
    
    print("Bot is starting...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    # ബോട്ടിൽ ഒരു പ്രത്യേക ത്രെഡിൽ തുടങ്ങുന്നു
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # മെയിൻ ത്രെഡിൽ FastAPI സെർവർ റൺ ചെയ്യുന്നു
    print("Web server is starting...")
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

