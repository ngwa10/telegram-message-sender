#!/usr/bin/env python3
"""
Robust Telegram message listener for multiple source channels.

Features:
- Resolves channels at startup and registers a NewMessage handler using resolved entities
  (avoids the "only first source works" issue).
- Caches channel id -> access_hash in channels_cache.json for faster startup.
- Exponential backoff + retries for transient RPC/network errors.
- Graceful shutdown and queue draining.
- Configurable via .env: API_ID, API_HASH, PHONE_NUMBER, SOURCE_GROUP_ID1..4, LOG_LEVEL, PERIODIC_INTERVAL.
"""

import asyncio
import json
import logging
import os
import random
import re
import sys
from pathlib import Path
from dotenv import find_dotenv, load_dotenv
from telethon import TelegramClient, events
from telethon.errors import ChannelPrivateError, RPCError
from telethon.tl.types import Message, InputPeerChannel

# ---------------------------
# Config and constants
# ---------------------------
CACHE_PATH = Path("channels_cache.json")
BACKOFF_BASE = 1.0    # seconds
BACKOFF_FACTOR = 2.0
BACKOFF_MAX = 30.0    # seconds
MAX_RETRIES = 4       # number of retries for transient errors
DEFAULT_PERIODIC_INTERVAL = 300  # seconds
DEFAULT_LOG_LEVEL = "INFO"

# ---------------------------
# Logging setup (configurable via env)
# ---------------------------
env_path = find_dotenv()
if env_path:
    load_dotenv(env_path)

LOG_LEVEL = os.getenv("LOG_LEVEL", DEFAULT_LOG_LEVEL).upper()
numeric_level = getattr(logging, LOG_LEVEL, logging.INFO)

logging.basicConfig(
    level=numeric_level,
    format='[%(asctime)s] %(levelname)s - %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ---------------------------
# Load required env vars
# ---------------------------
try:
    API_ID = int(os.getenv("API_ID") or "")
    API_HASH = os.getenv("API_HASH")
    PHONE_NUMBER = os.getenv("PHONE_NUMBER")
    if not all([API_ID, API_HASH, PHONE_NUMBER]):
        raise ValueError("API_ID, API_HASH, or PHONE_NUMBER not set in .env")
except Exception as e:
    logger.error("Missing or invalid API credentials: %s", e)
    sys.exit(1)

# Source groups: read up to 8 possible IDs to be flexible
source_group_ids = []
for i in range(1, 9):
    v = os.getenv(f"SOURCE_GROUP_ID{i}")
    if v and v.strip():
        try:
            source_group_ids.append(int(v.strip()))
        except ValueError:
            # keep non-int forms (usernames) as strings
            source_group_ids.append(v.strip())

if not source_group_ids:
    logger.error("No SOURCE_GROUP_IDs found in .env (SOURCE_GROUP_ID1..8). Exiting.")
    sys.exit(1)

# Optional config
PERIODIC_INTERVAL = int(os.getenv("PERIODIC_INTERVAL") or DEFAULT_PERIODIC_INTERVAL)
# Identify special source for default martingale levels if set
source4_id = int(os.getenv("SOURCE_GROUP_ID4")) if os.getenv("SOURCE_GROUP_ID4") else None

# ---------------------------
# Telethon client + queue
# ---------------------------
client = TelegramClient('userbot_session', API_ID, API_HASH)
message_queue = asyncio.Queue()

# ---------------------------
# Cache helpers
# ---------------------------
def load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {int(k): int(v) for k, v in data.items()}
    except Exception as e:
        logger.warning("Failed to load cache file: %s. Starting fresh cache.", e)
        return {}

def save_cache(cache: dict):
    try:
        serializable = {str(k): int(v) for k, v in cache.items()}
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2)
        logger.debug("Saved channel cache to %s", CACHE_PATH)
    except Exception as e:
        logger.error("Failed to save cache: %s", e)

