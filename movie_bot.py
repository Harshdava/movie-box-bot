# movie_bot.py
# Multi-item support: save multiple storage messages under the same movie_id.
# Requires: python-telegram-bot==20.6, aiosqlite
# Uses BOT_TOKEN and ADMIN_USER_ID from environment (Replit secrets).

import os
import asyncio
import aiosqlite
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

DB = "movies.db"
AUTO_DELETE_SECONDS = 120  # 2 minutes


async def init_db():
    # Create new items table. If old single-table exists, migrate it.
    async with aiosqlite.connect(DB) as db:
        # New table to store multiple items per movie_id
        await db.execute("""
        CREATE TABLE IF NOT EXISTS movie_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            movie_id TEXT,
            from_chat_id INTEGER,
            from_message_id INTEGER,
            caption TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        # Legacy table (if exists) may be called 'movies' from older version.
        # If it exists, migrate that single-row-per-movie table into movie_items.
        try:
            cur = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='movies'"
            )
            row = await cur.fetchone()
            if row:
                # migrate rows
                cur2 = await db.execute(
                    "SELECT movie_id, from_chat_id, from_message_id, caption FROM movies"
                )
                old_rows = await cur2.fetchall()
                for r in old_rows:
                    await db.execute(
                        "INSERT INTO movie_items (movie_id, from_chat_id, from_message_id, caption) VALUES (?, ?, ?, ?)",
                        (r[0], r[1], r[2], r[3] or ""))
                # optional: drop old table to avoid double-migration on restart
                await db.execute("DROP TABLE IF EXISTS movies")
        except Exception:
            # ignore migration errors but continue
            pass
        await db.commit()


# Admin-only: save mapping by replying to a message in the storage channel
async def save_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ADMIN_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
    if user_id != ADMIN_ID:
        await update.message.reply_text(
            "Unauthorized. Only admin can save movies.")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: reply to the movie message in the storage channel with: /save movie123"
        )
        return
    movie_id = args[0].strip()
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "Reply to the message that contains the movie (in the storage channel)."
        )
        return

    from_chat_id = update.effective_chat.id
    from_message_id = update.message.reply_to_message.message_id
    caption = update.message.reply_to_message.caption or ""

    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO movie_items (movie_id, from_chat_id, from_message_id, caption) VALUES (?, ?, ?, ?)",
            (movie_id, from_chat_id, from_message_id, caption))
        await db.commit()

    await update.message.reply_text(
        f"Saved item for movie_id='{movie_id}' from chat {from_chat_id} msg {from_message_id}."
    )


# Admin-only: clear all items for a movie code
async def clear_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ADMIN_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
    if user_id != ADMIN_ID:
        await update.message.reply_text("Unauthorized.")
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: /clear movie123  — deletes all saved items for that code.")
        return
    movie_id = args[0].strip()
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM movie_items WHERE movie_id = ?",
            (movie_id, ))
        row = await cur.fetchone()
        count = row[0] if row else 0
        if count == 0:
            await update.message.reply_text(f"No items found for '{movie_id}'."
                                            )
            return
        await db.execute("DELETE FROM movie_items WHERE movie_id = ?",
                         (movie_id, ))
        await db.commit()
    await update.message.reply_text(f"Cleared {count} items for '{movie_id}'.")


# /start handler: send ALL saved items for movie_id
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "Open this bot from a channel poster button.")
        return
    movie_id = args[0].strip()

    # fetch all items for this movie_id (in insertion order)
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT id, from_chat_id, from_message_id, caption FROM movie_items WHERE movie_id = ? ORDER BY id ASC",
            (movie_id, ))
        rows = await cur.fetchall()

    if not rows:
        await update.message.reply_text("Sorry, movie not found or expired.")
        return

    sent_messages = []
    bot = context.bot
    user_chat_id = update.effective_chat.id

    for item in rows:
        item_id, from_chat_id, from_message_id, caption = item
        try:
            sent = await bot.copy_message(chat_id=user_chat_id,
                                          from_chat_id=from_chat_id,
                                          message_id=from_message_id)
            sent_messages.append((user_chat_id, sent.message_id))
            # small sleep to avoid hammering API for many large files
            await asyncio.sleep(0.5)
        except Exception as e:
            # if one item fails, continue to next but inform the user once
            await bot.send_message(
                chat_id=user_chat_id,
                text=f"(Failed to send one item for '{movie_id}': {e})")

    # schedule deletion of all sent messages
    async def delete_later(lst, delay):
        await asyncio.sleep(delay)
        for chat_id, message_id in lst:
            try:
                await bot.delete_message(chat_id=chat_id,
                                         message_id=message_id)
            except Exception:
                pass

    if sent_messages:
        asyncio.create_task(delete_later(sent_messages, AUTO_DELETE_SECONDS))


# Admin-only list: show movie codes and counts
async def list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ADMIN_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
    if user_id != ADMIN_ID:
        await update.message.reply_text("Unauthorized.")
        return
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT movie_id, COUNT(*) FROM movie_items GROUP BY movie_id")
        rows = await cur.fetchall()
    if not rows:
        await update.message.reply_text("No saved movie items.")
        return
    text = "Saved movie codes and counts:\n"
    for r in rows:
        text += f"{r[0]}  → {r[1]} item(s)\n"
    await update.message.reply_text(text)


async def main():
    await init_db()
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
    if not BOT_TOKEN:
        print("Set BOT_TOKEN env var.")
        return
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("save", save_handler))
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("list", list_handler))
    app.add_handler(CommandHandler("clear", clear_handler))
    print("Bot starting...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
