import os
import logging
from telegram import Update, ChatMember, ChatMemberUpdated
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ChatMemberHandler
import google.generativeai as genai
# import asyncio # Uncomment if using delays

# --- Uptime Pinger Imports (Flask) ---
from flask import Flask
from threading import Thread

# --- Configuration ---
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Bot reads ALLOWED_CHANNEL_IDS from environment variable set on Render.
# Example: ALLOWED_CHANNEL_IDS_STR = "-1001234567890,-1009876543210"
ALLOWED_CHANNEL_IDS_STR = os.environ.get("ALLOWED_CHANNEL_IDS", "")
ALLOWED_CHANNEL_IDS = []
if ALLOWED_CHANNEL_IDS_STR:
    try:
        ALLOWED_CHANNEL_IDS = [int(cid.strip()) for cid in ALLOWED_CHANNEL_IDS_STR.split(',') if cid.strip()]
    except ValueError:
        logging.error(f"ERROR: Could not parse one or more IDs in ALLOWED_CHANNEL_IDS ('{ALLOWED_CHANNEL_IDS_STR}'). Ensure it's a comma-separated list of integers.")
        # ALLOWED_CHANNEL_IDS will remain empty or partially filled, checks below will handle it.

# Initial logging for ALLOWED_CHANNEL_IDS (more detailed checks in main())
if not ALLOWED_CHANNEL_IDS_STR:
    logging.warning("CONFIG: ALLOWED_CHANNEL_IDS environment variable is not set or is empty.")
elif not ALLOWED_CHANNEL_IDS:
     logging.warning(f"CONFIG: ALLOWED_CHANNEL_IDS ('{ALLOWED_CHANNEL_IDS_STR}') was found but could not be parsed into valid IDs.")
else:
    logging.info(f"CONFIG: Parsed ALLOWED_CHANNEL_IDS: {ALLOWED_CHANNEL_IDS}")


MAX_USER_MESSAGE_CHARS = 3000
MAX_TELEGRAM_MESSAGE_CHARS = 4000

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Gemini Configuration ---
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    try:
        gemini_model = genai.GenerativeModel('gemini-2.0-flash') # Or your preferred model
        logger.info(f"Gemini model '{gemini_model.model_name}' configured.")
    except Exception as e:
        logger.error(f"Failed to initialize Gemini model with API key: {e}")
        gemini_model = None
else:
    logger.error("GEMINI_API_KEY not found in environment variables. Gemini features disabled.")
    gemini_model = None

user_gemini_chats = {} # Stores chat sessions: {chat_id: gemini_chat_object}

# --- Flask Web Server for Uptime Pinging ---
flask_app = Flask(__name__)
@flask_app.route('/ping')
def ping():
    logger.info("Flask: /ping endpoint was hit. Responding PONG.")
    return "PONG - Bot is alive!", 200

def run_flask_server():
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"Flask: Starting web server on host 0.0.0.0, port {port} for uptime pings.")
    try:
        flask_app.run(host='0.0.0.0', port=port)
    except Exception as e:
        logger.error(f"Flask: Web server failed to start or crashed: {e}")
    logger.info("Flask: Web server has stopped.")

# --- Helper function to split long messages ---
def split_message(text, max_length):
    chunks = []
    remaining_text = text
    while len(remaining_text) > max_length:
        split_point = remaining_text.rfind('\n', 0, max_length)
        if split_point == -1: split_point = remaining_text.rfind(' ', 0, max_length)
        if split_point == -1: split_point = max_length
        chunks.append(remaining_text[:split_point].strip())
        remaining_text = remaining_text[split_point:].strip()
    if remaining_text: chunks.append(remaining_text)
    return chunks

