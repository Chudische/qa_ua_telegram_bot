import os
import pickle
from datetime import datetime, time
from dotenv import load_dotenv
import logging
from typing import Optional, Tuple


from telegram import Chat, ChatMember, ChatMemberUpdated, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


# Load environment variables from dotenv file
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path)

TOKEN: str = os.getenv("TOKEN")
CHAT_ID: int = int(os.getenv("CHAT_ID"))
DB_FILE = "./members"

with open("welcome.html", "r") as file:
    WELCOME_MESSAGE = file.read()


with open("notification.html", "r") as file:
    NOTIFICATION_MESSAGE = file.read()


class Member(object):
    def __init__(self, name, username):
        self.name = name
        self.username = username
        self.join_date = datetime.now()
        self.notified = False

    def __str__(self):
        return f"{self.name} з ніком {self.username} приєднався/лася {self.join_date.date()} "


if not os.path.exists(DB_FILE):
    new_members: dict = {}
    kick_list: dict = {}
else:
    with open(DB_FILE, "rb") as db_file:
        db = pickle.load(db_file)
        new_members = db["new_members"]
        kick_list = db["kick_list"]


def save_db():
    with open(DB_FILE, "wb") as db_file:
        combine = dict(new_members=new_members, kick_list=kick_list)
        pickle.dump(combine, db_file)


# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def notify_members(context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Start notifying members")
    users = ""
    for member in list(new_members.keys()):
        delta = datetime.now() - new_members[member].join_date
        if delta.days >= 2 and not new_members[member].notified:
            users += f'<a href="tg://user?id={new_members[member].id}">{new_members[member].name}</a>, '
            new_members[member].notified = True
        elif delta.days >= 3 and new_members[member].notified:
            kick_list[member] = new_members.pop(member)
    save_db()
    if not users:
        return
    await context.bot.send_message(chat_id=CHAT_ID, text=NOTIFICATION_MESSAGE.format(users), parse_mode=ParseMode.HTML)


async def send_test_notification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends test notification which should be sent daily"""
    await context.bot.send_message(chat_id=update.message.chat_id, text="Надсилаю тестове нагадування")

    context.job_queue.run_once(notify_members, 2)


def extract_status_change(chat_member_update: ChatMemberUpdated) -> Optional[Tuple[bool, bool]]:
    """Takes a ChatMemberUpdated instance and extracts whether the 'old_chat_member' was a member
    of the chat and whether the 'new_chat_member' is a member of the chat. Returns None, if
    the status didn't change.
    """
    status_change = chat_member_update.difference().get("status")
    old_is_member, new_is_member = chat_member_update.difference().get("is_member", (None, None))

    if status_change is None:
        return None

    old_status, new_status = status_change
    was_member = old_status in [
        ChatMember.MEMBER,
        ChatMember.OWNER,
        ChatMember.ADMINISTRATOR,
    ] or (old_status == ChatMember.RESTRICTED and old_is_member is True)
    is_member = new_status in [
        ChatMember.MEMBER,
        ChatMember.OWNER,
        ChatMember.ADMINISTRATOR,
    ] or (new_status == ChatMember.RESTRICTED and new_is_member is True)

    return was_member, is_member


async def show_kick_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show members that don't want to follow the rules"""
    result = ""
    for member in kick_list:
        result += f"{kick_list[member]}"
    if not kick_list:
        result = "Список штрафників наразі пустий..."
    await update.effective_message.reply_text(result, parse_mode=ParseMode.HTML)


async def show_new_members_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show new members"""
    result = ""
    for member in new_members:
        result += f"{new_members[member]}\n"
    if not new_members:
        result = "Я не маю інформації про нових членів групи"
    await update.effective_message.reply_text(result, parse_mode=ParseMode.HTML)


async def clean_new_members_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove all from new members list"""
    new_members.clear()
    save_db()
    await update.effective_message.reply_text("Список нових членів группи очищено", parse_mode=ParseMode.HTML)


