# movie_bot.py
# MongoDB Version: Stores data in Cloud so it never gets deleted.
# Requires: python-telegram-bot, motor, pymongo, flask

import os
import asyncio
import logging
from threading import Thread
from flask import Flask
from motor.motor_asyncio import AsyncIOMotorClient
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- WEB SERVER TO KEEP BOT ALIVE (Flask) ---
app_server = Flask('')

@app_server.route('/')
def home():
    return "Bot is running on MongoDB!"

def run_server():
    # Use port 8080 or the one Render assigns
    port = int(os.environ.get("PORT", 8080))
    app_server.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_server)
    t.start()

# --- CONFIGURATION ---
AUTO_DELETE_SECONDS = 120  # 2 minutes
# MongoDB Connection
MONGO_URL = os.environ.get("MONGO_URL")
if not MONGO_URL:
    print("ERROR: MONGO_URL is missing in Environment Variables!")

# Global Database Variables
client = None
db = None
collection = None

async def init_mongo():
    global client, db, collection
    if not MONGO_URL:
        return
    client = AsyncIOMotorClient(MONGO_URL)
    db = client["MovieBotDB"]          # Database Name
    collection = db["movie_items"]     # Collection (Table) Name
    print("Connected to MongoDB successfully!")

# --- HANDLERS ---

# Admin-only: Save movie items
async def save_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ADMIN_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("Unauthorized. Only admin can save movies.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: Reply to a file with: /save movie123")
        return

    movie_id = args[0].strip()
    
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to the message that contains the movie.")
        return

    from_chat_id = update.effective_chat.id
    from_message_id = update.message.reply_to_message.message_id
    caption = update.message.reply_to_message.caption or ""

    # Create document to save
    movie_data = {
        "movie_id": movie_id,
        "from_chat_id": from_chat_id,
        "from_message_id": from_message_id,
        "caption": caption
    }

    # Insert into MongoDB
    await collection.insert_one(movie_data)

    await update.message.reply_text(
        f"Saved item for movie_id='{movie_id}' in Cloud Database."
    )

# Admin-only: Clear all items for a movie code
async def clear_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ADMIN_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("Unauthorized.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /clear movie123")
        return

    movie_id = args[0].strip()

    # Delete from MongoDB
    result = await collection.delete_many({"movie_id": movie_id})

    if result.deleted_count > 0:
        await update.message.reply_text(f"Cleared {result.deleted_count} items for '{movie_id}'.")
    else:
        await update.message.reply_text(f"No items found for '{movie_id}'.")

# /start handler: Send ALL saved items for movie_id
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Open this bot via a direct link.")
        return

    movie_id = args[0].strip()

    # Fetch items from MongoDB
    # .find() returns a cursor, we convert it to a list
    cursor = collection.find({"movie_id": movie_id})
    rows = await cursor.to_list(length=None)

    if not rows:
        await update.message.reply_text("Sorry, movie not found or expired.")
        return

    sent_messages = []
    bot = context.bot
    user_chat_id = update.effective_chat.id

    for item in rows:
        from_chat_id = item['from_chat_id']
        from_message_id = item['from_message_id']
        
        try:
            sent = await bot.copy_message(
                chat_id=user_chat_id,
                from_chat_id=from_chat_id,
                message_id=from_message_id
            )
            sent_messages.append((user_chat_id, sent.message_id))
            await asyncio.sleep(0.5) # Avoid flooding
        except Exception as e:
            await bot.send_message(
                chat_id=user_chat_id, 
                text=f"(Failed to send one item: {e})"
            )

    # Schedule auto-delete
    if sent_messages:
        asyncio.create_task(delete_later(context.bot, sent_messages, AUTO_DELETE_SECONDS))

async def delete_later(bot, message_list, delay):
    await asyncio.sleep(delay)
    for chat_id, message_id in message_list:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass

# Admin-only list: Show movie codes
async def list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ADMIN_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("Unauthorized.")
        return

    # MongoDB Aggregation to count items per movie_id
    pipeline = [
        {"$group": {"_id": "$movie_id", "count": {"$sum": 1}}}
    ]
    
    text = "Saved movie codes:\n"
    found = False
    
    async for doc in collection.aggregate(pipeline):
        found = True
        text += f"{doc['_id']} → {doc['count']} item(s)\n"

    if not found:
        text = "No saved movies found in database."

    await update.message.reply_text(text)

async def main():
    # 1. Start the Flask Server (Keep Alive)
    keep_alive()

    # 2. Connect to MongoDB
    await init_mongo()

    # 3. Start Telegram Bot
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
    if not BOT_TOKEN:
        print("Set BOT_TOKEN env var.")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("save", save_handler))
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("list", list_handler))
    app.add_handler(CommandHandler("clear", clear_handler))

    print("Bot is starting...")
    
    # Using run_polling which handles the loop automatically
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    # Keep the main thread running
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
