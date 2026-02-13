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
import python-dotenv

# Load environment variables
python-dotenv.load_dotenv()

# Configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
OWNER_ID = int(os.getenv('OWNER_ID'))
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME')
ZAPUPI_API_KEY = os.getenv('ZAPUPI_API_KEY')
LOG_CHANNEL_ID = os.getenv('LOG_CHANNEL_ID', '-1001234567890')  # Replace with actual channel ID
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY')  # Generate with: Fernet.generate_key()

# Initialize encryption
cipher_suite = Fernet(ENCRYPTION_KEY.encode())

# States for conversation handlers
ADD_PRODUCT_NAME, ADD_PRODUCT_IMAGE, ADD_PRODUCT_PRICE, ADD_PRODUCT_DESC = range(4)
BROADCAST_TEXT, BROADCAST_IMAGE, BROADCAST_BUTTONS = range(3)

# Data storage (in production, use PostgreSQL/Redis)
PRODUCTS_DB = {}  # {product_id: {"name": "", "image": "", "price": "", "desc": ""}}
USERS_DB = set()  # Track all users
GROUPS_DB = {}  # {group_id: {"welcome_msg": ""}}
PAYMENTS_DB = {}  # {user_id: {"status": "", "screenshot": "", "amount": ""}}

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
        self.app = Application.builder().token(BOT_TOKEN).build()
        self.setup_handlers()
        
    async def encrypt_data(self, data: str) -> str:
        """Encrypt sensitive data"""
        return cipher_suite.encrypt(data.encode()).decode()
    
    async def decrypt_data(self, encrypted_data: str) -> str:
        """Decrypt sensitive data"""
        return cipher_suite.decrypt(encrypted_data.encode()).decode()
    
    def is_admin(self, user_id: int) -> bool:
        """Check if user is admin"""
        return user_id == OWNER_ID
    
    def rate_limit_check(self, user_id: int) -> bool:
        """Simple rate limiting"""
        now = datetime.now().timestamp()
        if user_id in user_rates:
            if now - user_rates[user_id] < 2:  # 2 seconds cooldown
                return False
        user_rates[user_id] = now
        return True

