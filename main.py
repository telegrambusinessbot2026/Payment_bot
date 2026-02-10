import os, asyncio, json, uvicorn, hmac, hashlib
from fastapi import FastAPI, Request, Header
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ChatMemberHandler

# --- CONFIGURATION (Environment Variables) ---
TOKEN = os.getenv('8508093915:AAFAyqjKjDYXZzHVqjZttsC7FYdEoEa-Buc')
OWNER_ID = int(os.getenv('-7639633018', '0'))
ZAPUPI_API_KEY = os.getenv('d8c2943122ff97aaf722e87f73bbfd08')
ZAPUPI_SECRET = os.getenv('54d6e033843c0c519a9b4f207b606406') # Zapupi Secret Key
PREMIUM_GROUP_ID = int(os.getenv('-1005162246120', '0'))

# ലോഗ് ചാനലുകൾ
PAYMENT_LOG_ID = int(os.getenv('-1005235631263', '0'))
ACTIVITY_LOG_ID = int(os.getenv('-1003612737572', '0'))
DATABASE_CHANNEL = int(os.getenv('-1005269535383', '0'))

# FastAPI & Bot Setup
app = FastAPI()
bot_instance = Bot(token=TOKEN)

# ബോട്ട് ഡാറ്റ
data = {
    "products": {}, 
    "support_user": "@admin",
    "welcome_text": "Mallu-ലേക്ക് സ്വാഗതം! താഴെ പറയുന്ന പ്ലാനുകൾ നോക്കൂ:",
    "welcome_photo": None,
    "broadcast_msg": "Join our premium group now!",
    "active_groups": []
}

# ഹെൽപ്പ് ടെക്സ്റ്റ്
HELP_TEXT = """
📜 **Mallu Bot Command List**

🔹 /start - ബോട്ട് തുടങ്ങാനും പ്ലാനുകൾ കാണാനും.
🔹 /addproduct [ID] [Name] [Price] - പുതിയ പ്ലാൻ ചേർക്കാൻ.
🔹 /setsupport [Username] - സപ്പോർട്ട് അഡ്മിനെ മാറ്റാൻ.
🔹 /setwelcome [Text] - വെൽക്കം മെസ്സേജ് മാറ്റാൻ.
🔹 /setbroadcast [Message] - ഗ്രൂപ്പ് പരസ്യം സെറ്റ് ചെയ്യാൻ.
🔹 /help - ഈ വിവരങ്ങൾ കാണാൻ.
🔹 /showcmds - ഈ ലിസ്റ്റ് DATABASE ചാനലിലേക്ക് അയക്കാൻ.
"""

