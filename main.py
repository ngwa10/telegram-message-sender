import asyncio
import logging
import os
import re
import sys
from datetime import datetime, timedelta
from dotenv import find_dotenv, load_dotenv
from telethon import TelegramClient, events
from telethon.tl.types import Message

# --- Logging setup ---
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# --- Load environment ---
if not find_dotenv():
    logger.error("No .env file found.")
    sys.exit(1)
load_dotenv(find_dotenv())

try:
    API_ID = int(os.getenv("API_ID"))
    API_HASH = os.getenv("API_HASH")
    PHONE_NUMBER = os.getenv("PHONE_NUMBER")
    RECEIVING_CHANNEL_ID = int(os.getenv("RECEIVING_CHANNEL_ID"))
except Exception as e:
    logger.error(f"Failed to load environment variables: {e}")
    sys.exit(1)

# Source groups
try:
    source_group_ids = [
        int(os.getenv("SOURCE_GROUP_ID1")),
        int(os.getenv("SOURCE_GROUP_ID2")),
        int(os.getenv("SOURCE_GROUP_ID3")),
        int(os.getenv("SOURCE_GROUP_ID4")),
        int(os.getenv("SOURCE_GROUP_ID5")),
        int(os.getenv("SOURCE_GROUP_ID6"))
    ]
    source_group_ids = [gid for gid in source_group_ids if gid]
except Exception as e:
    logger.error(f"Error loading source group IDs: {e}")
    sys.exit(1)

anna_trader_id = int(os.getenv("SOURCE_GROUP_ID2"))

# --- Telegram client ---
client = TelegramClient('userbot_session', API_ID, API_HASH)
message_queue = asyncio.Queue()
channel_names = {}  # store ID → name mapping

# --- Signal detection ---
async def extract_signal(message: Message, source_group_id: int):
    text = message.message
    if not text:
        logger.debug(f"Message {message.id} from {channel_names.get(source_group_id, source_group_id)} has no text.")
        return None

    signal = {"source_group_id": source_group_id}

    # --- Detect currency pair ---
    pair_pattern = re.compile(
        r"(?:PAIR|CURRENCY PAIR|Asset|📊)?\s*[:\-]?\s*([A-Z]{3,5}[/\-_][A-Z]{3,5}(?:-[A-Z]{3,5})?|CRYPTO IDX)",
        re.IGNORECASE
    )
    match = pair_pattern.search(text)
    if match:
        signal["currency_pair"] = match.group(1).replace("_", "/").upper()

    # --- Detect direction ---
    if any(x in text.upper() for x in ["BUY", "CALL", "🟩", "🟢", "🔼"]):
        signal["direction"] = "CALL"
    elif any(x in text.upper() for x in ["SELL", "PUT", "🟥", "🔽"]):
        signal["direction"] = "SELL"

    # --- Optional entry time ---
    time_pattern = re.compile(r"(\d{1,2}:\d{2}(?::\d{2})?)")
    match = time_pattern.search(text)
    if match:
        signal["entry_time"] = match.group(1)

    # --- Martingale detection ---
    martingale_levels = []
    for line in text.splitlines():
        match = re.findall(r'([1-9])️⃣', line)
        if match:
            martingale_levels.extend([int(x) for x in match])
        if re.search(r'PROTECTION|level', line, re.IGNORECASE):
            nums = re.findall(r'(\d+)', line)
            martingale_levels.extend([int(x) for x in nums])

    if martingale_levels:
        signal["martingale_levels"] = max(martingale_levels)
    elif source_group_id == anna_trader_id:
        signal["martingale_levels"] = 2
    else:
        signal["martingale_levels"] = 0

    # --- Calculate martingale entry times (if entry time present) ---
    if "entry_time" in signal and signal["martingale_levels"] > 0:
        try:
            entry_dt = datetime.strptime(signal["entry_time"], "%H:%M")
            expiration = 5
            martingale_times = [
                (entry_dt + timedelta(minutes=expiration * (i + 1))).strftime("%H:%M")
                for i in range(signal["martingale_levels"])
            ]
            signal["martingale_times"] = martingale_times
        except ValueError:
            pass

    # --- Validation: must have pair + direction ---
    if not signal.get("currency_pair") or not signal.get("direction"):
        logger.warning(f"❌ No valid signal found in message {message.id} from {channel_names.get(source_group_id, source_group_id)}")
        return None

    # --- Log details ---
    logger.info(
        f"✅ Signal detected from {channel_names.get(source_group_id, source_group_id)}: "
        f"Pair={signal.get('currency_pair')}, "
        f"Direction={signal.get('direction')}, "
        f"Entry={signal.get('entry_time', 'N/A')}, "
        f"Martingale Levels={signal.get('martingale_levels')}, "
        f"Martingale Times={signal.get('martingale_times', 'N/A')}"
    )

    return signal

# --- Process message queue ---
async def process_message_queue():
    while True:
        message, source_group_id = await message_queue.get()
        try:
            signal = await extract_signal(message, source_group_id)
            if signal:
                await client.forward_messages(RECEIVING_CHANNEL_ID, message)
                logger.info(f"📤 Forwarded message {message.id} to receiving channel")
        except Exception as e:
            logger.error(f"Error processing message from queue: {e}", exc_info=True)
        finally:
            message_queue.task_done()

# --- Event handler ---
@client.on(events.NewMessage(chats=source_group_ids))
async def handler(event):
    logger.info(f"📩 New message from {channel_names.get(event.chat_id, event.chat_id)}: {event.message.text[:50]}...")
    await message_queue.put((event.message, event.chat_id))

# --- Main ---
async def main():
    await client.start(phone=PHONE_NUMBER)
    logger.info("Bot started ✅")

    # preload channel names
    for gid in source_group_ids:
        try:
            entity = await client.get_entity(gid)
            channel_names[gid] = getattr(entity, "title", str(gid))
        except Exception:
            channel_names[gid] = str(gid)

    processor_task = asyncio.create_task(process_message_queue())

    try:
        await client.run_until_disconnected()
    finally:
        processor_task.cancel()
        await asyncio.gather(processor_task, return_exceptions=True)
        logger.info("Bot stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Program stopped manually.")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
        
