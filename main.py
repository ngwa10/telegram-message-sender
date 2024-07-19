import random
from telethon import TelegramClient, events
import asyncio
import os
from dotenv import load_dotenv


load_dotenv()
# API_ID and API_HASH

API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
PHONE_NUMBER = os.getenv("PHONE_NUMBER")

source_group_id = os.getenv("SOURCE_GROUP_ID")
target_group_ids_str = os.getenv("TARGET_GROUP_IDS")

try:
    target_group_ids = list(map(int, target_group_ids_str.split(',')))
except:
    target_group_ids = []

channel_usernames_str = os.getenv("CHANNEL_USERNAMES")
channel_usernames = channel_usernames_str.split(',')

client = TelegramClient('userbot_session', API_ID, API_HASH)

message_queue = asyncio.Queue()

async def process_message():
    while True:
        channel_username, message = await message_queue.get()
        for group_id in target_group_ids:
            try:
                await client.forward_messages(group_id, message)
                print(f"Message forwarded to group with ID {group_id} from {channel_username}")
            except Exception as e:
                print(f"Error forwarding message to group with ID {group_id} from {channel_username}: {e}")
        
        random_number = random.randint(0, 10)
        print(f"Time interval randomly chosen for {channel_username} is: {random_number}")
        await asyncio.sleep(random_number)

        # Re-enqueue the message for the next round
        await message_queue.put((channel_username, message))
        message_queue.task_done()

async def add_message_to_queue(channel, message):
    await message_queue.put((channel, message))

for channel_username in channel_usernames:
    @client.on(events.NewMessage(chats=channel_username))
    async def handler(event, channel=channel_username):
        message = event.message
        print(f"New message detected in source group with ID {channel}")
        await add_message_to_queue(channel, message)

async def get_group_ids():
    await client.start(PHONE_NUMBER)
    
    # Get all dialogs (conversations)
    dialogs = await client.get_dialogs()

    # Open a file to write the group names and IDs
    with open('group_ids.txt', 'w', encoding="utf-8") as file:
        for dialog in dialogs:
            if dialog.is_group:
                file.write(f"Group Name: {dialog.name}, Group ID: {dialog.id}\n")

async def main():
    # Start the client and log in if necessary
    await client.start(PHONE_NUMBER)
    print("Client Created")
    await get_group_ids()

    # Start the message processing task
    asyncio.create_task(process_message())

    # Run the event handler until the script is stopped
    await client.run_until_disconnected()
    
with client:
    client.loop.run_until_complete(main())
