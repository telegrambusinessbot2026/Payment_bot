import os
import requests
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Heroku Settings (Config Vars വഴി എടുക്കുന്നത്)
TELEGRAM_TOKEN = os.getenv('8508093915:AAGv4bAE7LmBq3JOxU_6BLH9rtnl_R7Ws7U')
ZAPUPI_API_TOKEN = os.getenv('d8c2943122ff97aaf722e87f73bbfd08')
ZAPUPI_SECRET = os.getenv('54d6e033843c0c519a9b4f207b606406')
PAYMENT_LOG_ID = int(os.getenv('-1005235631263'))
ACTIVITY_LOG_ID = int(os.getenv('-1003612737572'))
GROUP_ID = int(os.getenv('-1005162246120'))
OWNER_ID = int(os.getenv('7639633018'))

AMOUNT = "1.00"

async def log_activity(context, message):
    try:
        await context.bot.send_message(chat_id=ACTIVITY_LOG_ID, text=f"🔔 **Activity:**\n{message}", parse_mode='Markdown')
    except: pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    await log_activity(context, f"👤 {user.full_name} (@{user.username}) ബോട്ട് സ്റ്റാർട്ട് ചെയ്തു.")
    # Zacmo ബ്രാൻഡ് ഫോട്ടോകൾ ഇവിടെ ചേർക്കാം
    images = ['https://yourlink.com/photo1.jpg'] 
    media = [InputMediaPhoto(img) for img in images]
    await update.message.reply_media_group(media=media)
    btn = [[InlineKeyboardButton("Join Premium Group", callback_data='pay')]]
    await update.message.reply_text("Zacmo-ലേക്ക് സ്വാഗതം! പേയ്മെന്റ് ചെയ്യാൻ താഴെ ക്ലിക്ക് ചെയ്യുക.", reply_markup=InlineKeyboardMarkup(btn))

async def handle_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    order_id = f"ZACMO_{query.from_user.id}_{int(asyncio.get_event_loop().time())}"
    url = "https://zapupi.com/api/v1/create_order"
    payload = {"api_token": ZAPUPI_API_TOKEN, "secret_key": ZAPUPI_SECRET, "amount": AMOUNT, "order_id": order_id}
    res = requests.post(url, data=payload).json()
    if res.get('status') == 'success':
        btn = [[InlineKeyboardButton("Pay Now (UPI)", url=res.get('payment_url'))],
               [InlineKeyboardButton("Verify Payment", callback_data=f"v_{order_id}")]]
        await query.edit_message_text(f"തുക: {AMOUNT} രൂപ. പണമടച്ച ശേഷം വെരിഫൈ ചെയ്യുക.", reply_markup=InlineKeyboardMarkup(btn))

async def verify_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    order_id = query.data.split("_")[1]
    res = requests.post("https://zapupi.com/api/v1/check_status", data={"api_token": ZAPUPI_API_TOKEN, "order_id": order_id}).json()
    if res.get('status') == 'COMPLETED':
        context.user_data['waiting_ss'] = order_id
        await query.edit_message_text("✅ വെരിഫൈഡ്! ഇപ്പോൾ സ്ക്രീൻഷോട്ട് അയക്കുക.")
    else:
        await query.answer("❌ പേയ്മെന്റ് കംപ്ലീറ്റ് ആയിട്ടില്ല.", show_alert=True)

async def collect_ss(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('waiting_ss'):
        user = update.message.from_user
        user_mention = f"[{user.full_name}](tg://user?id={user.id})"
        log_text = f"💰 **New Payment Log**\n👤 Name: {user_mention}\n🆔 ID: `{user.id}`\n📦 Order: `{context.user_data['waiting_ss']}`"
        btn = [[InlineKeyboardButton("Approve & Send Link", callback_data=f"app_{user.id}")]]
        await context.bot.send_photo(chat_id=PAYMENT_LOG_ID, photo=update.message.photo[-1].file_id, caption=log_text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(btn))
        await update.message.reply_text("സ്ക്രീൻഷോട്ട് ലഭിച്ചു. അഡ്മിൻ പരിശോധിച്ച ശേഷം ലിങ്ക് ലഭിക്കും.")
        context.user_data['waiting_ss'] = None

async def approve_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != OWNER_ID: return
    target_id = query.data.split("_")[1]
    link = await context.bot.create_chat_invite_link(chat_id=GROUP_ID, member_limit=1)
    await context.bot.send_message(chat_id=target_id, text=f"🎉 അപ്രൂവ് ചെയ്തു! ലിങ്ക്: {link.invite_link}")
    await query.edit_message_caption(caption=f"{query.message.caption}\n✅ **Approved**\n🔗 **Link:** {link.invite_link}")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_payment, pattern='pay'))
    app.add_handler(CallbackQueryHandler(verify_payment, pattern='^v_'))
    app.add_handler(CallbackQueryHandler(approve_user, pattern='^app_'))
    app.add_handler(MessageHandler(filters.PHOTO, collect_ss))
    app.run_polling()

if __name__ == '__main__':
    main()