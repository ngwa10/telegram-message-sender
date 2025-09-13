import asyncio
import logging
import os
import re
import sys
from dotenv import find_dotenv, load_dotenv
from telethon import TelegramClient, events
from telethon.tl.types import Message, PeerChannel

# --- Logging setup ---
logging.basicConfig(
    level=logging.INFO,  # Change to DEBUG for more detailed output
    format='[%(asctime)s] %(levelname)s - %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# --- Load environment variables ---
if not find_dotenv():
    logger.error("No .env file found.")
    sys.exit(1)
load_dotenv(find_dotenv())

# --- API Credentials and Phone Number ---
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

# --- Source Group IDs ---
try:
    source_group_ids = [
        int(gid) for gid in [
            os.getenv("SOURCE_GROUP_ID1"),
            os.getenv("SOURCE_GROUP_ID2"),
            os.getenv("SOURCE_GROUP_ID3"),
            os.getenv("SOURCE_GROUP_ID4")
        ] if gid is not None and gid.strip()
    ]
    if not source_group_ids:
        raise ValueError("No valid SOURCE_GROUP_ID found in .env")
except (ValueError, TypeError) as e:
    logger.error(f"Failed to load group IDs from .env: {e}")
    sys.exit(1)

# --- Telegram Client and Queue ---
client = TelegramClient('userbot_session', API_ID, API_HASH)
message_queue = asyncio.Queue()

# Identify the specific source for default martingale levels
source4_id = int(os.getenv("SOURCE_GROUP_ID4")) if os.getenv("SOURCE_GROUP_ID4") else None

async def extract_signal(message: Message, source_group_id: int):
    """Extracts a trading signal from a message based on various formats."""
    try:
        signal = {"source_group_id": source_group_id}
        text = message.message
        if not text:
            logger.debug(f"Message {message.id} from group {source_group_id} is empty.")
            return None
        
        # --- Flexible Regex Patterns ---
        currency_pair_pattern = re.compile(
            r"(?:PAIR:?|CURRENCY PAIR:?|PAIR\s*:?|CURRENCY\s*PAIR\s*:?)\s*([A-Z]{3,5}[/ _-][A-Z]{3,5}(?:-[A-Z]{3})?)|"
            r"([A-Z]{3,5}[/ _-][A-Z]{3,5}(?:-[A-Z]{3,5})?)"
        )
        direction_pattern = re.compile(r"(?i)(BUY|CALL|SELL)|(🟩|🟥|🔼|🔽|🟢)")
        entry_time_pattern = re.compile(
            r"(?:Entry at|ENTRY at|Entry time|TIME \(UTC.*?\)):?\s+(\d{1,2}:\d{2}(?::\d{2})?)|"
            r"([0-9]+\:[0-9]+)\s*:\s*(?:CALL|SELL)"
        )
        martingale_pattern = re.compile(r"(?:Martingale levels?|PROTECTION|M-)\s*(\d+)")

        # --- Check for signal keywords ---
        is_signal_text = any(
            kw in text.upper() for kw in ["BUY", "CALL", "SELL", "OTC", "EXPIRATION", "ENTRY", "TIME", "PROTECTION"]
        )
        is_signal_emoji = any(
            em in text for em in ["🟩", "🟥", "🔼", "🔽", "🟢"]
        )

        if not (is_signal_text or is_signal_emoji):
            logger.debug(f"Message {message.id} from group {source_group_id} does not contain signal keywords.")
            return None

        # --- Extract Currency Pair ---
        match = currency_pair_pattern.search(text)
        if match:
            signal["currency_pair"] = (match.group(1) or match.group(2)).replace(" ", "").replace("_", "/").strip().upper()
        
        # --- Extract Direction ---
        if "BUY" in text.upper() or "CALL" in text.upper() or "🟩" in text or "🔼" in text or "🟢" in text:
            signal["direction"] = "CALL"
        elif "SELL" in text.upper() or "🟥" in text or "🔽" in text:
            signal["direction"] = "SELL"
        
        # --- Extract Entry Time ---
        match = entry_time_pattern.search(text)
        if match:
            signal["entry_time"] = match.group(1) or match.group(2)

        # --- Extract Martingale Levels ---
        martingale_levels_found = False
        if source_group_id == source4_id:
            signal["martingale_levels"] = 2
            martingale_levels_found = True
        else:
            match = martingale_pattern.search(text)
            if match:
                signal["martingale_levels"] = int(match.group(1))
                martingale_levels_found = True
        
        if not martingale_levels_found:
            signal["martingale_levels"] = 0

        # --- Check for minimum signal info ---
        if not signal.get('currency_pair') or not signal.get('direction'):
            logger.info(f"Message {message.id} from group {source_group_id} is not a complete signal.")
            return None
        
        logger.info(f"Extracted signal from message {message.id}: {signal}")
        return signal

    except Exception as e:
        logger.error(f"Error extracting signal from message {message.id}: {e}", exc_info=True)
        return None

async def process_message_queue():
    """Consumer task to process messages from the queue."""
    logger.info("Message processing queue started.")
    while True:
        try:
            message, source_group_id = await message_queue.get()
            signal = await extract_signal(message, source_group_id)
            if signal:
                logger.info(f"Processed signal: {signal}")
            message_queue.task_done()
        except asyncio.CancelledError:
            logger.warning("Message processing queue task cancelled.")
            break
        except Exception as e:
            logger.error(f"Error processing message from queue: {e}", exc_info=True)
            message_queue.task_done()

async def periodic_channel_check(client, group_ids, interval=300):
    """
    Periodically fetches messages from specified channels to keep the update
    stream active. This is a workaround for Telegram's selective update behavior.
    """
    logger.info("Starting periodic channel check task.")
    while True:
        for group_id in group_ids:
            try:
                entity = await client.get_entity(PeerChannel(group_id * -1))
                # Fetching the last message forces an update from Telegram's servers.
                await client.get_messages(entity, limit=1)
                logger.debug(f"Manually fetched update for channel {group_id} ({entity.title}).")
            except Exception as e:
                logger.error(f"Failed to perform periodic check for channel {group_id}: {e}")
        await asyncio.sleep(interval) # Wait for the specified interval (e.g., 5 minutes)


@client.on(events.NewMessage(chats=source_group_ids))
async def handler(event):
    """Event handler for new messages in specified chats."""
    try:
        message = event.message
        await message_queue.put((message, event.chat_id))
    except Exception as e:
        logger.error(f"Error in event handler for message {event.message.id}: {e}", exc_info=True)


async def main():
    """Main function to start the bot and message processor."""
    try:
        await client.start(phone=PHONE_NUMBER)
        logger.info("Client started and connected successfully.")
        
        # Verify membership for all channels
        for group_id in source_group_ids:
            try:
                entity = await client.get_entity(group_id)
                logger.info(f"Verified membership for group: {group_id} ({entity.title})")
            except ValueError:
                logger.error(f"Client is not a member of group with ID: {group_id}. Cannot listen for messages.")
        
        # Start the message processing and periodic checking tasks
        processor_task = asyncio.create_task(process_message_queue())
        periodic_task = asyncio.create_task(periodic_channel_check(client, source_group_ids))

        await client.run_until_disconnected()
        logger.info("Client disconnected. Shutting down...")

    except Exception as e:
        logger.error(f"Failed to start Telethon client: {e}", exc_info=True)
        return

    finally:
        try:
            # Cleanup tasks gracefully
            await asyncio.wait_for(message_queue.join(), timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning("Queue did not empty in time. Some messages may not be processed.")
        
        processor_task.cancel()
        periodic_task.cancel()
        await asyncio.gather(processor_task, periodic_task, return_exceptions=True)
        await client.disconnect()
        logger.info("Application shutdown complete.")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Program interrupted by user. Exiting...")
    except Exception as e:
        logger.error(f"An unhandled error occurred: {e}", exc_info=True)
        sys.exit(1)

            