# --- Telegram Bot Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    bot_username = context.bot.username # Get bot's username
    welcome_message = f"Hi {user.mention_html() if user else 'there'}! I'm {bot_username}, a bot powered by Google Gemini."

    if chat.type == "private":
        await update.message.reply_html(welcome_message + " How can I help you today?")
    elif chat.type == "channel":
        if chat.id in ALLOWED_CHANNEL_IDS:
            await context.bot.send_message(
                chat_id=chat.id,
                text=f"{bot_username} is active in this channel. I will respond to messages posted here."
            )
            logger.info(f"/start command processed in allowed channel {chat.id}")
        else:
            logger.info(f"/start command in non-allowed channel {chat.id}, ignoring per ALLOWED_CHANNEL_IDS.")
    else: # Groups
        await update.message.reply_html(welcome_message + f" Mention me (e.g. @{bot_username}) to get a response.")

    logger.info(f"User {user.id if user else 'UnknownUser'} ({user.full_name if user else 'N/A'}) used /start in chat {chat.id} (type: {chat.type}).")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not gemini_model:
        if update.effective_chat.type == "private":
            await update.message.reply_text("Sorry, Gemini AI is not configured or an error occurred during its setup.")
        logger.error("Gemini AI not configured/available, skipping message processing.")
        return

    message = update.effective_message
    if not message or not message.text:
        logger.info("Received update without text message or message object.")
        return

    chat_id = message.chat.id
    user_message_text = message.text.strip()
    chat_type = message.chat.type

    should_process = False
    is_allowed_channel_interaction = False

    if chat_type == "private":
        should_process = True
        logger.info(f"Processing private message from user {message.from_user.id if message.from_user else 'Unknown'}")
    elif chat_type == "channel":
        if chat_id in ALLOWED_CHANNEL_IDS:
            should_process = True
            is_allowed_channel_interaction = True
            logger.info(f"Processing message from allowed channel {chat_id}")
        else:
            logger.info(f"Ignoring message from non-allowed channel {chat_id} (ID not in ALLOWED_CHANNEL_IDS: {ALLOWED_CHANNEL_IDS}). Message: {user_message_text[:50]}...")
            return
    elif chat_type in ["group", "supergroup"]:
        bot_username = context.bot.username
        if bot_username and f"@{bot_username}" in user_message_text:
            user_message_text = user_message_text.replace(f"@{bot_username}", "").strip()
            if user_message_text:
                should_process = True
                logger.info(f"Processing mentioned message in group {chat_id}")
            else:
                logger.info(f"Bot mentioned in group {chat_id} but no further text.")
                return
        else:
            # logger.info(f"Ignoring non-mention message in group {chat_id}") # Can be noisy
            return

    if not should_process or not user_message_text:
        if not user_message_text and should_process: # e.g. only mention in group
            logger.info(f"Empty message after processing for chat {chat_id}, not sending to Gemini.")
        # else: # Noisy if it logs every non-processed group message
            # logger.info(f"Message from chat {chat_id} (type: {chat_type}) not processed based on rules.")
        return

    if len(user_message_text) > MAX_USER_MESSAGE_CHARS:
        response_text = (
            f"The message is too long ({len(user_message_text)} chars). "
            f"Max {MAX_USER_MESSAGE_CHARS} chars. Shorter message please."
        )
        if is_allowed_channel_interaction:
            await context.bot.send_message(chat_id=chat_id, text=response_text)
        else:
            await message.reply_text(response_text)
        logger.warning(f"Chat {chat_id} msg too long: {len(user_message_text)} chars.")
        return

    context_key = chat_id
    if context_key not in user_gemini_chats:
        try:
            user_gemini_chats[context_key] = gemini_model.start_chat(history=[])
            logger.info(f"New Gemini chat session for context_key: {context_key} (type: {chat_type})")
        except Exception as e:
            logger.error(f"Error starting Gemini chat for {context_key}: {e}")
            error_msg = "Oops! Couldn't start a new chat with Gemini. Admin has been notified."
            if is_allowed_channel_interaction: await context.bot.send_message(chat_id=chat_id, text=error_msg)
            else: await message.reply_text(error_msg)
            return

    try:
        logger.info(f"Sending to Gemini for {context_key}: '{user_message_text[:100].replace(chr(10), ' ')}...'")
        api_response = user_gemini_chats[context_key].send_message(user_message_text)
        gemini_response = api_response.text
        logger.info(f"Gemini response for {context_key}: '{gemini_response[:100].replace(chr(10), ' ')}...'")

        message_parts = split_message(gemini_response, MAX_TELEGRAM_MESSAGE_CHARS)
        for i, part in enumerate(message_parts):
            part_header = f"(Part {i+1}/{len(message_parts)})\n\n" if len(message_parts) > 1 else ""
            await context.bot.send_message(chat_id=chat_id, text=f"{part_header}{part}")
            # if len(message_parts) > 1 and i < len(message_parts) - 1: await asyncio.sleep(0.5) # Optional delay

    except Exception as e:
        logger.error(f"Error interacting with Gemini for {context_key}: {e}")
        error_message = "Oops! Error talking to Gemini. Please try again."
        error_str = str(e).lower()
        if "model not found" in error_str: error_message = "Gemini model error (not found). Admin notified."
        elif "too many tokens" in error_str or "request contains too many tokens" in error_str:
            error_message = "Conversation is too long for Gemini. The bot might need its memory reset for this chat (e.g. via a /reset command - not yet implemented for channels)."
        elif "deadline exceeded" in error_str: error_message = "Gemini took too long to respond. Please try again."
        elif "permission denied" in error_str or "unauthenticated" in error_str: error_message = "Gemini API authentication error. Admin notified."
        elif "quota" in error_str or "resource exhausted" in error_str: error_message = "Gemini API usage limit reached. Please try later."


        if is_allowed_channel_interaction: await context.bot.send_message(chat_id=chat_id, text=error_message)
        else: await message.reply_text(error_message)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f'Update {update} caused error {context.error}', exc_info=context.error)
    # Avoid spamming channels with generic error messages
    if update and update.effective_chat and update.effective_chat.type == "private":
        if update.effective_message:
            await update.effective_message.reply_text("A bot error occurred! Please try again later. The admin has been notified.")