# Initialize bot
bot = ProductBot()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    if not bot.rate_limit_check(update.effective_user.id):
        await update.message.reply_text("⏳ Please wait a moment before trying again.")
        return
    
    user_id = update.effective_user.id
    USERS_DB.add(user_id)
    
    # Welcome message with product categories
    welcome_text = """
🎉 **Welcome to ProductBot!** 🎉

Discover amazing products with easy browsing and secure payments!

Choose a category to explore:
    """
    
    keyboard = [
        [InlineKeyboardButton("📱 Electronics", callback_data="cat_electronics")],
        [InlineKeyboardButton("👗 Fashion", callback_data="cat_fashion")],
        [InlineKeyboardButton("🏠 Home & Kitchen", callback_data="cat_home")],
        [InlineKeyboardButton("💎 All Products", callback_data="cat_all")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Send product showcase
    await update.message.reply_photo(
        photo="https://example.com/welcome-product.jpg",  # Replace with actual image
        caption=welcome_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    help_text = f"""
🆘 **Help & Support**

**Available Commands:**
• `/start` - Welcome & Product Categories
• `/help` - This help message

**Admin Commands:**
• `/add_product` - Add new product
• `/broadcast_users` - Send message to all users
• `/broadcast_groups` - Send message to all groups

**Need Help?** Contact @{ADMIN_USERNAME}

**Payment:** Use UPI via Zapupi gateway
    """
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def log_activity(message: str, context: ContextTypes.DEFAULT_TYPE, screenshot=None):
    """Log all activities to channel"""
    try:
        log_msg = f"📋 **Bot Activity Log**\n\n{message}"
        if screenshot:
            await context.bot.send_photo(
                chat_id=LOG_CHANNEL_ID,
                photo=screenshot,
                caption=log_msg,
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await context.bot.send_message(
                chat_id=LOG_CHANNEL_ID,
                text=log_msg,
                parse_mode=ParseMode.MARKDOWN
            )
    except Exception as e:
        logger.error(f"Logging failed: {e}")

# Admin Commands
async def add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add product command"""
    if not bot.is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only command!")
        return ConversationHandler.END
    
    await log_activity(
        f"👤 Admin @{update.effective_user.username} started adding product",
        context
    )
    await update.message.reply_text("📝 **Enter Product Name:**")
    return ADD_PRODUCT_NAME

async def add_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle product name input"""
    context.user_data['product_name'] = update.message.text
    await update.message.reply_text("🖼️ **Send Product Image:**")
    return ADD_PRODUCT_IMAGE

async def add_product_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle product image"""
    context.user_data['product_image'] = update.message.photo[-1].file_id
    await update.message.reply_text("💰 **Enter Product Price:** (e.g., ₹999)")
    return ADD_PRODUCT_PRICE

async def add_product_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle product price"""
    context.user_data['product_price'] = update.message.text
    await update.message.reply_text("📄 **Enter Product Description:**")
    return ADD_PRODUCT_DESC

async def add_product_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save product and complete"""
    product_data = {
        'name': context.user_data['product_name'],
        'image': context.user_data['product_image'],
        'price': context.user_data['product_price'],
        'desc': update.message.text
    }
    
    product_id = hashlib.md5(product_data['name'].encode()).hexdigest()[:8]
    PRODUCTS_DB[product_id] = product_data
    
    await log_activity(
        f"✅ New Product Added:\n"
        f"ID: `{product_id}`\n"
        f"Name: {product_data['name']}\n"
        f"Price: {product_data['price']}",
        context
    )
    
    await update.message.reply_text(
        f"✅ **Product Added Successfully!**\n"
        f"ID: `{product_id}`\n\n"
        f"Product will appear in categories shortly."
    )
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel conversation"""
    context.user_data.clear()
    await update.message.reply_text("❌ Operation cancelled.")
    return ConversationHandler.END

async def broadcast_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast to all users"""
    if not bot.is_admin(update.effective_user.id):
        return
    
    await update.message.reply_text("📢 **Enter broadcast message:**")
    return BROADCAST_TEXT

async def broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle broadcast text"""
    context.user_data['broadcast_text'] = update.message.text
    await update.message.reply_text("🖼️ **Send broadcast image (or /skip):**")
    return BROADCAST_IMAGE

async def broadcast_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle broadcast image or skip"""
    if update.message.text == '/skip':
        await broadcast_buttons(update, context)
        return BROADCAST_BUTTONS
    
    context.user_data['broadcast_image'] = update.message.photo[-1].file_id
    await broadcast_buttons(update, context)
    return BROADCAST_BUTTONS

async def broadcast_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle broadcast buttons"""
    await update.message.reply_text(
        "🔘 **Add buttons?** Send button text (format: 'Text|callback_data') or /skip"
    )
    return BROADCAST_BUTTONS

# Payment Integration (Zapupi)
async def verify_zapupi_payment(amount: str, transaction_id: str) -> bool:
    """Verify payment with Zapupi API"""
    try:
        async with aiohttp.ClientSession() as session:
            headers = {'Authorization': f'Bearer {ZAPUPI_API_KEY}'}
            params = {'amount': amount, 'transaction_id': transaction_id}
            async with session.get('https://api.zapupi.com/verify', 
                                 headers=headers, params=params) as resp:
                data = await resp.json()
                return data.get('status') == 'success'
    except:
        return False

async def handle_payment_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle payment screenshot"""
    user_id = update.effective_user.id
    screenshot = update.message.photo[-1].file_id
    
    PAYMENTS_DB[user_id] = {
        'screenshot': screenshot,
        'status': 'pending',
        'timestamp': datetime.now().isoformat()
    }
    
    # Log payment screenshot
    await log_activity(
        f"💰 **Payment Received**\n"
        f"User: {update.effective_user.mention_html()}\n"
        f"Status: Pending Verification",
        context,
        screenshot
    )
    
    await update.message.reply_text(
        "✅ **Payment received!** Verifying with Zapupi...\n\n"
        "Please wait 10-30 seconds for confirmation."
    )
    
    # Simulate verification (replace with real Zapupi API call)
    await asyncio.sleep(5)
    
    if await verify_zapupi_payment("₹999", "txn_123"):  # Mock verification
        PAYMENTS_DB[user_id]['status'] = 'verified'
        await update.message.reply_text(
            "🎉 **Payment Verified!** Order confirmed.\n"
            "📦 Your order will be processed shortly."
        )
    else:
        PAYMENTS_DB[user_id]['status'] = 'failed'
        await update.message.reply_text(
            "❌ **Payment Verification Failed.**\n"
            "Please contact support."
        )

# Group handlers
async def group_added(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle bot added to group"""
    group_id = update.effective_chat.id
    GROUPS_DB[group_id] = {"welcome_msg": "Welcome to the group!"}
    
    await log_activity(
        f"👥 **Bot Added to Group**\n"
        f"Group: {update.effective_chat.title}\n"
        f"ID: `{group_id}`",
        context
    )
    
    await context.bot.send_message(
        OWNER_ID,
        f"✅ Bot added to group: {update.effective_chat.title} ({group_id})"
    )

# Callback query handler for categories
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button callbacks"""
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith('cat_'):
        # Show products for category
        products_text = "📦 **Available Products:**\n\n"
        for pid, product in PRODUCTS_DB.items():
            products_text += f"**{product['name']}** - {product['price']}\n"
        
        keyboard = [[InlineKeyboardButton("💳 Buy Now", callback_data="buy_product")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_caption(
            caption=products_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )

def setup_handlers():
    """Setup all handlers"""
    # Command handlers
    bot.app.add_handler(CommandHandler("start", start))
    bot.app.add_handler(CommandHandler("help", help_command))
    
    # Admin conversation handlers
    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add_product", add_product)],
        states={
            ADD_PRODUCT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_name)],
            ADD_PRODUCT_IMAGE: [MessageHandler(filters.PHOTO, add_product_image)],
            ADD_PRODUCT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_price)],
            ADD_PRODUCT_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_desc)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    bot.app.add_handler(add_conv)
    
    # Other handlers
    bot.app.add_handler(CallbackQueryHandler(button_callback))
    bot.app.add_handler(MessageHandler(filters.PHOTO & filters.Regex(r'.*upi.*|.*payment.*'), handle_payment_screenshot))
    bot.app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, group_added))

async def main():
    """Main function with webhook support"""
    setup_handlers()
    
    # Webhook setup for Render
    await bot.app.initialize()
    await bot.app.start()
    await bot.app.updater.start_webhook(
        listen="0.0.0.0",
        port=int(os.getenv('PORT', 8443)),
        url_path=BOT_TOKEN,
        webhook_url=f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/{BOT_TOKEN}"
    )
    
    logger.info("Bot started with webhook!")
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
