import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import google.generativeai as genai

# --- Configuration ---
# Get API keys from environment variables (crucial for security on Render)
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
    # Use 'gemini-1.5-flash-latest' for the latest Flash model
    # If you face issues, try 'gemini-pro'
    gemini_model = genai.GenerativeModel('gemini-1.5-flash-latest')
    logger.info("Gemini model configured.")
else:
    logger.error("GEMINI_API_KEY not found in environment variables.")
    gemini_model = None # Or raise an error to prevent bot from starting

# Dictionary to store chat sessions for conversation context
# In a real-world scenario, for production, you might want a more persistent storage
# like a database, but for a simple bot on Render, in-memory is fine.
user_gemini_chats = {}

# --- Telegram Bot Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message when the command /start is issued."""
    user = update.effective_user
    await update.message.reply_html(
        f"Hi {user.mention_html()}! I'm a bot powered by Google Gemini 1.5 Flash. "
        "Send me a message and I'll try to respond!",
    )
    logger.info(f"User {user.id} ({user.full_name}) started the bot.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles incoming text messages and sends them to Gemini."""
    if not gemini_model:
        await update.message.reply_text("Sorry, Gemini AI is not configured. Please check the API key.")
        return

    chat_id = update.effective_chat.id
    user_message = update.message.text
    logger.info(f"User {chat_id} sent message: {user_message}")

    # Get or create a Gemini chat session for this user
    if chat_id not in user_gemini_chats:
        user_gemini_chats[chat_id] = gemini_model.start_chat(history=[])
        logger.info(f"New Gemini chat session started for chat_id: {chat_id}")

    try:
        # Send message to Gemini and get response
        response = user_gemini_chats[chat_id].send_message(user_message)
        gemini_response = response.text
        logger.info(f"Gemini response for {chat_id}: {gemini_response}")

        # Send Gemini's response back to the user
        await update.message.reply_text(gemini_response)

    except Exception as e:
        logger.error(f"Error interacting with Gemini for chat_id {chat_id}: {e}")
        await update.message.reply_text(
            "Oops! I encountered an error while talking to Gemini. Please try again."
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

    # Create the Application and pass your bot's token.
    application = Application.builder().token(BOT_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    logger.info("Bot starting polling...")
    # Start the Bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)
    logger.info("Bot stopped.")

if __name__ == "__main__":
    main()
