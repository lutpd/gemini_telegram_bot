import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
# Removed: from telegram.constants import ParseMode # Still sending plain text as requested
import google.generativeai as genai

# --- Configuration ---
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Set a maximum input message length for your bot
# This is a character limit. Gemini's limits are in tokens, which vary,
# but this acts as a first line of defense against excessively long user inputs.
# Adjust this value based on your needs and Gemini's actual token limits.
MAX_USER_MESSAGE_CHARS = 3000 # Example: Roughly 3000 characters could be ~750 tokens.
                              # Gemini 1.5 Flash has a context window of 128,000 tokens.
                              # However, this also includes response history, so you might
                              # hit the limit sooner if you have long conversations.

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Gemini Configuration ---
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    
    # Using 'gemini-2.0-flash' as per your screenshot and preference.
    # If the "model not found" error persists, this is the first place to check.
    # Try 'gemini-1.5-flash-latest' or 'gemini-1.5-pro-latest' if 2.0-flash is giving issues.
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

    # --- NEW: Check message length before sending to Gemini ---
    if len(user_message) > MAX_USER_MESSAGE_CHARS:
        await update.message.reply_text(
            f"Your message is too long ({len(user_message)} characters). "
            f"I can only process messages up to {MAX_USER_MESSAGE_CHARS} characters at a time. "
            "Please try a shorter message."
        )
        logger.warning(f"User {chat_id} sent message exceeding max length: {len(user_message)} chars.")
        return # Stop processing this message

    if chat_id not in user_gemini_chats:
        try:
            # Note: The 'history' list grows with each turn. If conversations get very long,
            # this will eventually exceed Gemini's context window.
            # For persistent context across spin-downs or very long convos, you'd need a database.
            user_gemini_chats[chat_id] = gemini_model.start_chat(history=[])
            logger.info(f"New Gemini chat session started for chat_id: {chat_id}")
        except Exception as e:
            logger.error(f"Error starting Gemini chat for chat_id {chat_id}: {e}")
            await update.message.reply_text(
                "Oops! I couldn't start a chat with Gemini. This often means the "
                "API key is invalid or the model ('gemini-2.0-flash') is not available "
                "to your Google Cloud project. Please check the Replit logs for details."
            )
            return

    try:
        response = user_gemini_chats[chat_id].send_message(user_message)
        gemini_response = response.text
        logger.info(f"Gemini response for {chat_id}: {gemini_response}")

        # Sending as plain text (no Markdown/HTML parsing)
        await update.message.reply_text(gemini_response) 

    except Exception as e:
        logger.error(f"Error interacting with Gemini for chat_id {chat_id}: {e}")
        
        # --- NEW: More specific error messages based on common Gemini errors ---
        error_message = "Oops! I encountered an error while talking to Gemini. Please try again."
        
        # Common Google API errors (often from google.api_core.exceptions)
        error_str = str(e).lower()
        if "too many tokens" in error_str or "request contains too many tokens" in error_str:
            error_message = (
                "Your message, combined with the conversation history, is too long for Gemini to process. "
                "Please try a shorter message or consider typing '/start' to clear the current conversation context."
            )
        elif "deadline exceeded" in error_str or "timeout" in error_str:
            error_message = (
                "Gemini took too long to respond. The request might be too complex or the service is busy. "
                "Please try again."
            )
        elif "model not found" in error_str or "invalid model name" in error_str:
            error_message = (
                "The Gemini model you're trying to use ('gemini-2.0-flash') might not be available "
                "to your Google Cloud project or the name is incorrect. Please verify your API key and model access."
            )
        elif "quota" in error_str or "resource exhausted" in error_str:
            error_message = (
                "I've hit a usage limit with Gemini. Please try again later or check your Google Cloud quota settings."
            )
        elif "permission denied" in error_str or "unauthenticated" in error_str:
            error_message = (
                "There's an authentication issue with the Gemini API. "
                "Please ensure your GEMINI_API_KEY is correct and has the necessary permissions."
            )
        elif "bad request" in error_str:
            # This is the catch-all for other 400s not specifically handled
            error_message = (
                "Gemini received a bad request. This might be a temporary issue or an unusual input. "
                "Please try rephrasing your message."
            )

        await update.message.reply_text(error_message)

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
