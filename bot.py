import os
import logging
from telegram import Update, ChatMember, ChatMemberUpdated
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ChatMemberHandler
import google.generativeai as genai
import re # For regular expressions used in title formatting
# import asyncio # Uncomment if using delays between message parts

# --- Uptime Pinger Imports (Flask) ---
from flask import Flask
from threading import Thread

# --- Configuration ---
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

ALLOWED_CHANNEL_IDS_STR = os.environ.get("ALLOWED_CHANNEL_IDS", "")
ALLOWED_CHANNEL_IDS = []
if ALLOWED_CHANNEL_IDS_STR:
    try:
        ALLOWED_CHANNEL_IDS = [int(cid.strip()) for cid in ALLOWED_CHANNEL_IDS_STR.split(',') if cid.strip()]
    except ValueError:
        logging.error(f"ERROR: Could not parse one or more IDs in ALLOWED_CHANNEL_IDS ('{ALLOWED_CHANNEL_IDS_STR}'). Ensure it's a comma-separated list of integers.")

if not ALLOWED_CHANNEL_IDS_STR:
    logging.warning("CONFIG: ALLOWED_CHANNEL_IDS environment variable is not set or is empty.")
elif not ALLOWED_CHANNEL_IDS:
     logging.warning(f"CONFIG: ALLOWED_CHANNEL_IDS ('{ALLOWED_CHANNEL_IDS_STR}') was found but could not be parsed into valid IDs.")
else:
    logging.info(f"CONFIG: Parsed ALLOWED_CHANNEL_IDS: {ALLOWED_CHANNEL_IDS}")

MAX_USER_MESSAGE_CHARS = 3000
MAX_TELEGRAM_MESSAGE_CHARS = 4000 # Telegram's actual limit is 4096

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Gemini Configuration ---
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    try:
        gemini_model = genai.GenerativeModel('gemini-2.0-flash')
        logger.info(f"Gemini model '{gemini_model.model_name}' configured.")
    except Exception as e:
        logger.error(f"Failed to initialize Gemini model with API key: {e}")
        gemini_model = None
else:
    logger.error("GEMINI_API_KEY not found in environment variables. Gemini features disabled.")
    gemini_model = None

user_gemini_chats = {}

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

# --- Helper function to apply combining low line to titles ---
COMBINING_LOW_LINE = "\u0332" # Unicode for combining low line ( ͟ )

def format_titles_with_low_line(text_block: str) -> str:
    """
    Identifies markdown-style headers (#, ##, etc.) in a block of text
    and applies a combining low line to the text part of these headers.
    """
    if not text_block:
        return ""
        
    processed_lines = []
    for line in text_block.splitlines(): # splitlines correctly handles various newline chars
        match = re.match(r"^(#+)(\s+)(.*)", line) # Match one or more '#', then whitespace, then title text
        if match:
            hashes = match.group(1)      # The leading #, ##, etc.
            space = match.group(2)       # The whitespace after hashes
            title_text = match.group(3)  # The actual title content

            if title_text.strip(): # Only underline if there's actual text in the title
                underlined_title_text = "".join(c + COMBINING_LOW_LINE for c in title_text)
                processed_lines.append(f"{hashes}{space}{underlined_title_text}")
            else:
                # If line is like "#  " (hashes followed by only whitespace), keep as is.
                processed_lines.append(line)
        else:
            # Not a markdown header line
            processed_lines.append(line)
    return "\n".join(processed_lines)

# --- Helper function to split long messages ---
def split_message(text, max_chunk_length):
    """
    Splits a long string into chunks that don't exceed max_chunk_length.
    Tries to split at newlines or spaces.
    max_chunk_length is the max length for the content of each chunk.
    """
    chunks = []
    remaining_text = text
    while len(remaining_text) > max_chunk_length:
        split_point = -1
        # Try to split at the last newline within allowed length
        possible_split = remaining_text.rfind('\n', 0, max_chunk_length)
        if possible_split != -1:
            split_point = possible_split
        else:
            # No newline, try last space
            possible_split = remaining_text.rfind(' ', 0, max_chunk_length)
            if possible_split != -1:
                split_point = possible_split
        
        if split_point != -1:
            # Smart split found
            chunks.append(remaining_text[:split_point].strip())
            remaining_text = remaining_text[split_point:].strip()
        else:
            # No newline or space found, force split at max_chunk_length
            chunks.append(remaining_text[:max_chunk_length]) # No strip here, might cut mid-word
            remaining_text = remaining_text[max_chunk_length:]
            # No strip on remaining_text here to avoid losing leading spaces if next chunk starts with one

    if remaining_text.strip(): # Add any remaining part if it's not just whitespace
        chunks.append(remaining_text.strip())
    
    # Filter out any completely empty chunks that might result from stripping or multiple newlines
    return [chunk for chunk in chunks if chunk]


