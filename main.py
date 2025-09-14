import asyncio
import logging
import os
import re
import sys
from dotenv import find_dotenv, load_dotenv
from telethon import TelegramClient, events
from telethon.errors import ChannelPrivateError, RpcError
from telethon.tl.types import Message

# --- Logging setup ---
logging.basicConfig(
    level=logging.INFO,  # Change to DEBUG for more detailed output
    format='[%(asctime)s] %(levelname)s - %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# --- Load environment variables ---
env_path = find_dotenv()
if not env_path:
    logger.error("No .env file found.")
    sys.exit(1)
load_dotenv(env_path)

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

async def resolve_channel(client, group_ref):
    """
    Resolve a channel reference to a usable entity/input entity.
    group_ref can be:
     - int (channel id) possibly negative like -100123...
     - string username '@channel' or 'channel'
     - invite link
    Returns (entity, resolved_by) where entity is a Telethon entity or input entity,
    and resolved_by is one of: 'get_entity', 'get_input_entity'
    Raises the original exception if resolution fails.
    """
    # Normalize numeric forms
    try:
        if isinstance(group_ref, str) and group_ref.startswith('-100'):
            # Telethon often expects the positive ID when resolving via API
            try:
                group_ref = int(group_ref)
            except Exception:
                pass

        # Prefer get_entity first (gives full Channel/User object including title)
        try:
            ent = await client.get_entity(group_ref)
            return ent, 'get_entity'
        except (ValueError, TypeError, RpcError) as e:
            # Fall back to get_input_entity which can fetch an input peer
            logger.debug("get_entity failed for %r: %s. Falling back to get_input_entity", group_ref, e)

        ent_in = await client.get_input_entity(group_ref)
        return ent_in, 'get_input_entity'

    except ChannelPrivateError as e:
        logger.error("ChannelPrivateError resolving %r: %s", group_ref, e)
        raise
    except Exception as e:
        logger.exception("Failed to resolve channel %r", group_ref)
        raise

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
            pair = (match.group(1) or match.group(2))
            if pair:
                signal["currency_pair"] = pair.replace(" ", "").replace("_", "/").strip().upper()

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
                try:
                    signal["martingale_levels"] = int(match.group(1))
                    martingale_levels_found = True
                except ValueError:
                    pass

        if not martingale_levels_found:
            signal["martingale_levels"] = 0

        # --- Check for minimum signal info ---
        if not signal.get('currency_pair') or not signal.get('direction'):
            logger.info(f"Message {message.id} from group {source_group_id} is not a complete signal.")
            return None

        logger.info(f"Extracted signal from message {message.id}: {signal}")
        return signal

    except Exception as e:
        logger.error(f"Error extracting signal from message {getattr(message, 'id', '[no-id]')}: {e}", exc_info=True)
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
            try:
                message_queue.task_done()
            except Exception:
                pass

async def periodic_channel_check(client, group_ids, interval=300):
    """
    Periodically fetches messages from specified channels to keep the update
    stream active. Resolves each channel every run (robust against stale caches).
    """
    logger.info("Starting periodic channel check task.")
    while True:
        for group_id in group_ids:
            try:
                ent, mode = await resolve_channel(client, group_id)
                # Fetch the last message to keep updates active. get_messages accepts both entity and input entity.
                msgs = await client.get_messages(ent, limit=1)
                if msgs:
                    logger.debug(f"Manually fetched update for channel {group_id}; latest msg id={msgs[0].id}")
                else:
                    logger.debug(f"No messages found for channel {group_id} on periodic check.")
            except ChannelPrivateError:
                logger.error(f"Channel {group_id} is private or your account lost access.")
            except Exception as e:
                logger.error(f"Failed to perform periodic check for channel {group_id}: {e}", exc_info=True)
        await asyncio.sleep(interval)  # Wait for the specified interval (e.g., 5 minutes)

@client.on(events.NewMessage(chats=source_group_ids))
async def handler(event):
    """Event handler for new messages in specified chats."""
    try:
        message = event.message
        await message_queue.put((message, event.chat_id))
    except Exception as e:
        logger.error(f"Error in event handler for message {getattr(event.message, 'id', '[no-id]')}: {e}", exc_info=True)

async def main():
    """Main function to start the bot and message processor."""
    try:
        await client.start(phone=PHONE_NUMBER)
        logger.info("Client started and connected successfully.")

        # Verify membership for all channels (resolve and log friendly title when possible)
        for group_id in source_group_ids:
            try:
                entity = await client.get_entity(group_id)
                friendly = getattr(entity, 'title', getattr(entity, 'username', str(entity)))
                logger.info(f"Verified membership for group: {group_id} ({friendly})")
            except ValueError:
                logger.error(f"Client is not a member of group with ID: {group_id}. Cannot listen for messages.")
            except Exception as e:
                logger.error(f"Could not resolve group {group_id}: {e}")

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
        