# --- DATABASE LOGIC ---
async def update_db(context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.send_message(
            chat_id=DATABASE_CHANNEL,
            text=f"#DATABASE_UPDATE\n\n{json.dumps(str(data))}"
        )
    except: pass

# --- WEBHOOK: SECURE AUTOMATIC PAYMENT ---
@app.post("/webhook/zapupi")
async def zapupi_webhook(request: Request):
    # Zapupi അയക്കുന്ന Signature വെരിഫൈ ചെയ്യുന്നു
    signature = request.headers.get("X-Zapupi-Signature")
    body = await request.body()
    
    if not signature or not ZAPUPI_SECRET:
        return {"status": "unauthorized"}

    expected_signature = hmac.new(
        ZAPUPI_SECRET.encode(), 
        body, 
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_signature):
        return {"status": "error", "message": "Invalid Signature"}

    payload = await request.json()
    
    if payload.get("status") == "completed":
        user_id = payload.get("external_id")
        try:
            # സിംഗിൾ യൂസ് ലിങ്ക്
            invite_link = await bot_instance.create_chat_invite_link(
                chat_id=PREMIUM_GROUP_ID, member_limit=1
            )
            # കസ്റ്റമർക്ക് സ്പോട്ടിൽ ലിങ്ക് അയക്കുന്നു
            await bot_instance.send_message(
                chat_id=user_id,
                text=f"✅ **പേയ്‌മെന്റ് വിജയിച്ചു!**\n\nനിങ്ങളുടെ ലിങ്ക് ഇതാ: {invite_link.invite_link}\n\nഈ ലിങ്ക് ഒരാൾക്ക് മാത്രമേ ഉപയോഗിക്കാൻ സാധിക്കൂ."
            )
            # ലോഗ് ചാനലുകളിൽ അറിയിക്കുന്നു
            await bot_instance.send_message(
                chat_id=PAYMENT_LOG_ID,
                text=f"💰 **SUCCESS:** User `{user_id}` പൈസ അടച്ചു, ലിങ്ക് നൽകി."
            )
        except Exception as e:
            print(f"Webhook Error: {e}")
            
    return {"status": "ok"}

# --- BOT HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await context.bot.send_message(chat_id=ACTIVITY_LOG_ID, text=f"👤 Bot Started: {user.first_name}")
    
    keyboard = []
    for pid, pinfo in data["products"].items():
        keyboard.append([InlineKeyboardButton(f"Buy {pinfo['name']} - ₹{pinfo['price']}", callback_data=f"buy_{pid}")])
    keyboard.append([InlineKeyboardButton("Support", url=f"https://t.me/{data['support_user'].replace('@','')}")])
    
    markup = InlineKeyboardMarkup(keyboard)
    if data["welcome_photo"]:
        await update.message.reply_photo(photo=data["welcome_photo"], caption=data["welcome_text"], reply_markup=markup)
    else:
        await update.message.reply_text(data["welcome_text"], reply_markup=markup)

async def handle_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("buy_"):
        pid = query.data.split("_")[1]
        product = data["products"].get(pid)
        if product:
            pay_url = f"https://zapupi.com/pay?api={ZAPUPI_API_KEY}&amount={product['price']}&external_id={update.effective_user.id}"
            keyboard = [[InlineKeyboardButton(f"Pay ₹{product['price']}", url=pay_url)]]
            await query.edit_message_text(
                f"🛍 **Plan:** {product['name']}\n💰 **Price:** ₹{product['price']}\n\nപേയ്‌മെന്റ് കഴിഞ്ഞ് സ്പോട്ടിൽ ലിങ്ക് ഇവിടെ ലഭിക്കും.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

async def add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        args = context.args # /addproduct [ID] [Name] [Price]
        data["products"][args[0]] = {"name": args[1], "price": args[2]}
        await update_db(context)
        await update.message.reply_text(f"✅ Product Added: {args[1]}")
    except: await update.message.reply_text("Usage: /addproduct 1 Gold 500")

async def show_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == OWNER_ID:
        await update.message.reply_text(HELP_TEXT)
        await context.bot.send_message(chat_id=DATABASE_CHANNEL, text=f"📋 **Command List Requested:**\n{HELP_TEXT}")

async def auto_broadcast_task(context: ContextTypes.DEFAULT_TYPE):
    while True:
        for gid in data["active_groups"]:
            try: await context.bot.send_message(chat_id=gid, text=f"📢 {data['broadcast_msg']}")
            except: pass
        await asyncio.sleep(600)

# --- MAIN ---
async def run_bot():
    app_bot = Application.builder().token(TOKEN).build()
    
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("addproduct", add_product))
    app_bot.add_handler(CommandHandler("help", show_commands))
    app_bot.add_handler(CommandHandler("showcmds", show_commands))
    app_bot.add_handler(CallbackQueryHandler(handle_click))
    
    async def track(update, context):
        if update.my_chat_member and update.my_chat_member.new_chat_member.status in ["member", "administrator"]:
            data["active_groups"].append(update.my_chat_member.chat.id)
            await update_db(context)
    app_bot.add_handler(ChatMemberHandler(track))

    await app_bot.initialize()
    await app_bot.start()
    await app_bot.updater.start_polling()
    
    # സ്റ്റാർട്ടപ്പ് മെസ്സേജ് ഡാറ്റാബേസിൽ
    await app_bot.bot.send_message(chat_id=DATABASE_CHANNEL, text=f"🤖 **Bot Online with Secret Verification!**\n{HELP_TEXT}")
    asyncio.create_task(auto_broadcast_task(app_bot))

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(run_bot())
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

