import asyncio
import logging
import os
import re
import sys
from dotenv import find_dotenv, load_dotenv
from telethon import TelegramClient, events
from telethon.tl.types import Message

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Load environment variables from .env file
if not find_dotenv():
    logger.error("No .env file found. Please create one.")
    sys.exit(1)
load_dotenv(find_dotenv())

# API_ID, API_HASH, and PHONE_NUMBER
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

# Source group IDs
try:
    source_group_ids = [
        int(os.getenv("SOURCE_GROUP_ID1")),
        int(os.getenv("SOURCE_GROUP_ID2")),
        int(os.getenv("SOURCE_GROUP_ID3")),
        int(os.getenv("SOURCE_GROUP_ID4"))
    ]
    # Remove any None or empty values if some group IDs are not set
    source_group_ids = [gid for gid in source_group_ids if gid is not None]
    if not source_group_ids:
        raise ValueError("No valid SOURCE_GROUP_ID found in .env")
except (ValueError, TypeError) as e:
    logger.error(f"Failed to load group IDs from .env: {e}")
    sys.exit(1)

# Create the Telegram client instance
client = TelegramClient('userbot_session', API_ID, API_HASH)

# Message queue
message_queue = asyncio.Queue()


async def extract_signal(message: Message, source_group_id: int):
    """Extracts a trading signal from a message."""
    try:
        signal = {}
        text = message.message

        # Compile regular expressions for efficiency
        # Non-capturing group (?:...) for alternatives
        currency_pair_pattern = re.compile(r"([A-Z]{3,5}[/][A-Z]{3,5}-(?:[A-Z]{3,5}))|([A-Z]{6,10}-(?:[A-Z]{3,5}))")
        direction_pattern = re.compile(r"(?i)(BUY|CALL|SELL)|(🟩|🟥|🔼)")
        entry_time_pattern = re.compile(r"(\d{2}:\d{2}:\d{2})")
        entry_price_pattern = re.compile(r"Entry: (\d{1,5}\.\d{1,5})")
        martingale_pattern = re.compile(r"Martingale: (\d)")

        # Check for signal indicators before processing
        is_call_signal = "CALL" in text.upper() or "🟩" in text or "🔼" in text
        is_sell_signal = "SELL" in text.upper() or "🟥" in text

        if not (is_call_signal or is_sell_signal):
            logger.debug(f"Message {message.id} is not a valid signal.")
            return None

        # Extract currency pair
        match = currency_pair_pattern.search(text)
        if match:
            # Handle both formats from the regex pattern
            signal["currency_pair"] = match.group(1) or match.group(2)
            logger.info(f"Extracted currency pair: {signal['currency_pair']}")

        # Extract direction (Buy, Sell, or Call)
        match = direction_pattern.search(text)
        if match:
            if "BUY" in match.group(0).upper() or "CALL" in match.group(0).upper() or "🟩" in match.group(0) or "🔼" in match.group(0):
                signal["direction"] = "CALL"
            elif "SELL" in match.group(0).upper() or "🟥" in match.group(0):
                signal["direction"] = "SELL"
            logger.info(f"Extracted direction: {signal.get('direction', 'N/A')}")

        # Extract entry time
        match = entry_time_pattern.search(text)
        if match:
            signal["entry_time"] = match.group(1)
            logger.info(f"Extracted entry time: {signal['entry_time']}")

        # Extract entry price
        match = entry_price_pattern.search(text)
        if match:
            signal["entry_price"] = match.group(1)
            logger.info(f"Extracted entry price: {signal['entry_price']}")

        # Extract martingale levels
        match = martingale_pattern.search(text)
        if match:
            signal["martingale_levels"] = int(match.group(1))
            logger.info(f"Extracted martingale levels: {signal['martingale_levels']}")
        elif source_group_id == int(os.getenv("SOURCE_GROUP_ID4")):
            # Only assign default if it's from the specific source
            signal["martingale_levels"] = 2
            logger.info(f"Assigned default martingale levels: {signal['martingale_levels']}")

        if not signal:
            logger.info(f"No valid signal components found in message: {message.id}")
            return None

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
            logger.info(f"Dequeued message {message.id} from group {source_group_id}")
            signal = await extract_signal(message, source_group_id)
            if signal:
                logger.info(f"Extracted and processed signal: {signal}")
                # You can add your signal handling logic here, e.g.,
                # sending it to another system, storing in a database, etc.
                # Example:
                # await client.send_message('me', f"New signal received: {signal}")

            await asyncio.sleep(1)  # Add a small delay to prevent rate-limiting
            message_queue.task_done()
        except asyncio.CancelledError:
            logger.warning("Message processing queue task cancelled.")
            break
        except Exception as e:
            logger.error(f"Error processing message from queue: {e}", exc_info=True)
            message_queue.task_done()


@client.on(events.NewMessage(chats=source_group_ids))
async def handler(event):
    """Event handler for new messages in specified chats."""
    try:
        message = event.message
        logger.info(f"New message detected in source group with ID {event.chat_id}: {message.id}")
        await message_queue.put((message, event.chat_id))
    except Exception as e:
        logger.error(f"Error in event handler for message {event.message.id}: {e}", exc_info=True)


async def main():
    """Main function to start the bot and message processor."""
    # Ensure client is authenticated and connected
    try:
        await client.start(phone=PHONE_NUMBER)
        logger.info("Client started and connected successfully.")
    except Exception as e:
        logger.error(f"Failed to start Telethon client: {e}", exc_info=True)
        return

    # Start the message processing queue as a background task
    processor_task = asyncio.create_task(process_message_queue())

    # Wait until the client is disconnected
    await client.run_until_disconnected()
    logger.info("Client disconnected. Shutting down...")

    # Wait for the queue to be empty and cancel the processor task
    try:
        await asyncio.wait_for(message_queue.join(), timeout=30.0)
    except asyncio.TimeoutError:
        logger.warning("Queue did not empty in time. Some messages may not be processed.")

    processor_task.cancel()
    await asyncio.gather(processor_task, return_exceptions=True)

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
    
