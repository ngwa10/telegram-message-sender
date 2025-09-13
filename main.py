import asyncio
import logging
import os
import re
import sys
from dotenv import find_dotenv, load_dotenv
from telethon import TelegramClient, events
from telethon.tl.types import Message

# Set up logging [1]
logging.basicConfig(
    level=logging.DEBUG, 
    format='[%(asctime)s] %(levelname)s - %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Load environment variables [1]
if not find_dotenv():
    logger.error("No .env file found.")
    sys.exit(1)
load_dotenv(find_dotenv())

# API_ID, API_HASH, and PHONE_NUMBER [1]
try:
    API_ID = os.getenv("API_ID")
    API_HASH = os.getenv("API_HASH")
    PHONE_NUMBER = os.getenv("PHONE_NUMBER")
    if not all([API_ID, API_HASH, PHONE_NUMBER]):
        raise ValueError("API_ID, API_HASH, or PHONE_NUMBER not set in .env")
    API_ID = int(API_ID)
except (ValueError, TypeError) as e:
    logger.error(f"Failed to load environment variables: {e}")
    sys.exit(1)

# Source group IDs [1]
try:
    source_group_ids = [
        int(os.getenv("SOURCE_GROUP_ID1")),
        int(os.getenv("SOURCE_GROUP_ID2")),
        int(os.getenv("SOURCE_GROUP_ID3")),
        int(os.getenv("SOURCE_GROUP_ID4"))
    ]
    # Remove any None or empty values if some group IDs are not set [1]
    source_group_ids = [gid for gid in source_group_ids if gid is not None]
    if not source_group_ids:
        raise ValueError("No valid SOURCE_GROUP_ID found in .env")
except (ValueError, TypeError) as e:
    logger.error(f"Failed to load group IDs from .env: {e}")
    sys.exit(1)

# Create the Telegram client instance [1]
client = TelegramClient('userbot_session', API_ID, API_HASH)

# Message queue [1]
message_queue = asyncio.Queue()

# Identify the specific source for default martingale levels [1]
source4_id = int(os.getenv("SOURCE_GROUP_ID4")) if os.getenv("SOURCE_GROUP_ID4") else None


async def extract_signal(message: Message, source_group_id: int):
    """Extracts a trading signal from a message based on various formats."""
    try:
        signal = {"source_group_id": source_group_id} [1]
        text = message.message [1]
        if not text: [1]
            logger.debug(f"Message {message.id} from group {source_group_id} is empty.") [1]
            return None [1]
        
        # --- Flexible Regex Patterns --- [1]
        currency_pair_pattern = re.compile(
            r"(?:PAIR:?|CURRENCY PAIR:?|PAIR\s*:?|CURRENCY\s*PAIR\s*:?)\s*([A-Z]{3,5}[/ _-][A-Z]{3,5}(?:-[A-Z]{3})?)|"
            r"([A-Z]{3,5}[/ _-][A-Z]{3,5}(?:-[A-Z]{3,5})?)"
        ) [1]
        direction_pattern = re.compile(r"(?i)(BUY|CALL|SELL)|(🟩|🟥|🔼|🔽|🟢)") [1]
        entry_time_pattern = re.compile(
            r"(?:Entry at|ENTRY at|Entry time|TIME \(UTC.*?\)):?\s+(\d{1,2}:\d{2}(?::\d{2})?)|"
            r"([0-9]+\:[0-9]+)\s*:\s*(?:CALL|SELL)"
        ) [1]
        martingale_pattern = re.compile(r"(?:Martingale levels?|PROTECTION|M-)\s*(\d+)") [1]

        # --- Check for signal keywords --- [1]
        is_signal_text = any(
            kw in text.upper() for kw in ["BUY", "CALL", "SELL", "OTC", "EXPIRATION", "ENTRY", "TIME", "PROTECTION"]
        ) [1]
        is_signal_emoji = any(
            em in text for em in ["🟩", "🟥", "🔼", "🔽", "🟢"]
        ) [1]

        if not (is_signal_text or is_signal_emoji): [1]
            logger.debug(f"Message {message.id} from group {source_group_id} does not contain signal keywords.") [1]
            return None [1]

        # --- Extract Currency Pair --- [1]
        match = currency_pair_pattern.search(text) [1]
        if match: [1]
            signal["currency_pair"] = (match.group(1) or match.group(2)).replace(" ", "").replace("_", "/").strip().upper() [1]
        
        # --- Extract Direction --- [1]
        if "BUY" in text.upper() or "CALL" in text.upper() or "🟩" in text or "🔼" in text or "🟢" in text: [1]
            signal["direction"] = "CALL" [1]
        elif "SELL" in text.upper() or "🟥" in text or "🔽" in text: [1]
            signal["direction"] = "SELL" [1]
        
        # --- Extract Entry Time --- [1]
        match = entry_time_pattern.search(text) [1]
        if match: [1]
            signal["entry_time"] = match.group(1) or match.group(2) [1]

        # --- Extract Martingale Levels --- [1]
        martingale_levels_found = False [1]
        if source_group_id == source4_id: [1]
            signal["martingale_levels"] = 2 [1]
            martingale_levels_found = True [1]
        else: [1]
            match = martingale_pattern.search(text) [1]
            if match: [1]
                signal["martingale_levels"] = int(match.group(1)) [1]
                martingale_levels_found = True [1]
        
        if not martingale_levels_found: [1]
            signal["martingale_levels"] = 0 [1]

        # --- Check for minimum signal info --- [1]
        if not signal.get('currency_pair') or not signal.get('direction'): [1]
            logger.info(f"Message {message.id} from group {source_group_id} is not a complete signal.") [1]
            return None [1]
        
        logger.info(f"Extracted signal from message {message.id}: {signal}") [1]
        return signal [1]

    except Exception as e:
        logger.error(f"Error extracting signal from message {message.id}: {e}", exc_info=True) [1]
        return None [1]

async def process_message_queue():
    """Consumer task to process messages from the queue."""
    logger.info("Message processing queue started.") [1]
    while True: [1]
        try:
            message, source_group_id = await message_queue.get() [1]
            signal = await extract_signal(message, source_group_id) [1]
            if signal: [1]
                logger.info(f"Processed signal: {signal}") [1]

            await asyncio.sleep(1) [1]
            message_queue.task_done() [1]
        except asyncio.CancelledError: [1]
            logger.warning("Message processing queue task cancelled.") [1]
            break [1]
        except Exception as e: [1]
            logger.error(f"Error processing message from queue: {e}", exc_info=True) [1]
            message_queue.task_done() [1]

@client.on(events.NewMessage(chats=source_group_ids))
async def handler(event):
    """Event handler for new messages in specified chats."""
    try:
        if event.chat_id in source_group_ids: [1]
            message = event.message [1]
            await message_queue.put((message, event.chat_id)) [1]
    except Exception as e: [1]
        logger.error(f"Error in event handler for message {event.message.id}: {e}", exc_info=True) [1]


async def main():
    """Main function to start the bot and message processor."""
    try:
        await client.start(phone=PHONE_NUMBER) [1]
        logger.info("Client started and connected successfully.") [1]
        
        for group_id in source_group_ids: [1]
            try:
                entity = await client.get_entity(group_id) [1]
                logger.info(f"Verified membership for group: {group_id} ({entity.title})") [1]
            except ValueError: [1]
                logger.error(f"Client is not a member of group with ID: {group_id}. Cannot listen for messages.") [1]

    except Exception as e: [1]
        logger.error(f"Failed to start Telethon client: {e}", exc_info=True) [1]
        return [1]

    processor_task = asyncio.create_task(process_message_queue()) [1]
    await client.run_until_disconnected() [1]
    logger.info("Client disconnected. Shutting down...") [1]

    try:
        await asyncio.wait_for(message_queue.join(), timeout=30.0) [1]
    except asyncio.TimeoutError: [1]
        logger.warning("Queue did not empty in time. Some messages may not be processed.") [1]

    processor_task.cancel() [1]
    await asyncio.gather(processor_task, return_exceptions=True) [1]
    await client.disconnect() [1]
    logger.info("Application shutdown complete.") [1]

if __name__ == '__main__': [1]
    try:
        asyncio.run(main()) [1]
    except KeyboardInterrupt: [1]
        logger.info("Program interrupted by user. Exiting...") [1]
    except Exception as e: [1]
        logger.error(f"An unhandled error occurred: {e}", exc_info=True) [1]
        sys.exit(1) [1]