async def clean_kick_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove all from new members list"""
    kick_list.clear()
    save_db()
    await update.effective_message.reply_text("Список штрафників очищено", parse_mode=ParseMode.HTML)


async def greet_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Greets new users in chats and announces when someone leaves"""
    result = extract_status_change(update.chat_member)

    if result is None:
        return

    was_member, is_member = result
    new_chat_member = update.chat_member.new_chat_member.user

    if not was_member and is_member:
        new_members[new_chat_member.id] = Member(name=new_chat_member.full_name, username=new_chat_member.username)
        user = f'<a href="tg://user?id={new_chat_member.id}">{new_chat_member.name}</a>'
        logger.info("%s added to group", update.effective_user.username)
        save_db()
        await update.effective_chat.send_message(
            WELCOME_MESSAGE.format(user),
            parse_mode=ParseMode.HTML,
        )


async def start_private_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Greets the user and records that they started a chat with the bot if it's a private chat.
    Since no `my_chat_member` update is issued when a user starts a private chat with the bot
    for the first time, we have to track it explicitly here.
    """
    user_name = update.effective_user.full_name
    chat = update.effective_chat
    if chat.type != Chat.PRIVATE or chat.id in context.bot_data.get("user_ids", set()):
        return

    logger.info("%s started a private chat with the bot", user_name)
    context.bot_data.setdefault("user_ids", set()).add(chat.id)

    await update.effective_message.reply_text(
        f"Вітаю {user_name}! В данний час у мене не дуже багато функцій... Але якщо ти знаєш команди, я залюбки їх виконаю!"
    )


async def check_new_member_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pop new member from kick list after first message"""
    user_id = update.effective_user.id
    chat = update.effective_chat.id
    if chat == CHAT_ID and user_id in new_members:
        new_members.pop(user_id)
    elif chat == CHAT_ID and user_id in kick_list:
        kick_list.pop(user_id)
    else:
        print(f"{chat} != {CHAT_ID} and {user_id} not in {new_members.keys()}")
        return
    save_db()


async def start_tracking_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    job = context.chat_data.get("polling_job")
    if job:
        await update.message.reply_text("Я вже слідкую за новими членами групи...")
    else:
        noon = time(hour=12, minute=00, second=00)
        job = context.job_queue.run_daily(notify_members, time=noon)
        context.chat_data["polling_job"] = job
        await update.message.reply_text("Добре! Починаю слідкувати за новими членами группи.")


async def stop_tracking_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    job = context.chat_data.get("polling_job")
    if job:
        job.schedule_removal()
        del context.chat_data["polling_job"]
        await update.message.reply_text("Добре! Більше не слідкую за новими членами групи.")
    else:
        await update.message.reply_text("Я ще не отримував команди слідкувати... ")


def main() -> None:
    """Start the bot."""
    # Create the Application and pass it your bot's token.
    application = Application.builder().token(TOKEN).build()

    # Show members from kick list or new members
    application.add_handler(CommandHandler("show_kick_list", show_kick_list))
    application.add_handler(CommandHandler("show_new_members", show_new_members_list))

    # Clean kick list or new members list
    application.add_handler(CommandHandler("clean_new_members_list", clean_new_members_list))
    application.add_handler(CommandHandler("clean_kick_list", clean_kick_list))

    # Start/Stop tracking of new members
    application.add_handler(CommandHandler("start_tracking", start_tracking_members))
    application.add_handler(CommandHandler("stop_tracking", stop_tracking_members))

    # Notify members
    application.add_handler(CommandHandler("send_test_notification", send_test_notification))

    # Handle members joining/leaving chats.
    application.add_handler(ChatMemberHandler(greet_chat_members, ChatMemberHandler.CHAT_MEMBER))

    # on non command i.e message - check if message came from new member on Telegram
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, check_new_member_message))

    # Interpret any other command or text message as a start of a private chat.
    # This will record the user as being in a private chat with bot.
    # application.add_handler(MessageHandler(filters.ALL, start_private_chat))

    # Run the bot until the user presses Ctrl-C
    # We pass 'allowed_updates' handle *all* updates including `chat_member` updates
    # To reset this, simply pass `allowed_updates=[]`
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()


