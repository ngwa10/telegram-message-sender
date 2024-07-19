import random
from telethon import TelegramClient,events
import asyncio
import os
from dotenv import load_dotenv


load_dotenv()
# API_ID and API_HASH

API_ID = os.getenv("API_ID")
API_HASH=os.getenv("API_HASH")

PHONE_NUMBER=os.getenv("PHONE_NUMBER")

source_group_id = os.getenv("SOURCE_GROUP_ID")
target_group_ids_str=os.getenv("TARGET_GROUP_IDS")

try:
    target_group_ids=list(map(int, target_group_ids_str.split(',')))
except:
    target_group_ids=[]


channel_username=os.getenv("CHANNEL_USERNAME1")
channel_username2=os.getenv("CHANNEL_USERNAME2")

client = TelegramClient('userbot_session', API_ID, API_HASH)


@client.on(events.NewMessage(chats=channel_username))
async def handler(event):
    message = event.message
    print("New message detected in source group with ID {channel_username}")
    
    
    while True:
        for group_id in target_group_ids:
            try:
                await client.forward_messages(group_id, message)
                print(f"Message forwarded to group with ID {group_id}")
            except Exception as e:
                print(f"Error forwarding message to group with ID {group_id}: {e}")
        random_number = random.randint(60, 180)
        print("Time interval randomly chosen for {channel_username} is : ",random_number)
        # Wait for 30 seconds before forwarding the message again
        await asyncio.sleep(random_number)

@client.on(events.NewMessage(chats=channel_username2))
async def handler2(event):
    message = event.message
    print("New message detected in source group with ID {channel_username2}")
    random_num_for_first = random.randint(30, 180)
    print(f"The wait for the second message is : {random_num_for_first}")
    await asyncio.sleep(random_num_for_first)

    while True:
        for group_id in target_group_ids:
            try:
                await client.forward_messages(group_id, message)
                print(f"Handler 2 :Message forwarded to group with ID {group_id}")
            except Exception as e:
                print(f"Handler 2 :Error forwarding message to group with ID {group_id}: {e}")
        random_numbers = random.randint(30, 180)

        print("Time interval randomly chosen for {channel_username2} is : ",random_numbers)
        # Wait for 30 seconds before forwarding the message again
        await asyncio.sleep(random_numbers)


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


    # Run the event handler until the script is stopped
    await client.run_until_disconnected()
    
with client:
    client.loop.run_until_complete(main())