# --- Telegram Bot Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    bot_username = context.bot.username
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
            # logger.info(f"Ignoring message from non-allowed channel {chat_id} (ID not in ALLOWED_CHANNEL_IDS: {ALLOWED_CHANNEL_IDS}). Message: {user_message_text[:50]}...")
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
            return

    if not should_process or not user_message_text:
        if not user_message_text and should_process:
            logger.info(f"Empty message after processing for chat {chat_id}, not sending to Gemini.")
        return

    if len(user_message_text) > MAX_USER_MESSAGE_CHARS:
        response_text = (
            f"The message is too long ({len(user_message_text)} chars). "
            f"Max {MAX_USER_MESSAGE_CHARS} chars. Shorter message please."
        )
        if is_allowed_channel_interaction: await context.bot.send_message(chat_id=chat_id, text=response_text)
        else: await message.reply_text(response_text)
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
        
        raw_gemini_text = api_response.text # No strip here yet, format_titles handles lines
        if not raw_gemini_text or not raw_gemini_text.strip(): # Check if effectively empty
            logger.info(f"Gemini returned empty or whitespace-only response for {context_key}.")
            # Optionally, send a specific message back to the user/channel
            # await context.bot.send_message(chat_id=chat_id, text="I received an empty response from Gemini.")
            return

        # Apply combining low line formatting to titles
        formatted_gemini_text = format_titles_with_low_line(raw_gemini_text)
        
        logger.info(f"Formatted Gemini response for {context_key}: '{formatted_gemini_text[:150].replace(chr(10), ' ')}...'")

        # Each part_header is approx 15 chars: "(Part X/Y)\n\n"
        # So, content of each chunk should be MAX_TELEGRAM_MESSAGE_CHARS - 20 (buffer)
        max_content_length_for_chunk = MAX_TELEGRAM_MESSAGE_CHARS - 25 # Increased buffer slightly
        message_parts = split_message(formatted_gemini_text, max_content_length_for_chunk)
        
        if not message_parts: # If split_message returned empty (e.g., formatted_gemini_text was all whitespace)
            logger.info(f"Formatted Gemini text resulted in no message parts for {context_key} after splitting.")
            return

        for i, part_content in enumerate(message_parts):
            part_header = f"(Part {i+1}/{len(message_parts)})\n\n" if len(message_parts) > 1 else ""
            final_message_to_send = f"{part_header}{part_content}"
            
            if len(final_message_to_send) > MAX_TELEGRAM_MESSAGE_CHARS:
                logger.warning(
                    f"Message part {i+1} for chat {chat_id} is too long ({len(final_message_to_send)} > {MAX_TELEGRAM_MESSAGE_CHARS}). This may indicate an issue with splitting logic or MAX_TELEGRAM_MESSAGE_CHARS calculation. Attempting to send truncated."
                )
                allowed_content_len = MAX_TELEGRAM_MESSAGE_CHARS - len(part_header)
                if allowed_content_len < 0: allowed_content_len = 0
                part_content_truncated = part_content[:allowed_content_len]
                final_message_to_send = f"{part_header}{part_content_truncated}"
                logger.info(f"Truncated part sent with length: {len(final_message_to_send)}")

            await context.bot.send_message(chat_id=chat_id, text=final_message_to_send)
            # if len(message_parts) > 1 and i < len(message_parts) - 1:
            #     await asyncio.sleep(0.3) # Requires asyncio import

    except Exception as e:
        logger.error(f"Error interacting with Gemini or sending message for {context_key}: {e}", exc_info=True)
        error_message = "Oops! Error talking to Gemini or sending its response. Please try again."
        error_str = str(e).lower()
        if "model not found" in error_str: error_message = "Gemini model error (not found). Admin notified."
        elif "too many tokens" in error_str or "request contains too many tokens" in error_str:
            error_message = "Conversation is too long for Gemini. The bot might need its memory reset for this chat."
        elif "deadline exceeded" in error_str: error_message = "Gemini took too long to respond. Please try again."
        elif "permission denied" in error_str or "unauthenticated" in error_str: error_message = "Gemini API authentication error. Admin notified."
        elif "quota" in error_str or "resource exhausted" in error_str: error_message = "Gemini API usage limit reached. Please try later."

        if is_allowed_channel_interaction: await context.bot.send_message(chat_id=chat_id, text=error_message)
        else: await message.reply_text(error_message)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f'Update {update} caused error {context.error}', exc_info=context.error)
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
    if not BOT_TOKEN:
        logger.critical("FATAL: TELEGRAM_BOT_TOKEN not found. Bot cannot start.")
        return
    if not GEMINI_API_KEY:
        logger.critical("FATAL: GEMINI_API_KEY not found. Gemini features will be disabled.")
    elif not gemini_model:
         logger.warning("WARNING: Gemini model initialization failed. Gemini features may be unavailable.")

    if not ALLOWED_CHANNEL_IDS_STR:
        logger.critical("FATAL: ALLOWED_CHANNEL_IDS environment variable is NOT SET. Bot will not respond in channels.")
    elif not ALLOWED_CHANNEL_IDS:
        logger.critical(f"FATAL: ALLOWED_CHANNEL_IDS ('{ALLOWED_CHANNEL_IDS_STR}') could not be parsed. Bot will not respond in channels.")
    else:
        valid_ids = True
        for cid_val in ALLOWED_CHANNEL_IDS:
            if not isinstance(cid_val, int) or cid_val >= 0:
                valid_ids = False
                logger.error(f"CRITICAL ERROR: Invalid Channel ID format '{cid_val}'. Must be negative integer.")
        if not valid_ids:
             logger.critical("CRITICAL: One or more IDs in ALLOWED_CHANNEL_IDS are invalid. Bot may not function in channels.")
        else:
            logger.info(f"Bot will attempt to operate in configured channels: {ALLOWED_CHANNEL_IDS}")

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
