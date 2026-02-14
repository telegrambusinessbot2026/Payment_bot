import logging
import os
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import traceback
import sys

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes
)
from telegram.error import TelegramError
import httpx

load_dotenv()

# Environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", 0))
ZAPUPI_API_KEY = os.getenv("ZAPUPI_API_KEY", "")
ZAPUPI_SECRET = os.getenv("ZAPUPI_SECRET", "")
PAID_GROUP_ID = int(os.getenv("PAID_GROUP_ID", 0))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", 0))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")

if not BOT_TOKEN:
    print("❌ BOT_TOKEN required!")
    sys.exit(1)

print(f"✅ Bot ready for OWNER_ID: {OWNER_ID}")

# States
ADD_PRODUCT, ADD_PRODUCT_IMAGE, ADD_PRODUCT_PRICE, ADD_PRODUCT_DESC = range(4)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ProductManager:
    def __init__(self):
        self.products = {}
        self.product_counter = 0
    
    def add_product(self, name: str, image_id: str, price: float, desc: str) -> str:
        self.product_counter += 1
        pid = f"prod_{self.product_counter:03d}"
        self.products[pid] = {"name": name, "image": image_id, "price": price, "desc": desc}
        return pid
    
    def get_products(self) -> Dict:
        return self.products

product_manager = ProductManager()

class ZapupiAPI:
    BASE_URL = "https://api.zapupi.in/v1"
    
    def __init__(self, api_key, secret):
        self.api_key = api_key
        self.secret = secret
        self.session = httpx.AsyncClient(timeout=30.0)
    
    async def verify(self, txn_id: str, amount: float) -> dict:
        try:
            async with self.session.get(
                f"{self.BASE_URL}/payments/verify",
                params={"transaction_id": txn_id, "amount": amount},
                headers={"Authorization": f"Bearer {self.api_key}"}
            ) as resp:
                data = await resp.json()
                data["success"] = resp.status_code == 200
                return data
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def close(self):
        await self.session.aclose()

zapupi = ZapupiAPI(ZAPUPI_API_KEY, ZAPUPI_SECRET)

application: Application = None
bot = None

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    return update.effective_user.id == OWNER_ID

async def log_msg(message: str, photo: str = None):
    if LOG_CHANNEL_ID == 0:
        return
    try:
        ts = datetime.now().strftime("%H:%M:%S")
        full_msg = f"[{ts}] {message}"
        if photo:
            await bot.send_photo(LOG_CHANNEL_ID, photo, caption=full_msg)
        else:
            await bot.send_message(LOG_CHANNEL_ID, full_msg)
    except:
        pass

# COMMANDS
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_admin(update, context):
        await update.message.reply_text(
            "👑 ADMIN:\n/add_product\n/broadcast\n/stats",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text("💰 Send payment proof!")

async def add_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return ConversationHandler.END
    await update.message.reply_text("📦 Product name:")
    return ADD_PRODUCT

async def add_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text
    await update.message.reply_text("🖼️ Image:")
    return ADD_PRODUCT_IMAGE

async def add_product_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["image"] = update.message.photo[-1].file_id
    await update.message.reply_text("💰 Price:")
    return ADD_PRODUCT_PRICE

async def add_product_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["price"] = float(update.message.text)
        await update.message.reply_text("📝 Description:")
        return ADD_PRODUCT_DESC
    except:
        await update.message.reply_text("❌ Number please!")
        return ADD_PRODUCT_PRICE

async def add_product_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data
    pid = product_manager.add_product(
        data["name"], data["image"], data["price"], update.message.text
    )
    await update.message.reply_text(f"✅ Added: {pid}")
    await log_msg(f"New product: {pid}")
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled")
    return ConversationHandler.END

async def payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    media = None
    
    if update.message.photo:
        media = update.message.photo[-1].file_id
        msg_type = "Screenshot"
    elif update.message.text:
        msg_type = f"TXN: {update.message.text[:20]}"
    else:
        return
    
    await log_msg(f"💰 User {user_id}: {msg_type}", media)
    
    products = product_manager.get_products()
    if not products:
        await update.message.reply_text("❌ No products!")
        return
    
    text = "📦 Products:\n\n"
    for pid, p in products.items():
        text += f"• {p['name']}\n  💰 ₹{p['price']}\n\n"
    
    kb = [[InlineKeyboardButton("✅ Verify", callback_data="verify")]]
    await update.message.reply_text(
        text + "Click to verify:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    
    context.user_data["user_id"] = user_id
    context.user_data["media"] = media or update.message.text

async def verify_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if context.user_data.get("user_id") != query.from_user.id:
        return await query.edit_message_text("❌ Not yours!")
    
    await query.edit_message_text("🔄 Checking...")
    
    # Demo verification
    product = list(product_manager.get_products().values())[0]
    result = await zapupi.verify("DEMO_TXN", product["price"])
    
    if result.get("success"):
        try:
            link = await bot.create_chat_invite_link(
                PAID_GROUP_ID, member_limit=1,
                expire_date=datetime.now() + timedelta(hours=24)
            )
            await query.edit_message_text(f"✅ Access:\n{link.invite_link}")
            await log_msg(f"✅ Verified: {query.from_user.id}", context.user_data["media"])
        except Exception as e:
            await query.edit_message_text(f"❌ Group error: {e}")
    else:
        await query.edit_message_text("❌ Payment not verified")
    
    context.user_data.clear()

async def error_handler(update, context):
    logger.error(f"Error: {context.error}")

def main():
    global application, bot
    
    app = Application.builder().token(BOT_TOKEN).build()
    bot = app.bot
    
    # Handlers
    conv = ConversationHandler(
        entry_points=[CommandHandler("add_product", add_product_start)],
        states={
            ADD_PRODUCT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_name)],
            ADD_PRODUCT_IMAGE: [MessageHandler(filters.PHOTO, add_product_image)],
            ADD_PRODUCT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_price)],
            ADD_PRODUCT_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_desc)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    app.add_handler(conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO | filters.TEXT & ~filters.COMMAND, payment_handler))
    app.add_handler(CallbackQueryHandler(verify_cb, pattern="^verify$"))
    app.add_error_handler(error_handler)
    
    # Render webhook
    port = int(os.getenv("PORT", 8443))
    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL}/{BOT_TOKEN}"
        print(f"Webhook: {webhook_url}")
        app.run_webhook(
            host="0.0.0.0",
            port=port,
            url_path=BOT_TOKEN,
            webhook_url=webhook_url
        )
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
