import asyncio
import logging
import os
import random
import re
from dotenv import load_dotenv
from telethon import TelegramClient, events

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# API_ID and API_HASH
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
PHONE_NUMBER = os.getenv("PHONE_NUMBER")

# Source group IDs
source_group_id1 = int(os.getenv("SOURCE_GROUP_ID1"))
source_group_id2 = int(os.getenv("SOURCE_GROUP_ID2"))
source_group_id3 = int(os.getenv("SOURCE_GROUP_ID3"))
source_group_id4 = int(os.getenv("SOURCE_GROUP_ID4"))

source_group_ids = [source_group_id1, source_group_id2, source_group_id3, source_group_id4]

# Create the Telegram client
client = TelegramClient('userbot_session', API_ID, API_HASH)

# Message queue
message_queue = asyncio.Queue()

async def extract_signal(message):
    signal = {}
    text = message.message

    # Extract currency pair
    match = re.search(r"([A-Z]{3,5}/[A-Z]{3,5})", text)
    if match:
        signal["currency_pair"] = match.group(0)

    # Extract direction (Buy or Sell)
    if "BUY" in text.upper() or "🟩" in text:
        signal["direction"] = "BUY"
    elif "SELL" in text.upper() or "🟥" in text:
        signal["direction"] = "SELL"

    # Extract entry time
    match = re.search(r"(\d{2}:\d{2})", text)
    if match:
        signal["entry_time"] = match.group(0)

    # Extract martingale levels
    levels = []
    for line in text.splitlines():
        match = re.search(r"(\d+️⃣ level at (\d{2}:\d{2}))", line)
        if match:
            levels.append({"level": len(levels) + 1, "time": match.group(2)})
    signal["martingale_levels"] = levels

    return signal

async def process_message():
    while True:
        message = await message_queue.get()
        signal = await extract_signal(message)
        logger.info(f"Extracted signal: {signal}")
        await asyncio.sleep(1)  # Add a small delay
        message_queue.task_done()

async def add_message_to_queue(message):
    await message_queue.put(message)

async def main():
    await client.start(PHONE_NUMBER)
    logger.info("Client Created")

    # Start the message processing task
    asyncio.create_task(process_message())

    # Set up event handler
    @client.on(events.NewMessage(chats=source_group_ids))
    async def handler(event):
        message = event.message
        logger.info(f"New message detected in source group with ID {event.chat_id}")
        await add_message_to_queue(message)

    # Run the event handler until the script is stopped
    await client.run_until_disconnected()

with client:
    client.loop.run_until_complete(main())
