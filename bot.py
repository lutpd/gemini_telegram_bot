import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
# Removed: from telegram.constants import ParseMode # No longer needed for plain text
import google.generativeai as genai

# --- Configuration ---
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Gemini Configuration ---
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    
    # Using 'gemini-2.0-flash' as per your screenshot and preference
    # If you still get errors about the model not found, this 'gemini-2.0-flash'
    # might not be available to your specific Google Cloud project/account yet.
    # In that case, you'd need to try 'gemini-1.5-flash-latest' or 'gemini-1.5-pro-latest'.
    gemini_model = genai.GenerativeModel('gemini-2.0-flash') 
    
    logger.info(f"Gemini model '{gemini_model.model_name}' configured.")
else:
    logger.error("GEMINI_API_KEY not found in environment variables.")
    gemini_model = None 

# Dictionary to store chat sessions for conversation context
user_gemini_chats = {}

# --- Telegram Bot Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message when the command /start is issued."""
    user = update.effective_user
    # Kept reply_html for mention_html to work correctly
    await update.message.reply_html(
        f"Hi {user.mention_html()}! I'm a bot powered by Google Gemini 2.0 Flash. " 
        "Send me a message and I'll try to respond!",
    )
    logger.info(f"User {user.id} ({user.full_name}) started the bot.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles incoming text messages and sends them to Gemini."""
    if not gemini_model:
        await update.message.reply_text("Sorry, Gemini AI is not configured. Please check the API key and model name.")
        return

    chat_id = update.effective_chat.id
    user_message = update.message.text
    logger.info(f"User {chat_id} sent message: {user_message}")

    if chat_id not in user_gemini_chats:
        try:
            user_gemini_chats[chat_id] = gemini_model.start_chat(history=[])
            logger.info(f"New Gemini chat session started for chat_id: {chat_id}")
        except Exception as e:
            logger.error(f"Error starting Gemini chat for chat_id {chat_id}: {e}")
            await update.message.reply_text(
                "Oops! I couldn't start a chat with Gemini. The model might not be available or there's an API issue. "
                "Please check the Replit logs for details."
            )
            return

    try:
        response = user_gemini_chats[chat_id].send_message(user_message)
        gemini_response = response.text
        logger.info(f"Gemini response for {chat_id}: {gemini_response}")

        # --- MODIFIED LINE HERE: Sending as plain text ---
        # Any bold (**text**), italics, etc. will now appear literally as Gemini sends them.
        await update.message.reply_text(gemini_response) 

    except Exception as e:
        logger.error(f"Error interacting with Gemini for chat_id {chat_id}: {e}")
        # Simplified error message, as parsing errors should be gone now
        await update.message.reply_text(
            "Oops! I encountered an error while talking to Gemini. The model might not be available or there's an API issue. "
            "Please try again."
        )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and send a message to the user."""
    logger.warning(f'Update {update} caused error {context.error}')
    if update.effective_message:
        text = "An error occurred! Please try again later."
        await update.effective_message.reply_text(text)


def main() -> None:
    """Starts the bot."""
    if not BOT_TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN not found in environment variables. Bot cannot start.")
        return

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    logger.info("Bot starting polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)
    logger.info("Bot stopped.")

if __name__ == "__main__":
    main()
