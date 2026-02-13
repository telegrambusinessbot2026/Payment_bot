import os
import logging
import asyncio
import json
import hashlib
import base64
from datetime import datetime
from typing import Dict, List, Optional
import aiohttp
from cryptography.fernet import Fernet
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, 
    ConversationHandler, ContextTypes, filters, CallbackContext
)
from telegram.constants import ParseMode
import dotenv  # Fixed import

# Load environment variables
dotenv.load_dotenv()

# Configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
OWNER_ID = int(os.getenv('OWNER_ID', '0'))
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', '@admin')
ZAPUPI_API_KEY = os.getenv('ZAPUPI_API_KEY', '')
LOG_CHANNEL_ID = os.getenv('LOG_CHANNEL_ID', '-1002000000000')
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY', Fernet.generate_key().decode())  # Fallback key

# Initialize encryption
try:
    cipher_suite = Fernet(ENCRYPTION_KEY.encode())
except:
    ENCRYPTION_KEY = Fernet.generate_key().decode()
    cipher_suite = Fernet(ENCRYPTION_KEY.encode())

# States for conversation handlers
ADD_PRODUCT_NAME, ADD_PRODUCT_IMAGE, ADD_PRODUCT_PRICE, ADD_PRODUCT_DESC = range(4)
BROADCAST_TEXT = 0

# Data storage
PRODUCTS_DB = {}
USERS_DB = set()
GROUPS_DB = {}
PAYMENTS_DB = {}

# Rate limiting
user_rates = {}

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class ProductBot:
    def __init__(self):
        self.app = None
        self.cipher_suite = cipher_suite
        
    def is_admin(self, user_id: int) -> bool:
        return user_id == OWNER_ID
    
    def rate_limit_check(self, user_id: int) -> bool:
        now = datetime.now().timestamp()
        if user_id in user_rates:
            if now - user_rates[user_id] < 2:
                return False
        user_rates[user_id] = now
        return True

