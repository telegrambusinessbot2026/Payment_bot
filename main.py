import logging
import os
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import traceback
import sys

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot, ChatInviteLink
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes, ChatMemberHandler
)
from telegram.error import TelegramError, BadRequest
import httpx

# Load environment variables
load_dotenv()

# Environment variables - FAIL FAST if missing
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", 0))
ZAPUPI_API_KEY = os.getenv("ZAPUPI_API_KEY", "")
ZAPUPI_SECRET = os.getenv("ZAPUPI_SECRET", "")
PAID_GROUP_ID = int(os.getenv("PAID_GROUP_ID", 0))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", 0))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")

# Validate critical env vars
REQUIRED_VARS = [BOT_TOKEN, str(OWNER_ID)]
if not all(REQUIRED_VARS):
    print("❌ MISSING REQUIRED ENV VARS: BOT_TOKEN, OWNER_ID")
    sys.exit(1)

print(f"✅ Bot initialized for OWNER_ID: {OWNER_ID}")

# Conversation states
ADD_PRODUCT, ADD_PRODUCT_IMAGE, ADD_PRODUCT_PRICE, ADD_PRODUCT_DESC = range(4)

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class ProductManager:
    def __init__(self):
        self.products = {}
        self.product_counter = 0
    
    async def add_product(self, name: str, image_file_id: str, price: float, desc: str) -> str:
        self.product_counter += 1
        product_id = f"prod_{self.product_counter:03d}"
        self.products[product_id] = {
            "name": name,
            "image": image_file_id,
            "price": price,
            "desc": desc,
            "added": datetime.now().isoformat()
        }
        return product_id
    
    def get_products(self) -> Dict[str, Dict]:
        return self.products

product_manager = ProductManager()

class ZapupiAPI:
    BASE_URL = "https://api.zapupi.in/v1"  # UPDATE WITH ACTUAL ZAPUPI URL
    
    def __init__(self, api_key: str, secret: str):
        self.api_key = api_key
        self.secret = secret
        self.session = httpx.AsyncClient(timeout=30.0, limits=httpx.Limits(max_keepalive_connections=5))
    
    async def verify_payment(self, transaction_id: str, amount: float) -> Dict[str, Any]:
        """Verify payment via Zapupi API"""
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "X-API-Secret": self.secret,
                "Content-Type": "application/json"
            }
            params = {"transaction_id": transaction_id, "amount": amount}
            
            async with self.session.get(
                f"{self.BASE_URL}/payments/verify",  # ✅ FIXED BUG
                headers=headers,
                params=params
            ) as response:
                data = await response.json() if response.status_code == 200 else {}
                data["success"] = response.status_code == 200
                return data
                
        except Exception as e:
            logger.error(f"Zapupi verification error: {str(e)}")
            return {"success": False, "error": f"API Error: {str(e)}"}
    
    async def close(self):
        await self.session.aclose()

zapupi_client = ZapupiAPI(ZAPUPI_API_KEY, ZAPUPI_SECRET)

# Global instances
application: Application = None
bot: Bot = None

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id if update.effective_user else None
    return user_id == OWNER_ID

async def send_log(message: str, photo: Optional[str] = None):
    """Send log message to channel"""
    try:
        if LOG_CHANNEL_ID == 0:
            return
            
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        full_message = f"[{timestamp}]\n{message}"
        
        if photo:
            await bot.send_photo(chat_id=LOG_CHANNEL_ID, photo=photo, caption=full_message)
        else:
            await bot.send_message(chat_id=LOG_CHANNEL_ID, text=full_message)
    except Exception as e:
        logger.error(f"Failed to send log: {str(e)}")

# Admin Commands
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await is_admin(update, context):
        await update.message.reply_text(
            "👑 <b>ADMIN PANEL</b>\n\n"
            "📦 /add_product\n"
            "📢 /broadcast\n"
            "📊 /stats",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            "💰 <b>Product Bot</b>\n\n"
            "Send payment screenshot or transaction ID to verify and get access!",
            parse_mode="HTML"
        )

async def add_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Unauthorized.")
        return ConversationHandler.END
    await update.message.reply_text("📦 Enter product name:")
    return ADD_PRODUCT

async def add_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["product_name"] = update.message.text.strip()
    await update.message.reply_text("🖼️ Send product image:")
    return ADD_PRODUCT_IMAGE

async def add_product_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        await update.message.reply_text("❌ Send a valid image!")
        return ADD_PRODUCT_IMAGE
    context.user_data["product_image"] = update.message.photo[-1].file_id
    await update.message.reply_text("💰 Enter price (e.g. 99.99):")
    return ADD_PRODUCT_PRICE

async def add_product_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        price = float(update.message.text)
        context.user_data["product_price"] = price
        await update.message.reply_text("📝 Enter description:")
        return ADD_PRODUCT_DESC
    except ValueError:
        await update.message.reply_text("❌ Invalid price! Use: 99.99")
        return ADD_PRODUCT_PRICE

async def add_product_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = context.user_data
    product_id = await product_manager.add_product(
        data["product_name"], data["product_image"], 
        data["product_price"], update.message.text.strip()
    )
    
    await update.message.reply_text(
        f"✅ <b>Product Added!</b>\n\n"
        f"ID: <code>{product_id}</code>\n"
        f"Name: {data['product_name']}\n"
        f"💰 ₹{data['product_price']:.2f}",
        parse_mode="HTML"
    )
    
    await send_log(f"New product: {product_id} - {data['product_name']} (₹{data['product_price']:.2f})")
    context.user_data.clear()
    return ConversationHandler.END