_channel_cache = load_cache()  # {channel_id: access_hash}

# ---------------------------
# Channel resolution + retries
# ---------------------------
async def resolve_channel(client, group_ref):
    """
    Resolve `group_ref` to an entity or input entity.

    Returns (entity_or_inputpeer, 'cache'|'get_entity'|'get_input_entity').
    Raises ChannelPrivateError for private/no-access.
    """
    # Normalize '-100...' strings to int where possible
    try:
        if isinstance(group_ref, str) and group_ref.startswith('-100'):
            try:
                group_ref = int(group_ref)
            except Exception:
                pass

        # Try cache if group_ref is an integer channel id
        if isinstance(group_ref, int) or (isinstance(group_ref, str) and group_ref.lstrip('-').isdigit()):
            try:
                cid = int(group_ref)
                if cid in _channel_cache:
                    ahash = _channel_cache[cid]
                    logger.debug("Using cached access_hash for channel %s", cid)
                    return InputPeerChannel(channel_id=cid, access_hash=ahash), 'cache'
            except Exception as e:
                logger.debug("Cache attempt failed for %r: %s", group_ref, e)

        # Try get_entity (prefers server-resolved full object with access_hash)
        try:
            ent = await client.get_entity(group_ref)
            ent_id = getattr(ent, "id", None)
            ent_ah = getattr(ent, "access_hash", None)
            if ent_id and ent_ah:
                _channel_cache[int(ent_id)] = int(ent_ah)
                save_cache(_channel_cache)
            return ent, 'get_entity'
        except (ValueError, TypeError, RPCError) as e:
            logger.debug("get_entity failed for %r: %s. Falling back to get_input_entity", group_ref, e)

        ent_in = await client.get_input_entity(group_ref)
        return ent_in, 'get_input_entity'

    except ChannelPrivateError:
        # propagate permanent access issues up
        raise
    except Exception:
        # propagate others to caller (retry wrapper will handle)
        raise

async def resolve_channel_with_retries(client, group_ref, max_retries=MAX_RETRIES):
    """
    Wrapper around resolve_channel that retries on transient errors with exponential backoff + jitter.
    """
    attempt = 0
    while True:
        try:
            ent, mode = await resolve_channel(client, group_ref)
            return ent, mode
        except ChannelPrivateError:
            logger.error("ChannelPrivateError while resolving %r -> access revoked or channel private.", group_ref)
            raise
        except RPCError as e:
            attempt += 1
            if attempt > max_retries:
                logger.exception("Max retries reached resolving %r; last error: %s", group_ref, e)
                raise
            backoff = min(BACKOFF_BASE * (BACKOFF_FACTOR ** (attempt - 1)), BACKOFF_MAX)
            jitter = random.uniform(0, backoff * 0.25)
            sleep_for = backoff + jitter
            logger.warning("RPCError resolving %r (attempt %d/%d). Backing off %.2fs...", group_ref, attempt, max_retries, sleep_for)
            await asyncio.sleep(sleep_for)
        except Exception as e:
            attempt += 1
            if attempt > max_retries:
                logger.exception("Failed to resolve %r after %d attempts; last error: %s", group_ref, attempt - 1, e)
                raise
            backoff = min(BACKOFF_BASE * (BACKOFF_FACTOR ** (attempt - 1)), BACKOFF_MAX)
            jitter = random.uniform(0, backoff * 0.25)
            sleep_for = backoff + jitter
            logger.warning("Error resolving %r (attempt %d/%d). Backing off %.2fs...", group_ref, attempt, max_retries, sleep_for)
            await asyncio.sleep(sleep_for)