bot = ProductBot()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not bot.rate_limit_check(update.effective_user.id):
        await update.message.reply_text("⏳ Please wait a moment.")
        return
    
    user_id = update.effective_user.id
    USERS_DB.add(user_id)
    
    welcome_text = """
🎉 **Welcome to ProductBot!** 🎉

Discover amazing products!

**Categories:**
    """
    
    keyboard = [
        [InlineKeyboardButton("📱 Electronics", callback_data="cat_electronics")],
        [InlineKeyboardButton("👗 Fashion", callback_data="cat_fashion")],
        [InlineKeyboardButton("🏠 Home", callback_data="cat_home")],
        [InlineKeyboardButton("💎 All Products", callback_data="cat_all")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        welcome_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = f"""
🆘 **ProductBot Help**

**Commands:**
• `/start` - Main menu
• `/help` - Help

**Admin Only:**
• `/add_product` - Add products
• `/broadcast` - Send to all users

**Support:** @{ADMIN_USERNAME}

**Payment:** UPI via Zapupi
    """
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def log_activity(message: str, context: ContextTypes.DEFAULT_TYPE, photo_id: str = None):
    try:
        if photo_id:
            await context.bot.send_photo(
                chat_id=LOG_CHANNEL_ID,
                photo=photo_id,
                caption=f"📋 **Activity Log**\n\n{message}",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await context.bot.send_message(
                chat_id=LOG_CHANNEL_ID,
                text=f"📋 **Activity Log**\n\n{message}",
                parse_mode=ParseMode.MARKDOWN
            )
    except:
        pass

# Admin Commands
async def add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not bot.is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only!")
        return ConversationHandler.END
    
    await log_activity(f"Admin started adding product", context)
    await update.message.reply_text("📝 **Product Name:**")
    return ADD_PRODUCT_NAME

async def add_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['product_name'] = update.message.text
    await update.message.reply_text("🖼️ **Send Product Image:**")
    return ADD_PRODUCT_IMAGE

async def add_product_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['product_image'] = update.message.photo[-1].file_id
    await update.message.reply_text("💰 **Product Price:** (₹999)")
    return ADD_PRODUCT_PRICE

async def add_product_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['product_price'] = update.message.text
    await update.message.reply_text("📄 **Description:**")
    return ADD_PRODUCT_DESC

async def add_product_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    product_data = {
        'name': context.user_data['product_name'],
        'image': context.user_data['product_image'],
        'price': context.user_data['product_price'],
        'desc': update.message.text
    }
    
    product_id = hashlib.md5(product_data['name'].encode()).hexdigest()[:8]
    PRODUCTS_DB[product_id] = product_data
    
    await log_activity(
        f"✅ Product Added\nID: `{product_id}`\nName: {product_data['name']}",
        context
    )
    
    await update.message.reply_text(f"✅ Product `{product_id}` added!")
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not bot.is_admin(update.effective_user.id):
        return
    
    await update.message.reply_text("📢 **Broadcast message:**")
    return BROADCAST_TEXT

async def broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    broadcast_msg = update.message.text
    success_count = 0
    
    for user_id in list(USERS_DB):
        try:
            await context.bot.send_message(user_id, broadcast_msg)
            success_count += 1
            await asyncio.sleep(0.05)  # Rate limit
        except:
            pass
    
    await update.message.reply_text(f"📢 Broadcast sent to {success_count} users!")
    return ConversationHandler.END

# Payment handler
async def handle_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    screenshot = update.message.photo[-1].file_id
    
    PAYMENTS_DB[user_id] = {'screenshot': screenshot, 'status': 'pending'}
    
    await log_activity(f"💰 Payment from {update.effective_user.first_name}", context, screenshot)
    
    await update.message.reply_text(
        "✅ **Payment received!** Verifying...\n⏳ Please wait..."
    )
    
    # Mock Zapupi verification
    await asyncio.sleep(3)
    PAYMENTS_DB[user_id]['status'] = 'verified'
    
    await update.message.reply_text("🎉 **Payment Verified!** Order confirmed!")

# Group handler
async def new_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.bot.id in [member.user.id for member in update.message.new_chat_members]:
        group_id = update.effective_chat.id
        GROUPS_DB[group_id] = {"welcome_msg": "Welcome to group!"}
        await log_activity(f"👥 Added to group: {update.effective_chat.title}", context)

# Category buttons
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith('cat_'):
        products_text = "📦 **Products:**\n\n"
        if PRODUCTS_DB:
            for pid, product in PRODUCTS_DB.items():
                products_text += f"**{product['name']}** - {product['price']}\n"
        else:
            products_text += "No products yet!"
        
        keyboard = [[InlineKeyboardButton("💳 Buy Now", callback_data="buy_now")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            products_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )

def main():
    """Main entry point"""
    print("🚀 Starting ProductBot...")
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    
    # Admin handlers
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add_product", add_product)],
        states={
            ADD_PRODUCT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_name)],
            ADD_PRODUCT_IMAGE: [MessageHandler(filters.PHOTO, add_product_image)],
            ADD_PRODUCT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_price)],
            ADD_PRODUCT_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_desc)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(conv_handler)
    
    broadcast_handler = ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast)],
        states={
            BROADCAST_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_text)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(broadcast_handler)
    
    # Other handlers
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.PHOTO & filters.CAPTION, handle_payment))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_chat_member))
    
    print("✅ Handlers registered!")
    
    # Webhook setup for Render
    port = int(os.getenv('PORT', 8443))
    webhook_url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/{BOT_TOKEN}"
    
    print(f"🌐 Setting up webhook: {webhook_url}")
    
    # Run with polling first for testing, webhook in production
    if os.getenv('RENDER_EXTERNAL_HOSTNAME'):
        # Production: Webhook
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=BOT_TOKEN,
            webhook_url=webhook_url
        )
    else:
        # Development: Polling
        application.run_polling()

if __name__ == '__main__':
    main()
