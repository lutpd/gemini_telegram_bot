import os
import logging
import re
from telegram import Update, ChatMember, ChatMemberUpdated
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ChatMemberHandler
import google.generativeai as genai
from flask import Flask
from threading import Thread

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ALLOWED_CHANNEL_IDS_STR = os.environ.get("ALLOWED_CHANNEL_IDS", "")
ALLOWED_CHANNEL_IDS = [int(cid.strip()) for cid in ALLOWED_CHANNEL_IDS_STR.split(',') if cid.strip()] if ALLOWED_CHANNEL_IDS_STR else []

MAX_USER_MESSAGE_CHARS = 3000
MAX_TELEGRAM_MESSAGE_CHARS = 4000

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel('gemini-2.0-flash')
else:
    gemini_model = None

user_gemini_chats = {}

flask_app = Flask(__name__)

@flask_app.route('/ping')
def ping():
    return "PONG - Bot is alive!", 200

def run_flask_server():
    flask_app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

def escape_markdown(text: str) -> str:
    return re.sub(r'([_\*\[\]()~`>#+\-=|{}.!])', r'\\\1', text)

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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    bot_username = context.bot.username
    welcome_message = f"Hi {user.mention_html() if user else 'there'}! I'm {bot_username}, a bot powered by Google Gemini."

    if chat.type == "private":
        await update.message.reply_html(welcome_message + " How can I help you today?")
    elif chat.type == "channel":
        if chat.id in ALLOWED_CHANNEL_IDS:
            await context.bot.send_message(chat_id=chat.id, text=f"{bot_username} is active in this channel. I will respond to messages posted here.", parse_mode="MarkdownV2")
    else:
        await update.message.reply_html(welcome_message + f" Mention me (e.g. @{bot_username}) to get a response.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not gemini_model:
        if update.effective_chat.type == "private":
            await update.message.reply_text("Gemini AI not configured.")
        return

    message = update.effective_message
    if not message or not message.text:
        return

    chat_id = message.chat.id
    user_message_text = message.text.strip()
    chat_type = message.chat.type

    should_process = False
    is_allowed_channel = False

    if chat_type == "private":
        should_process = True
    elif chat_type == "channel" and chat_id in ALLOWED_CHANNEL_IDS:
        should_process = True
        is_allowed_channel = True
    elif chat_type in ["group", "supergroup"]:
        bot_username = context.bot.username
        if bot_username and f"@{bot_username}" in user_message_text:
            user_message_text = user_message_text.replace(f"@{bot_username}", "").strip()
            if user_message_text:
                should_process = True

    if not should_process or not user_message_text:
        return

    if len(user_message_text) > MAX_USER_MESSAGE_CHARS:
        warning = f"The message is too long ({len(user_message_text)} chars). Max {MAX_USER_MESSAGE_CHARS} chars."
        await context.bot.send_message(chat_id=chat_id, text=escape_markdown(warning), parse_mode="MarkdownV2")
        return

    if chat_id not in user_gemini_chats:
        try:
            user_gemini_chats[chat_id] = gemini_model.start_chat(history=[])
        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=escape_markdown("Error starting Gemini chat."), parse_mode="MarkdownV2")
            return

    try:
        response = user_gemini_chats[chat_id].send_message(user_message_text)
        gemini_response = response.text
        chunks = split_message(gemini_response, MAX_TELEGRAM_MESSAGE_CHARS)
        for i, part in enumerate(chunks):
            header = f"(Part {i+1}/{len(chunks)})\n\n" if len(chunks) > 1 else ""
            await context.bot.send_message(chat_id=chat_id, text=escape_markdown(header + part), parse_mode="MarkdownV2")
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=escape_markdown("Error communicating with Gemini."), parse_mode="MarkdownV2")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update and update.effective_chat and update.effective_chat.type == "private":
        if update.effective_message:
            await update.effective_message.reply_text("Bot error. Please try again later.")

def main():
    if not BOT_TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN not set.")
        return

    flask_thread = Thread(target=run_flask_server, daemon=True)
    flask_thread.start()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
