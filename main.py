import asyncio
import json
import os
import re
from getpass import getpass
import click
from dotenv import load_dotenv
from telethon.sync import TelegramClient, errors, functions
from telethon.tl import types
import socks
import pandas as pd
import time

# Load environment variables from .env file
load_dotenv()

API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')
PHONE_NUMBER = os.getenv('PHONE_NUMBER')

def read_proxy_settings(file_path):
    proxies = []
    with open(file_path, 'r') as f:
        for line in f:
            proxy_data = line.strip().split(',')
            if len(proxy_data) < 3:
                print(f"Skipping improperly formatted line: {line}")
                continue
            proxy = (
                socks.SOCKS5 if proxy_data[0].lower() == 'socks5' else socks.HTTP,
                proxy_data[1],
                int(proxy_data[2]),
                True,  # rdns
                proxy_data[3] if len(proxy_data) > 3 else None,
                proxy_data[4] if len(proxy_data) > 4 else None
            )
            proxies.append(proxy)
    return proxies

def read_last_checked_number(file_path):
    with open(file_path, 'r') as f:
        return f.readline().strip()

def write_last_checked_number(file_path, number):
    with open(file_path, 'w') as f:
        f.write(number)

def increment_phone_number(phone_number):
    return str(int(phone_number) + 1)

def get_human_readable_user_status(status: types.TypeUserStatus):
    if isinstance(status, types.UserStatusOnline):
        return "Currently online"
    elif isinstance(status, types.UserStatusOffline):
        return status.was_online.strftime("%Y-%m-%d %H:%M:%S %Z")
    elif isinstance(status, types.UserStatusRecently):
        return "Last seen recently"
    elif isinstance(status, types.UserStatusLastWeek):
        return "Last seen last week"
    elif isinstance(status, types.UserStatusLastMonth):
        return "Last seen last month"
    else:
        return "Unknown"

async def get_names(client: TelegramClient, phone_number: str) -> dict:
    result = {}
    print(f"Checking: {phone_number=} ...", end="", flush=True)
    try:
        # Create a contact
        contact = types.InputPhoneContact(
            client_id=0, phone=phone_number, first_name="", last_name=""
        )
        # Attempt to add the contact from the address book
        contacts = await client(functions.contacts.ImportContactsRequest([contact]))

        users = contacts.to_dict().get("users", [])
        number_of_matches = len(users)

        if number_of_matches == 0:
            result.update(
                {
                    "error": "No response, the phone number is not on Telegram or has blocked contact adding."
                }
            )
        elif number_of_matches == 1:
            # Attempt to remove the contact from the address book.
            # The response from DeleteContactsRequest contains more information than from ImportContactsRequest
            updates_response: types.Updates = await client(
                functions.contacts.DeleteContactsRequest(id=[users[0].get("id")])
            )
            user = updates_response.users[0]
            # getting more information about the user
            result.update(
                {
                    "id": user.id,
                    "username": user.username,
                    "usernames": user.usernames,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "fake": user.fake,
                    "verified": user.verified,
                    "premium": user.premium,
                    "mutual_contact": user.mutual_contact,
                    "bot": user.bot,
                    "bot_chat_history": user.bot_chat_history,
                    "restricted": user.restricted,
                    "restriction_reason": user.restriction_reason,
                    "user_was_online": get_human_readable_user_status(user.status),
                    "phone": user.phone,
                }
            )
        else:
            result.update(
                {
                    "error": """This phone number matched multiple Telegram accounts, 
            which is unexpected. Please contact the developer."""
                }
            )

    except TypeError as e:
        result.update(
            {
                "error": f"TypeError: {e}. --> The error might have occurred due to the inability to delete the {phone_number=} from the contact list."
            }
        )
    except errors.FloodWaitError as e:
        wait_time = e.seconds
        print(f"Rate limit hit. Waiting for {wait_time} seconds.")
        await asyncio.sleep(wait_time)
        return await get_names(client, phone_number)
    except Exception as e:
        result.update({"error": f"Unexpected error: {e}."})
        raise
    print("Done.")
    return result

async def validate_users(client: TelegramClient, phone_number: str, file_path: str, excel_file: str) -> dict:
    result = {}
    delay = 5  # Initial delay between requests
    while True:
        if phone_number not in result:
            result[phone_number] = await get_names(client, phone_number)
            if "error" in result[phone_number]:
                print(f"Error for {phone_number}: {result[phone_number]['error']}")
                if "not on Telegram" in result[phone_number]["error"]:
                    phone_number = increment_phone_number(phone_number)
                    write_last_checked_number(file_path, phone_number)
                else:
                    print(f"Unexpected error for {phone_number}: {result[phone_number]['error']}")
                    break
            else:
                save_to_excel(excel_file, result[phone_number])
                phone_number = increment_phone_number(phone_number)
                write_last_checked_number(file_path, phone_number)
            await asyncio.sleep(delay)
    return result

async def login(proxies=None) -> TelegramClient:
    """Create a telethon session or reuse existing one"""
    print("Logging in...", end="", flush=True)
    for proxy in proxies:
        client = TelegramClient(
            PHONE_NUMBER, 
            API_ID, 
            API_HASH,
            proxy=proxy
        )
        try:
            await client.connect()
            if not await client.is_user_authorized():
                await client.send_code_request(PHONE_NUMBER)
                try:
                    await client.sign_in(
                        PHONE_NUMBER, input("Enter the code (sent on telegram): ")
                    )
                except errors.SessionPasswordNeededError:
                    pw = getpass(
                        "Two-Step Verification enabled. Please enter your account password: "
                    )
                    await client.sign_in(password=pw)
            print("Done.")
            return client
        except Exception as e:
            print(f"Failed to connect with proxy {proxy}: {e}")
            await client.disconnect()
    raise Exception("All proxies failed")

def save_to_excel(file_path: str, data: dict) -> None:
    df = pd.DataFrame([data])
    if os.path.exists(file_path):
        df_existing = pd.read_excel(file_path)
        df = pd.concat([df_existing, df], ignore_index=True)
    df.to_excel(file_path, index=False)
    print(f"Results saved to {file_path}")

@click.command(
    epilog="Check out the docs at github.com/bellingcat/telegram-phone-number-checker for more information."
)
@click.option(
    "--phone-numbers-file",
    "-f",
    help="Filename containing the last checked phone number",
    type=str,
    default="last_checked_number.txt",
    show_default=True,
)
@click.option(
    "--output",
    help="Filename to store results",
    default="results.xlsx",
    show_default=True,
    type=str,
)
def main_entrypoint(
    phone_numbers_file: str, output: str
) -> None:
    """
    Check to see if one or more phone numbers belong to a valid Telegram account.

    \b
    Prerequisites:
    1. A Telegram account with an active phone number
    2. A Telegram App api_id and App api_hash, which you can get by creating
       a Telegram App @ https://my.telegram.org/apps

    \b
    Recommendations:
    Telegram recommends entering phone numbers in international format
    +(country code)(city or carrier code)(your number)
    i.e. +491234567891

    """
    proxies = read_proxy_settings('proxy.txt')
    phone_number = read_last_checked_number(phone_numbers_file)
    asyncio.run(
        run_program(
            phone_number,
            output,
            proxies,
            phone_numbers_file
        )
    )

async def run_program(
    phone_number: str, output: str, proxies: dict, file_path: str
):
    client = await login(proxies=proxies)
    await validate_users(client, phone_number, file_path, output)
    await client.disconnect()

if __name__ == "__main__":
    main_entrypoint()
