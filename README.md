
# Telegram Message Forwarding Bot

This is a Telegram bot that forwards messages from specified source channels to target groups. The bot is implemented using the Telethon library and includes functionality for scheduling message forwarding at random intervals.

- Python 3.8+
- A Telegram account
- A Telegram API ID and API Hash (you can get these from [my.telegram.org](https://my.telegram.org))

## Installation

1- Clone the repository or download the script files.
```bash
git clone https://github.com/yagizharman/telegram-message-sender.git
```
2- Install the required packages.

```
pip install -r requirements.txt
```

3- Create a `.env` file in the project directory and add your configuration details. The file should look like this:

```
API_ID=your_api_id
API_HASH=your_api_hash
PHONE_NUMBER=your_phone_number
TARGET_GROUP_IDS=target_group_id1,target_group_id2,target_group_id3
CHANNEL_USERNAMES=channel_username1,channel_username2
MIN_TIME=30
MAX_TIME=60

```
- API_ID: Your Telegram API ID.
- API_HASH: Your Telegram API Hash.
- PHONE_NUMBER: Your phone number associated with your Telegram account.
- TARGET_GROUP_IDS: A comma-separated list of target group IDs to which messages will be forwarded.
- CHANNEL_USERNAMES: A comma-separated list of channel usernames from which messages will be monitored and forwarded.
- MIN_TIME: Minimum random time interval (in seconds) between forwarding messages.
- MAX_TIME: Maximum random time interval (in seconds) between forwarding messages.

## Usage

1- Run the `main.py` script.
```bash
python main.py

```

2- The bot will start and listen for new messages in the specified channels. When a new message is detected, it will be forwarded to the target groups after a random time interval between the specified MIN_TIME and MAX_TIME.

3- The `get_group_ids` function fetches all group IDs and writes them to a group_ids.txt file. You can use this to find the IDs of your groups if you don't know them.



