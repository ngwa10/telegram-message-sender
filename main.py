import asyncio
import logging
import os
import re
import sys
from dotenv import find_dotenv, load_dotenv
from telethon import TelegramClient, events
from telethon.tl.types import Message

# --- Logging setup ---
logging.basicConfig(
    level=logging.INFO,  # Change to DEBUG for detailed output
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

# --- API Credentials ---
try:
    API_ID = int(os.getenv("API_ID"))
    API_HASH = os.getenv("API_HASH")
    PHONE_NUMBER = os.getenv("PHONE_NUMBER")
    if not all([API_ID, API_HASH, PHONE_NUMBER]):
        raise ValueError("API_ID, API_HASH, or PHONE_NUMBER not set in .env")
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

# --- Per-source signal counters ---
signal_counts = {gid: 0 for gid in source_group_ids}

# Source4 default martingale
source4_id = int(os.getenv("SOURCE_GROUP_ID4")) if os.getenv("SOURCE_GROUP_ID4") else None

# --- Already processed messages (to avoid duplicates) ---
processed_messages = set()

async def extract_signal(message: Message, source_group_id: int):
    """Extracts a trading signal from a message based on text/emoji."""
    try:
        if message.id in processed_messages:
            return None
        processed_messages.add(message.id)

        signal = {"source_group_id": source_group_id}
        text = message.message
        if not text:
            return None

        # --- Regex patterns ---
        currency_pair_pattern = re.compile(
            r"(?:PAIR:?|CURRENCY PAIR:?|PAIR\s*:?|CURRENCY\s*PAIR\s*:?)\s*([A-Z]{3,5}[/ _-][A-Z]{3,5}(?:-[A-Z]{3})?)|"
            r"([A-Z]{3,5}[/ _-][A-Z]{3,5}(?:-[A-Z]{3,5})?)"
        )
        direction_pattern = re.compile(r"(?i)(BUY|CALL|SELL)|(🟩|🟥|🔼|🔽|🟢)")
        entry_time_pattern = re.compile(
            r"(?:Entry at|ENTRY at|Entry time|TIME \(UTC.*?\)):?\s+(\d{1,2}:\d{2}(?::\d{2})?)|"
            r"([0-9]+\:[0-9]+)\s*:\s*(?:CALL|SELL)"
        )

        # --- Check if message contains signal keywords ---
        is_signal_text = any(
            kw in text.upper() for kw in ["BUY", "CALL", "SELL", "OTC", "EXPIRATION", "ENTRY", "TIME", "PROTECTION"]
        )
        is_signal_emoji = any(
            em in text for em in ["🟩", "🟥", "🔼", "🔽", "🟢"]
        )
        if not (is_signal_text or is_signal_emoji):
            return None

        # --- Extract currency pair ---
        match = currency_pair_pattern.search(text)
        if match:
            signal["currency_pair"] = (match.group(1) or match.group(2)).replace(" ", "").replace("_", "/").strip().upper()

        # --- Extract direction ---
        if "BUY" in text.upper() or "CALL" in text.upper() or "🟩" in text or "🔼" in text or "🟢" in text:
            signal["direction"] = "CALL"
        elif "SELL" in text.upper() or "🟥" in text or "🔽" in text:
            signal["direction"] = "SELL"

        # --- Extract entry time ---
        match = entry_time_pattern.search(text)
        if match:
            signal["entry_time"] = match.group(1) or match.group(2)

        # --- Extract martingale levels ---
        martingale_levels_found = False
        if source_group_id == source4_id:
            signal["martingale_levels"] = 2  # default for source4
            martingale_levels_found = True
        else:
            martingale_matches = re.findall(r"(?:Martingale levels?|PROTECTION|M-).*?(\d+)", text, re.IGNORECASE)
            if martingale_matches:
                signal["martingale_levels"] = max(int(x) for x in martingale_matches)
                martingale_levels_found = True
        if not martingale_levels_found:
            signal["martingale_levels"] = 0

        # --- Ensure minimum required info ---
        if not signal.get("currency_pair") or not signal.get("direction"):
            return None

        # --- Update per-source counter ---
        signal_counts[source_group_id] += 1

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

async def periodic_channel_check(interval=300):
    """Periodically fetch last message to keep updates active."""
    logger.info("Starting periodic channel check task.")
    while True:
        for group_id in source_group_ids:
            try:
                entity = await client.get_entity(group_id)
                await client.get_messages(entity, limit=1)
                logger.debug(f"Manually fetched update for channel {group_id}")
            except Exception as e:
                logger.error(f"Failed periodic check for channel {group_id}: {e}")
        await asyncio.sleep(interval)

@client.on(events.NewMessage(chats=source_group_ids))
async def handler(event):
    """Event handler for new messages in specified chats."""
    try:
        message = event.message
        await message_queue.put((message, event.chat_id))
    except Exception as e:
        logger.error(f"Error in event handler for message {event.message.id}: {e}", exc_info=True)

async def main():
    """Main function to start the bot."""
    try:
        await client.start(phone=PHONE_NUMBER)
        logger.info("Client started and connected successfully.")

        # Verify membership for all channels
        for group_id in source_group_ids:
            try:
                entity = await client.get_entity(group_id)
                logger.info(f"Verified membership for group: {group_id} via fresh ({getattr(entity, 'title', 'N/A')})")
            except Exception as e:
                logger.error(f"Could not verify membership for group {group_id}: {e}")

        # Start tasks
        processor_task = asyncio.create_task(process_message_queue())
        periodic_task = asyncio.create_task(periodic_channel_check())

        await client.run_until_disconnected()

    except Exception as e:
        logger.error(f"Failed to start Telethon client: {e}", exc_info=True)

    finally:
        # Graceful shutdown
        try:
            await asyncio.wait_for(message_queue.join(), timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning("Queue did not empty in time.")

        processor_task.cancel()
        periodic_task.cancel()
        await asyncio.gather(processor_task, periodic_task, return_exceptions=True)
        await client.disconnect()
        logger.info("Application shutdown complete.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Program interrupted by user. Exiting...")
    except Exception as e:
        logger.error(f"An unhandled error occurred: {e}", exc_info=True)
        sys.exit(1)
        
