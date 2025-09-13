import asyncio
import logging
import os
import random
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

# Source group ID
source_group_id = int(os.getenv("SOURCE_GROUP_ID", -1002944619696))

# Target group IDs
target_group_ids_str = os.getenv("TARGET_GROUP_IDS")
try:
    target_group_ids = list(map(int, target_group_ids_str.split(',')))
except ValueError:
    target_group_ids = []
    logger.error("Invalid target group IDs")

# Create the Telegram client
client = TelegramClient('userbot_session', API_ID, API_HASH)

# Message queue
message_queue = asyncio.Queue()

async def process_message():
    while True:
        message = await message_queue.get()
        message_id = message.id
        for group_id in target_group_ids:
            try:
                await client.forward_messages(group_id, message)
                logger.info(f"Message ID:{message_id} forwarded to group with ID {group_id}")
            except Exception as e:
                logger.error(f"Error forwarding message to group with ID {group_id}: {e}")
        random_number = random.randint(int(os.getenv("MIN_TIME")), int(os.getenv("MAX_TIME")))
        logger.info(f"Time interval randomly chosen is: {random_number}")
        await asyncio.sleep(random_number)
        await message_queue.put(message)
        message_queue.task_done()

async def add_message_to_queue(message):
    await message_queue.put(message)

async def main():
    await client.start(PHONE_NUMBER)
    logger.info("Client Created")

    # Start the message processing task
    asyncio.create_task(process_message())

    # Set up event handler
    @client.on(events.NewMessage(chats=source_group_id))
    async def handler(event):
        message = event.message
        logger.info(f"New message detected in source group with ID {source_group_id}")
        await add_message_to_queue(message)

    # Run the event handler until the script is stopped
    await client.run_until_disconnected()

with client:
    client.loop.run_until_complete(main())