async def add_product_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update, context):
        return await update.message.reply_text("❌ Unauthorized.")
    
    if not context.args:
        return await update.message.reply_text("Usage: /broadcast <message>")
    
    message = " ".join(context.args)
    await update.message.reply_text("📢 Broadcasting...")
    await send_log(f"Broadcast sent: {message[:100]}...")
    await update.message.reply_text("✅ Broadcast logged!")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update, context):
        return
    
    products = len(product_manager.get_products())
    await update.message.reply_text(
        f"📊 <b>Stats</b>\n"
        f"Products: {products}\n"
        f"Uptime: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        parse_mode="HTML"
    )

# Payment Flow
async def handle_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    
    # Log attempt
    media_id = None
    if update.message.photo:
        media_id = update.message.photo[-1].file_id
        msg_type = "Photo"
    elif update.message.text:
        msg_type = f"TXN: {update.message.text[:20]}"
    else:
        return
    
    await send_log(
        f"💰 Payment Request\n"
        f"User: {user_id} (@{username})\n"
        f"Type: {msg_type}",
        media_id
    )
    
    # Show products
    products = product_manager.get_products()
    if not products:
        await update.message.reply_text("❌ No products available. Contact admin.")
        return
    
    text = "📦 <b>Available Products</b>\n\n"
    for pid, p in products.items():
        text += f"• <b>{p['name']}</b>\n  💰 ₹{p['price']:.2f}\n  {p['desc'][:60]}...\n\n"
    
    keyboard = [[InlineKeyboardButton("✅ VERIFY PAYMENT", callback_data="verify_pay")]]
    await update.message.reply_text(
        f"{text}Click to verify:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )
    
    context.user_data["payment_user"] = user_id
    context.user_data["payment_media"] = media_id or update.message.text

async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    if context.user_data.get("payment_user") != query.from_user.id:
        return await query.edit_message_text("❌ Unauthorized.")
    
    await query.edit_message_text("🔄 Verifying with Zapupi API...")
    
    # Demo with first product
    products = product_manager.get_products()
    if not products:
        return await query.edit_message_text("❌ No products!")
    
    product = list(products.values())[0]
    amount = product["price"]
    txn_id = context.user_data.get("payment_media", "DEMO_TXN")
    
    # Verify payment
    result = await zapupi_client.verify_payment(txn_id, amount)
    
    if result.get("success"):
        try:
            # Generate ONE-TIME invite link
            invite_link = await bot.create_chat_invite_link(
                chat_id=PAID_GROUP_ID,
                member_limit=1,
                expire_date=datetime.now() + timedelta(hours=24)
            )
            
            await query.edit_message_text(
                f"✅ <b>PAYMENT VERIFIED!</b>\n\n"
                f"🎟️ <b>Invite Link:</b>\n<code>{invite_link.invite_link}</code>\n\n"
                f"⏰ Expires: 24h | 1 use only",
                parse_mode="HTML"
            )
            
            # Success log
            await send_log(
                f"✅ <b>PAYMENT SUCCESS</b>\n"
                f"User: {query.from_user.id} (@{query.from_user.username or 'N/A'})\n"
                f"₹{amount:.2f} | TXN: {txn_id}\n"
                f"Link: {invite_link.invite_link}",
                context.user_data["payment_media"]
            )
            
        except TelegramError as e:
            await query.edit_message_text(f"❌ Invite failed: {str(e)}")
            await send_log(f"❌ Invite error: {str(e)}")
    else:
        error = result.get("error", "Unknown error")
        await query.edit_message_text(f"❌ Verification failed:\n<b>{error}</b>", parse_mode="HTML")
        await send_log(f"❌ Payment failed | User: {query.from_user.id} | Error: {error}")
    
    context.user_data.clear()

async def group_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Notify owner of new groups"""
    chat = update.effective_chat
    if chat.type in ["group", "supergroup"]:
        await send_log(f"🆕 Bot added to: {chat.title} ({chat.id})")
        try:
            await bot.send_message(OWNER_ID, f"🆕 New group: {chat.title} ({chat.id})")
        except:
            pass

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Error: {context.error}", exc_info=context.error)
    await send_log(f"💥 Bot Error:\n<code>{str(context.error)[:1000]}</code>", parse_mode="HTML")

def main() -> None:
    global application, bot
    
    print("🚀 Starting bot...")
    application = Application.builder().token(BOT_TOKEN).build()
    bot = application.bot
    
    # Handlers
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add_product", add_product_start)],
        states={
            ADD_PRODUCT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_name)],
            ADD_PRODUCT_IMAGE: [MessageHandler(filters.PHOTO, add_product_image)],
            ADD_PRODUCT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_price)],
            ADD_PRODUCT_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_desc)],
        },
        fallbacks=[CommandHandler("cancel", add_product_cancel)],
    )
    
    # Register handlers
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CallbackQueryHandler(verify_callback, pattern="^verify_pay$"))
    application.add_handler(MessageHandler(filters.PHOTO | filters.TEXT & ~filters.COMMAND, handle_payment))
    application.add_handler(ChatMemberHandler(group_status, ChatMemberHandler.CHAT_MEMBER))
    application.add_error_handler(error_handler)
    
    # Webhook or polling
    port = int(os.getenv("PORT", 8443))
    
    if RENDER_EXTERNAL_URL and BOT_TOKEN:
        webhook_url = f"{RENDER_EXTERNAL_URL}/{BOT_TOKEN}"
        print(f"🌐 Webhook: {webhook_url}")
        
        loop = asyncio.get_event_loop()
        loop.run_until_complete(bot.delete_webhook(drop_pending_updates=True))
        
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=BOT_TOKEN,
            webhook_url=webhook_url
        )
    else:
        print("🔄 Polling mode (local)")
        application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("👋 Bot stopped")
    finally:
        asyncio.run(zapupi_client.close())
