import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
# Removed: from telegram.constants import ParseMode # Still no ParseMode for plain text
import google.generativeai as genai
# If you decide to add a delay between messages (optional), uncomment this:
# import asyncio

# --- Configuration ---
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Maximum input message length for your bot (to prevent sending excessively long queries to Gemini)
MAX_USER_MESSAGE_CHARS = 3000

# Maximum output message length for Telegram (Telegram's API limit is 4096 characters)
# We'll use a slightly smaller value to be safe and leave room for "Part X/Y" notes.
MAX_TELEGRAM_MESSAGE_CHARS = 4000

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Gemini Configuration ---
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

    # Using 'gemini-2.0-flash' as per your screenshot and preference.
    # If the "model not found" error persists, this 'gemini-2.0-flash'
    # might not be available to your specific Google Cloud project/account yet.
    # In that case, you'd need to try 'gemini-1.5-flash-latest' or 'gemini-1.5-pro-latest'.
    gemini_model = genai.GenerativeModel('gemini-2.0-flash')

    logger.info(f"Gemini model '{gemini_model.model_name}' configured.")
else:
    logger.error("GEMINI_API_KEY not found in environment variables.")
    gemini_model = None

# Dictionary to store chat sessions for conversation context
user_gemini_chats = {}


# --- Helper function to split long messages ---
def split_message(text, max_length):
    """Splits a long string into chunks that don't exceed max_length,
    trying to split at the last newline or space character.
    """
    chunks = []
    remaining_text = text

    while len(remaining_text) > max_length:
        # Find the last newline within the allowed length
        split_point = remaining_text.rfind('\n', 0, max_length)
        if split_point == -1:  # No newline found, try last space
            split_point = remaining_text.rfind(' ', 0, max_length)
        if split_point == -1:  # No space found either, force split at max_length
            split_point = max_length

        chunks.append(remaining_text[:split_point].strip())
        remaining_text = remaining_text[split_point:].strip()

    if remaining_text:  # Add any remaining part as the last chunk
        chunks.append(remaining_text)

    return chunks


# --- Telegram Bot Handlers ---


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message when the command /start is issued."""
    user = update.effective_user
    await update.message.reply_html(
        f"Hi {user.mention_html()}! I'm a bot powered by Google Gemini 2.0 Flash. "
        "Send me a message and I'll try to respond!", )
    logger.info(f"User {user.id} ({user.full_name}) started the bot.")


async def handle_message(update: Update,
                         context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles incoming text messages and sends them to Gemini."""
    if not gemini_model:
        await update.message.reply_text(
            "Sorry, Gemini AI is not configured. Please check the API key and model name."
        )
        return

    chat_id = update.effective_chat.id
    user_message = update.message.text
    logger.info(f"User {chat_id} sent message: {user_message}")

    # --- Check user message length before sending to Gemini ---
    if len(user_message) > MAX_USER_MESSAGE_CHARS:
        await update.message.reply_text(
            f"Your message is too long ({len(user_message)} characters). "
            f"I can only process messages up to {MAX_USER_MESSAGE_CHARS} characters at a time. "
            "Please try a shorter message.")
        logger.warning(
            f"User {chat_id} sent message exceeding max input length: {len(user_message)} chars."
        )
        return  # Stop processing this message

    if chat_id not in user_gemini_chats:
        try:
            user_gemini_chats[chat_id] = gemini_model.start_chat(history=[])
            logger.info(
                f"New Gemini chat session started for chat_id: {chat_id}")
        except Exception as e:
            logger.error(
                f"Error starting Gemini chat for chat_id {chat_id}: {e}")
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

        # --- NEW: Split Gemini's response into multiple messages if too long ---
        if len(gemini_response) > MAX_TELEGRAM_MESSAGE_CHARS:
            logger.warning(
                f"Gemini response for {chat_id} is too long ({len(gemini_response)} chars). Splitting into multiple messages."
            )

            message_parts = split_message(gemini_response,
                                          MAX_TELEGRAM_MESSAGE_CHARS)

            for i, part in enumerate(message_parts):
                # Add a "Part X/Y" header if there's more than one part
                part_header = f"(Part {i+1}/{len(message_parts)})\n\n" if len(
                    message_parts) > 1 else ""
                await update.message.reply_text(f"{part_header}{part}")
                # Optional: Add a small delay between messages to avoid potential flooding limits
                # and make it easier for Telegram to deliver them.
                # await asyncio.sleep(0.5)
        else:
            # Send as a single plain text message
            await update.message.reply_text(gemini_response)

    except Exception as e:
        logger.error(
            f"Error interacting with Gemini for chat_id {chat_id}: {e}")

        error_message = "Oops! I encountered an error while talking to Gemini. Please try again."

        error_str = str(e).lower()
        if "too many tokens" in error_str or "request contains too many tokens" in error_str:
            error_message = (
                "Your message, combined with the conversation history, is too long for Gemini to process. "
                "Please try a shorter message or consider typing '/start' to clear the current conversation context."
            )
        elif "deadline exceeded" in error_str or "timeout" in error_str:
            error_message = (
                "Gemini took too long to respond. The request might be too complex or the service is busy. "
                "Please try again.")
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
            error_message = (
                "Gemini received a bad request. This might be a temporary issue or an unusual input. "
                "Please try rephrasing your message.")

        await update.message.reply_text(error_message)


async def error_handler(update: Update,
                        context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and send a message to the user."""
    logger.warning(f'Update {update} caused error {context.error}')
    if update.effective_message:
        text = "An error occurred! Please try again later."
        await update.effective_message.reply_text(text)


def main() -> None:
    """Starts the bot."""
    if not BOT_TOKEN:
        logger.critical(
            "TELEGRAM_BOT_TOKEN not found in environment variables. Bot cannot start."
        )
        return

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    logger.info("Bot starting polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)
    logger.info("Bot stopped.")


if __name__ == "__main__":
    main()