# ---------------------------
# Signal extraction function
# ---------------------------
async def extract_signal(message: Message, source_group_id):
    """
    Extract trading signal from message. Returns dict or None.
    Accepts source_group_id as the resolved chat id (int) for clarity.
    """
    try:
        signal = {"source_group_id": source_group_id}
        text = message.message
        if not text:
            logger.debug("Message %s from %s empty", getattr(message, "id", "[no-id]"), source_group_id)
            return None

        # Patterns
        currency_pair_pattern = re.compile(
            r"(?:PAIR:?|CURRENCY PAIR:?|PAIR\s*:?|CURRENCY\s*PAIR\s*:?)\s*([A-Z]{3,5}[/ _-][A-Z]{3,5}(?:-[A-Z]{3})?)|"
            r"([A-Z]{3,5}[/ _-][A-Z]{3,5}(?:-[A-Z]{3,5})?)"
        )
        entry_time_pattern = re.compile(
            r"(?:Entry at|ENTRY at|Entry time|TIME \(UTC.*?\)):?\s+(\d{1,2}:\d{2}(?::\d{2})?)|"
            r"([0-9]+\:[0-9]+)\s*:\s*(?:CALL|SELL)"
        )
        martingale_pattern = re.compile(r"(?:Martingale levels?|PROTECTION|M-)\s*(\d+)")

        is_signal_text = any(
            kw in text.upper() for kw in ["BUY", "CALL", "SELL", "OTC", "EXPIRATION", "ENTRY", "TIME", "PROTECTION"]
        )
        is_signal_emoji = any(em in text for em in ["🟩", "🟥", "🔼", "🔽", "🟢"])

        if not (is_signal_text or is_signal_emoji):
            logger.debug("Message %s from %s not a signal (no keywords).", getattr(message, "id", "[no-id]"), source_group_id)
            return None

        # currency
        match = currency_pair_pattern.search(text)
        if match:
            pair = (match.group(1) or match.group(2))
            if pair:
                signal["currency_pair"] = pair.replace(" ", "").replace("_", "/").strip().upper()

        # direction
        if "BUY" in text.upper() or "CALL" in text.upper() or "🟩" in text or "🔼" in text or "🟢" in text:
            signal["direction"] = "CALL"
        elif "SELL" in text.upper() or "🟥" in text or "🔽" in text:
            signal["direction"] = "SELL"

        # entry time
        match = entry_time_pattern.search(text)
        if match:
            signal["entry_time"] = match.group(1) or match.group(2)

        # martingale levels
        martingale_levels_found = False
        try:
            if source_group_id == source4_id:
                signal["martingale_levels"] = 2
                martingale_levels_found = True
        except Exception:
            pass

        if not martingale_levels_found:
            match = martingale_pattern.search(text)
            if match:
                try:
                    signal["martingale_levels"] = int(match.group(1))
                    martingale_levels_found = True
                except Exception:
                    pass

        if not martingale_levels_found:
            signal["martingale_levels"] = 0

        # minimal fields check
        if not signal.get("currency_pair") or not signal.get("direction"):
            logger.info("Message %s from %s incomplete signal.", getattr(message, "id", "[no-id]"), source_group_id)
            return None

        logger.info("Extracted signal from message %s: %s", getattr(message, "id", "[no-id]"), signal)
        return signal

    except Exception as e:
        logger.exception("Error extracting signal from message %s: %s", getattr(message, "id", "[no-id]"), e)
        return None

# ---------------------------
# Queue consumer
# ---------------------------
async def process_message_queue():
    logger.info("Message processing queue started.")
    while True:
        try:
            message, source_chat_id = await message_queue.get()
            signal = await extract_signal(message, source_chat_id)
            if signal:
                # Do something useful with the signal (placeholder)
                logger.info("Processed signal: %s", signal)
            message_queue.task_done()
        except asyncio.CancelledError:
            logger.warning("Message processing queue cancelled.")
            break
        except Exception as e:
            logger.exception("Error processing message from queue: %s", e)
            try:
                message_queue.task_done()
            except Exception:
                pass

