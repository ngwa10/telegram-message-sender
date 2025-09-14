import asyncio
import logging
import os
import re
import sys
import json
from dotenv import find_dotenv, load_dotenv
from telethon import TelegramClient, events
from telethon.errors import ChannelPrivateError, RPCError
from telethon.tl.types import Message, PeerChannel

# --- Logging setup ---
logging.basicConfig(
    level=logging.INFO,  # Change to DEBUG for more detail
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

# --- Entity Cache (id + access_hash persistence) ---
ENTITY_CACHE_FILE = "entities.json"
entity_cache = {}
if os.path.exists(ENTITY_CACHE_FILE):
    try:
        with open(ENTITY_CACHE_FILE, "r") as f:
            entity_cache = json.load(f)
    except Exception as e:
        logger.warning(f"Could not load entity cache: {e}")

def save_entity_to_cache(group_id: int, entity):
    try:
        entity_cache[str(group_id)] = {
            "id": entity.id,
            "access_hash": getattr(entity, "access_hash", None),
            "title": getattr(entity, "title", None),
            "username": getattr(entity, "username", None),
        }
        with open(ENTITY_CACHE_FILE, "w") as f:
            json.dump(entity_cache, f, indent=2)
        logger.debug(f"Entity for group {group_id} cached.")
    except Exception as e:
        logger.error(f"Failed to save entity cache: {e}")

async def resolve_channel(client, group_id: int):
    """
    Resolve channel entity using cache, InputPeerChannel fallback, or get_entity.
    """
    try:
        if str(group_id) in entity_cache:
            cached = entity_cache[str(group_id)]
            if cached.get("access_hash"):
                try:
                    ent = await client.get_input_entity(PeerChannel(cached["id"]))
                    logger.debug("Resolved group %s from cache.", group_id)
                    return ent, "cache"
                except Exception as e:
                    logger.warning("Cache entity for %s failed: %s. Retrying full resolve...", group_id, e)

        # Fallback: resolve directly
        ent = await client.get_entity(PeerChannel(abs(group_id)))
        save_entity_to_cache(group_id, ent)
        return ent, "fresh"

    except ChannelPrivateError:
        logger.error(f"Group {group_id} is private or inaccessible.")
    except RPCError as e:
        logger.error(f"RPCError resolving group {group_id}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error resolving group {group_id}: {e}", exc_info=True)

    return None, "failed"

# --- Telegram Client and Queue ---
client = TelegramClient('userbot_session', API_ID, API_HASH)
message_queue = asyncio.Queue()

# Identify source4 for martingale defaults
source4_id = int(os.getenv("SOURCE_GROUP_ID4")) if os.getenv("SOURCE_GROUP_ID4") else None

async def extract_signal(message: Message, source_group_id: int):
    """Extract trading signals from messages."""
    try:
        signal = {"source_group_id": source_group_id}
        text = message.message
        if not text:
            return None

        # Patterns
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

        # Signal markers
        is_signal_text = any(kw in text.upper() for kw in ["BUY", "CALL", "SELL", "OTC", "EXPIRATION", "ENTRY", "TIME", "PROTECTION"])
        is_signal_emoji = any(em in text for em in ["🟩", "🟥", "🔼", "🔽", "🟢"])
        if not (is_signal_text or is_signal_emoji):
            return None

        # Currency Pair
        match = currency_pair_pattern.search(text)
        if match:
            signal["currency_pair"] = (match.group(1) or match.group(2)).replace(" ", "").replace("_", "/").strip().upper()

        # Direction
        if "BUY" in text.upper() or "CALL" in text.upper() or "🟩" in text or "🔼" in text or "🟢" in text:
            signal["direction"] = "CALL"
        elif "SELL" in text.upper() or "🟥" in text or "🔽" in text:
            signal["direction"] = "SELL"

        # Entry Time
        match = entry_time_pattern.search(text)
        if match:
            signal["entry_time"] = match.group(1) or match.group(2)

        # Martingale
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

        if not signal.get('currency_pair') or not signal.get('direction'):
            return None

        logger.info(f"Extracted signal from message {message.id}: {signal}")
        return signal

    except Exception as e:
        logger.error(f"Error extracting signal from message {message.id}: {e}", exc_info=True)
        return None

async def process_message_queue():
    """Process queued messages."""
    logger.info("Message processing queue started.")
    while True:
        try:
            message, source_group_id = await message_queue.get()
            signal = await extract_signal(message, source_group_id)
            if signal:
                logger.info(f"Processed signal: {signal}")
            message_queue.task_done()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            message_queue.task_done()

async def periodic_channel_check(client, group_ids, interval=300):
    """Keep channels active by periodic fetch."""
    logger.info("Starting periodic channel check task.")
    while True:
        for group_id in group_ids:
            try:
                entity, mode = await resolve_channel(client, group_id)
                if entity:
                    await client.get_messages(entity, limit=1)
                    logger.debug(f"Periodic check for channel {group_id} via {mode}.")
            except Exception as e:
                logger.error(f"Periodic check failed for {group_id}: {e}")
        await asyncio.sleep(interval)

@client.on(events.NewMessage(chats=source_group_ids))
async def handler(event):
    """Handle new incoming messages."""
    try:
        message = event.message
        await message_queue.put((message, event.chat_id))
        logger.debug("Queued message %s from chat %s", message.id, event.chat_id)
    except Exception as e:
        logger.error(f"Error in event handler: {e}", exc_info=True)

async def main():
    try:
        await client.start(phone=PHONE_NUMBER)
        logger.info("Client started and connected successfully.")

        # Verify membership
        for group_id in source_group_ids:
            entity, mode = await resolve_channel(client, group_id)
            if entity:
                logger.info(f"Verified membership for group: {group_id} via {mode} ({getattr(entity, 'title', '')})")
            else:
                logger.error(f"Could not verify membership for group {group_id}")

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
            logger.warning("Queue not empty at shutdown.")
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
        logger.error(f"Unhandled error: {e}", exc_info=True)
        sys.exit(1)
          
