import asyncio
import logging
import os
import re
import sys
from datetime import datetime, timedelta
from dotenv import find_dotenv, load_dotenv
from telethon import TelegramClient, events
from telethon.tl.types import Message, PeerChannel
from telethon.errors import ChannelPrivateError, RPCError

# --- Logging setup ---
logging.basicConfig(
    level=logging.INFO,
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
        ] if gid and gid.strip()
    ]
    if not source_group_ids:
        raise ValueError("No valid SOURCE_GROUP_ID found in .env")
except (ValueError, TypeError) as e:
    logger.error(f"Failed to load group IDs from .env: {e}")
    sys.exit(1)

source4_id = int(os.getenv("SOURCE_GROUP_ID4")) if os.getenv("SOURCE_GROUP_ID4") else None

# --- Telegram Client & Queue ---
client = TelegramClient('userbot_session', API_ID, API_HASH)
message_queue = asyncio.Queue()

# --- Signal extraction ---
async def extract_signal(message: Message, source_group_id: int):
    try:
        signal = {"source_group_id": source_group_id}
        text = message.message
        if not text:
            logger.debug(f"Message {message.id} from group {source_group_id} is empty.")
            return None

        # --- Extract currency pair ---
        currency_pair_pattern = re.compile(
            r"(?:PAIR:?|CURRENCY PAIR:?|PAIR\s*:?|CURRENCY\s*PAIR\s*:?)\s*([A-Z]{3,5}[/ _-][A-Z]{3,5}(?:-[A-Z]{3})?)|"
            r"([A-Z]{3,5}[/ _-][A-Z]{3,5}(?:-[A-Z]{3,5})?)"
        )
        match = currency_pair_pattern.search(text)
        if match:
            signal["currency_pair"] = (match.group(1) or match.group(2)).replace("_","/").strip().upper()

        # --- Extract direction ---
        if any(x in text.upper() for x in ["BUY", "CALL", "🟩","🔼","🟢"]):
            signal["direction"] = "CALL"
        elif any(x in text.upper() for x in ["SELL","🟥","🔽"]):
            signal["direction"] = "SELL"

        # --- Extract entry time ---
        entry_time_pattern = re.compile(
            r"(?:Entry at|ENTRY at|Entry time|TIME \(UTC.*?\)):?\s+(\d{1,2}:\d{2}(?::\d{2})?)|([0-9]+\:[0-9]+)\s*:\s*(?:CALL|SELL)"
        )
        match = entry_time_pattern.search(text)
        if match:
            signal["entry_time"] = match.group(1) or match.group(2)
            try:
                signal["entry_time_dt"] = datetime.strptime(signal["entry_time"], "%H:%M")
            except ValueError:
                pass

        # --- Extract martingale levels ---
        martingale_levels_found = False
        if source_group_id == source4_id:
            signal["martingale_levels"] = 2
            martingale_levels_found = True
        else:
            lines = text.splitlines()
            levels = []
            for line in lines:
                match = re.search(r'(\d+)[^\d]*level|PROTECTION', line, re.IGNORECASE)
                if match:
                    levels.append(int(match.group(1)))
                emoji_match = re.findall(r'([1-9])️⃣', line)
                if emoji_match:
                    levels.extend([int(x) for x in emoji_match])
            if levels:
                signal["martingale_levels"] = max(levels)
                martingale_levels_found = True

        if not martingale_levels_found:
            signal["martingale_levels"] = 0

        # --- Calculate martingale entry times ---
        if "entry_time_dt" in signal and signal["martingale_levels"] > 0:
            expiration_minutes = 5  # default expiration
            martingale_times = []
            base_time = signal["entry_time_dt"] + timedelta(minutes=expiration_minutes)
            for i in range(signal["martingale_levels"]):
                martingale_times.append((base_time + timedelta(minutes=expiration_minutes*i)).strftime("%H:%M"))
            signal["martingale_times"] = martingale_times

        # --- Fun message if no signal ---
        if not signal.get("currency_pair") or not signal.get("direction"):
            words = text.strip().split()
            signal["fun_message"] = " ".join(words[-5:]) if words else "[empty message]"
            logger.info(f"Message {message.id} from group {source_group_id} has no signal, fun message: {signal['fun_message']}")
            return None

        logger.info(f"Extracted signal from message {message.id}: {signal}")
        return signal

    except Exception as e:
        logger.error(f"Error extracting signal from message {message.id}: {e}", exc_info=True)
        return None

# --- Process messages from queue ---
async def process_message_queue():
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

# --- Periodic channel check ---
async def periodic_channel_check(client, group_ids, interval=300):
    logger.info("Starting periodic channel check task.")
    while True:
        for group_id in group_ids:
            try:
                entity = await client.get_entity(group_id)
                await client.get_messages(entity, limit=1)
            except Exception as e:
                logger.error(f"Failed to perform periodic check for channel {group_id}: {e}")
        await asyncio.sleep(interval)

# --- Event handler ---
@client.on(events.NewMessage(chats=source_group_ids))
async def handler(event):
    try:
        await message_queue.put((event.message, event.chat_id))
    except Exception as e:
        logger.error(f"Error in event handler for message {event.message.id}: {e}", exc_info=True)

# --- Main ---
async def main():
    try:
        await client.start(phone=PHONE_NUMBER)
        logger.info("Client started and connected successfully.")

        # Verify membership for all channels
        for group_id in source_group_ids:
            try:
                entity = await client.get_entity(group_id)
                logger.info(f"Verified membership for group: {group_id} via fresh ({getattr(entity,'title',group_id)})")
            except Exception as e:
                logger.error(f"Could not verify membership for group {group_id}: {e}")

        # Start tasks
        processor_task = asyncio.create_task(process_message_queue())
        periodic_task = asyncio.create_task(periodic_channel_check(client, source_group_ids))

        await client.run_until_disconnected()

    except Exception as e:
        logger.error(f"Failed to start Telethon client: {e}", exc_info=True)

    finally:
        try:
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
