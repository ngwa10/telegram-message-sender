import asyncio
import logging
import os
import re
from dotenv import load_dotenv
from telethon import TelegramClient, events

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
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
    try:
        signal = {}
        text = message.message

        # Extract currency pair
        match = re.search(r"([A-Z]{3,5}/[A-Z]{3,5}|[A-Z]{3,5}[A-Z]{3,5})", text)
        if match:
            signal["currency_pair"] = match.group(0).replace("-", "/")
            logger.info(f"Extracted currency pair: {signal['currency_pair']}")

        # Extract direction (Buy or Sell or Call)
        if "BUY" in text.upper() or "🟩" in text or "CALL" in text.upper() or "🔼" in text:
            signal["direction"] = "BUY" if "BUY" in text.upper() or "🟩" in text else "CALL"
            logger.info(f"Extracted direction: {signal['direction']}")
        elif "SELL" in text.upper() or "🟥" in text:
            signal["direction"] = "SELL"
            logger.info(f"Extracted direction: {signal['direction']}")

        # Extract entry time
        match = re.search(r"(\d{2}:\d{2})", text)
        if match:
            signal["entry_time"] = match.group(0)
            logger.info(f"Extracted entry time: {signal['entry_time']}")

        # Assign default martingale levels for the specific channel
        if message.chat_id == source_group_id4:
            signal["martingale_levels"] = [{"level": 1, "time": None}, {"level": 2, "time": None}]
            logger.info(f"Assigned default martingale levels: {signal['martingale_levels']}")
        else:
            levels = []
            for line in text.splitlines():
                match = re.search(r"(\d+️⃣ level at (\d{2}:\d{2}))", line)
                if match:
                    levels.append({"level": len(levels) + 1, "time": match.group(2)})
            signal["martingale_levels"] = levels
            logger.info(f"Extracted martingale levels: {signal.get('martingale_levels')}")

        return signal
    except Exception as e:
        logger.error(f"Error extracting signal: {e}")
        return None

async def process_message():
    while True:
        try:
            message = await message_queue.get()
            signal = await extract_signal(message)
            if signal:
                logger.info(f"Extracted signal: {signal}")
            await asyncio.sleep(1)  # Add a small delay
            message_queue.task_done()
        except Exception as e:
            logger.error(f"Error processing message: {e}")

async def add_message_to_queue(message):
    try:
        await message_queue.put(message)
        logger.info(f"Added message to queue: {message.id}")
    except Exception as e:
        logger.error(f"Error adding message to queue: {e}")

async def main():
    try:
        await client.start(PHONE_NUMBER)
        logger.info("Client Created")

        # Start the message processing task
        asyncio.create_task(process_message())

        # Set up event handler
        @client.on(events.NewMessage(chats=source_group_ids))
        async def handler(event):
            try:
                message = event.message
                logger.info(f"New message detected in source group with ID {event.chat_id}: {message.id}")
                await add_message_to_queue(message)
            except Exception as e:
                logger.error(f"Error handling new message: {e}")

        # Run the event handler until the script is stopped
        await client.run_until_disconnected()
    except Exception as e:
        logger.error(f"Error in main: {e}")

with client
