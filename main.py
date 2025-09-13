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
        match = re.search(r"([A-Z]{3,5}/[A-Z]{3,5}-[A-Z]{3})", text)
        if match:
            signal["currency_pair"] = match.group(0)
            logger.info(f"Extracted currency pair: {signal['currency_pair']}")
        else:
            match = re.search(r"([A-Z]{3,5}[A-Z]{3,5}-[A-Z]{3})", text)
            if match:
                signal["currency_pair"] = match.group(0)
                logger.info(f"Extracted currency pair: {signal['currency_pair']}")

        # Extract direction (Buy or Sell or Call)
        if "CALL" in text.upper() or "🟩" in text or "🔼" in text:
            signal["direction"] = "CALL"
            logger.info(f"Extracted direction: {signal['direction']}")
        elif "SELL" in text.upper() or "🟥" in text:
            signal["direction"] = "SELL"
            logger.info(f"Extracted direction: {signal['direction']}")

        # Extract entry time
        match = re.search(r"(\d{2}:\d{2}:\d{2})", text)
        if match:
            signal["entry_time"] = match.group(0)
            logger.info(f"Extracted entry time: {signal['entry_time']}")

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

@client.on(events.NewMessage(chats=source_group_ids))
async def handler(event):
    try:
        message = event.message
        logger.info(f"New message detected in source group with ID {event.chat_id}: {message.id}")
        await add_message_to_queue(message)
    except Exception as e:
        logger.error(f"Error handling new message: {e}")

forward_from_channel_id = int(os.getenv("FORWARD_FROM_CHANNEL_ID", -1002197451859))
@client.on(events.NewMessage(chats=forward_from_channel_id))
async def forward_handler(event):
    try:
        message = event.message
        logger.info(f"New message detected in forward channel with ID {event.chat_id}: {message.id}")
        await client.forward_messages(source_group_id1, message)
        logger.info(f"Message forwarded to source group with ID {source_group_id1}")
    except Exception as e:
        logger.error(f"Error forwarding message: {e}")

async def main():
    try:
        await client.start(PHONE_NUMBER)
        logger.info("Client Created")

        # Start the message processing task
        asyncio.create_task(process_message())

        # Run the event handler until the script is stopped
        await client.run_until_disconnected()
    except Exception as e:
        logger.error(f"Error in main: {e}")

loop = asyncio.get_event_loop()
loop.run_until_complete(main())