# ---------------------------
# Event handler (defined but not registered as decorator)
# ---------------------------
async def handler(event):
    """
    Generic handler that extracts a stable source_chat_id and enqueues the message.
    Registered dynamically after entities are resolved.
    """
    try:
        message = event.message
        # Prefer event.chat_id (works for many peer types)
        source_chat_id = getattr(event, "chat_id", None)
        if source_chat_id is None:
            # fallback to message.peer_id which may be a PeerChannel/PeerChat/PeerUser
            pid = getattr(message, "peer_id", None)
            if pid is not None:
                # try common attributes
                source_chat_id = getattr(pid, "channel_id", None) or getattr(pid, "chat_id", None) or getattr(pid, "user_id", None)
        # Final fallback to event.sender_id
        if source_chat_id is None:
            source_chat_id = getattr(event, "sender_id", None)

        logger.debug("Received message id=%s from chat=%s", getattr(message, "id", "[no-id]"), source_chat_id)
        await message_queue.put((message, source_chat_id))
    except Exception as e:
        logger.exception("Error in event handler for message %s: %s", getattr(event.message, "id", "[no-id]"), e)

# ---------------------------
# Periodic check task (keeps updates active)
# ---------------------------
async def periodic_channel_check(client, group_ids, interval=PERIODIC_INTERVAL):
    logger.info("Starting periodic channel check task (interval=%s seconds)", interval)
    while True:
        for group_id in group_ids:
            try:
                ent, mode = await resolve_channel_with_retries(client, group_id)
                msgs = await client.get_messages(ent, limit=1)
                if msgs:
                    logger.debug("Periodic check OK for %s (mode=%s); latest id=%s", group_id, mode, msgs[0].id)
                else:
                    logger.debug("Periodic check: no messages for %s (mode=%s).", group_id, mode)
            except ChannelPrivateError:
                logger.error("Channel %s is private or your account lost access.", group_id)
                # Remove from cache to force re-resolve later if needed
                try:
                    cid = int(group_id)
                    if cid in _channel_cache:
                        logger.info("Removing %s from cache due to access issues.", cid)
                        _channel_cache.pop(cid, None)
                        save_cache(_channel_cache)
                except Exception:
                    pass
            except Exception as e:
                logger.exception("Failed periodic check for %s: %s", group_id, e)
        await asyncio.sleep(interval)

# ---------------------------
# Main startup & registration
# ---------------------------
async def main():
    try:
        await client.start(phone=PHONE_NUMBER)
        logger.info("Client started and connected successfully.")

        # Resolve channels and collect resolved peers for handler registration
        resolved_peers = []
        resolved_map = {}  # map resolved entity -> original config ref (for logging)
        for group_ref in source_group_ids:
            try:
                ent, mode = await resolve_channel_with_retries(client, group_ref)
                resolved_peers.append(ent)
                # For easier logging later — try to obtain stable id
                resolved_id = getattr(ent, "channel_id", None) or getattr(ent, "id", None) or getattr(ent, "user_id", None)
                resolved_map[resolved_id] = (group_ref, mode)
                friendly = getattr(ent, "title", getattr(ent, "username", str(ent)))
                logger.info("Resolved %r -> %s (mode=%s)", group_ref, friendly, mode)
            except ChannelPrivateError:
                logger.error("Cannot resolve %r: access revoked or channel private.", group_ref)
            except Exception as e:
                logger.error("Failed to resolve %r: %s", group_ref, e)

        if not resolved_peers:
            logger.error("No chats resolved for event handler. Exiting.")
            await client.disconnect()
            return

        # Register handler dynamically using the resolved peers/entities
        client.add_event_handler(handler, events.NewMessage(chats=resolved_peers))
        logger.info("Registered NewMessage handler for %d chats.", len(resolved_peers))
        if numeric_level <= logging.DEBUG:
            for ent in resolved_peers:
                logger.debug("Handler registered for entity: %r (id=%s, title=%s, username=%s)",
                             ent, getattr(ent, "id", None), geta
    