async def chat_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    my_member = update.my_chat_member
    if not my_member: return

    chat = my_member.chat
    old_status = my_member.old_chat_member.status
    new_status = my_member.new_chat_member.status
    bot_username = context.bot.username

    logger.info(f"Bot's ({bot_username}) status in chat {chat.id} ('{chat.title or chat.username or 'PrivateChat'}'): {old_status} -> {new_status}")

    if chat.type == "channel":
        if chat.id in ALLOWED_CHANNEL_IDS:
            if new_status == ChatMember.ADMINISTRATOR:
                can_post = my_member.new_chat_member.can_post_messages
                if can_post:
                    logger.info(f"Bot ({bot_username}) is now admin with post permissions in allowed channel {chat.id} ('{chat.title}').")
                    try:
                        await context.bot.send_message(chat.id, f"Hello! {bot_username} is now active and will respond to messages here.")
                    except Exception as e:
                        logger.error(f"Failed to send welcome msg to channel {chat.id}: {e}")
                else:
                    logger.warning(f"Bot ({bot_username}) is admin in allowed channel {chat.id} ('{chat.title}') BUT LACKS 'Post Messages' permission.")
            elif new_status == ChatMember.LEFT or new_status == ChatMember.KICKED:
                logger.info(f"Bot ({bot_username}) was removed or left allowed channel {chat.id} ('{chat.title}').")
                if chat.id in user_gemini_chats:
                    del user_gemini_chats[chat.id]
                    logger.info(f"Cleared Gemini chat session for channel {chat.id}.")
        else:
            logger.info(f"Bot ({bot_username}) status changed in non-allowed channel {chat.id} ('{chat.title}'). It will not respond there unless ALLOWED_CHANNEL_IDS is updated to include {chat.id}.")


def main() -> None:
    # --- Critical Startup Checks ---
    if not BOT_TOKEN:
        logger.critical("FATAL: TELEGRAM_BOT_TOKEN not found. Bot cannot start.")
        return
    if not GEMINI_API_KEY:
        logger.critical("FATAL: GEMINI_API_KEY not found. Gemini features will be disabled. Bot may start but will not respond to queries.")
    elif not gemini_model: # If key was there but model init failed (logged earlier)
         logger.warning("WARNING: Gemini model initialization failed (API key was present). Gemini features may be unavailable.")

    if not ALLOWED_CHANNEL_IDS_STR:
        logger.critical("FATAL: ALLOWED_CHANNEL_IDS environment variable is NOT SET. The bot needs this to know which channel(s) to operate in. It will not respond in any channel. Please set this variable on your hosting platform (e.g., Render).")
        # For a channel-focused bot, you might want to exit if no channels are configured:
        # return
    elif not ALLOWED_CHANNEL_IDS: # Parsed list is empty from a non-empty string (parse error)
        logger.critical(f"FATAL: ALLOWED_CHANNEL_IDS ('{ALLOWED_CHANNEL_IDS_STR}') could not be parsed into a valid list of IDs. Ensure it's a comma-separated list of integers (negative for channels). Bot will not respond in channels.")
        # return
    else:
        all_negative_and_valid = True
        for cid_val in ALLOWED_CHANNEL_IDS:
            if not isinstance(cid_val, int) or cid_val >= 0: # Channel IDs must be negative integers
                all_negative_and_valid = False
                logger.error(f"CRITICAL ERROR: Invalid Channel ID format '{cid_val}' in ALLOWED_CHANNEL_IDS. Channel IDs MUST be negative integers. This ID will be ignored or cause issues.")
        if not all_negative_and_valid:
             logger.critical("CRITICAL: One or more IDs in ALLOWED_CHANNEL_IDS are invalid (not negative integers). Please correct them. Bot may not function correctly in channels.")
             # return
        else:
            logger.info(f"Bot will attempt to operate in configured channels: {ALLOWED_CHANNEL_IDS}")
    # --- End Critical Startup Checks ---

    flask_thread = Thread(target=run_flask_server, daemon=True)
    flask_thread.start()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & (filters.UpdateType.MESSAGE | filters.UpdateType.CHANNEL_POST),
            handle_message
        )
    )
    application.add_handler(ChatMemberHandler(chat_member_handler, ChatMemberHandler.MY_CHAT_MEMBER))
    application.add_error_handler(error_handler)

    logger.info("Telegram Bot: Starting polling...")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.critical(f"Telegram Bot: Polling failed critically: {e}", exc_info=True)
    finally:
        logger.info("Telegram Bot: Polling stopped.")


if __name__ == "__main__":
    main()

