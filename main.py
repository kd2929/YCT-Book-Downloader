import os
import shutil
import asyncio
import requests
from pyrogram import Client, filters
from pyrogram.types import Message
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, UnidentifiedImageError
import json
import re

# Bot configurations
API_ID = "28919717"
API_HASH = "e4b11bcdf5ce2ca405cf2a8e84dfed24"
BOT_TOKEN = "6126914317:AAH0Yl8yvgDbsL8yO72ncUSxi5RKkfVSEhM"

app = Client("book_downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_tasks = {}
executor = ThreadPoolExecutor(max_workers=20)

CI_DATABASE = os.getenv("CI_DATABASE", "92d3dfe1c081962d049f74e00a42f687b337d0fa")
CI_SESSION = os.getenv("CI_SESSION", "d36ff1d6f87aa84e1a05eda0357972303d44222d")

def get_cookies():
    return f"ci_database={CI_DATABASE}; ci_session={CI_SESSION}"

@app.on_message(filters.command("start"))
async def start_command(client, message: Message):
    await message.reply("Welcome to the Book Downloader Bot!\nSend /download to start downloading a book.")

@app.on_message(filters.command("cookie"))
async def update_cookies(client, message: Message):
    global CI_DATABASE, CI_SESSION
    try:
        args = message.text.split()
        if len(args) != 3:
            await message.reply("Invalid command format! Use:\n`/cookie <ci_database> <ci_session>`", parse_mode="markdown")
            return

        CI_DATABASE = args[1]
        CI_SESSION = args[2]
        await message.reply("Cookies updated successfully!")
    except Exception as e:
        await message.reply(f"An error occurred while updating cookies: {e}")

@app.on_message(filters.command("download"))
async def download_command(client, message: Message):
    user_id = message.from_user.id
    if user_id in user_tasks:
        await message.reply("You already have an ongoing task. Please wait or send /cancel to stop it.")
        return

    user_tasks[user_id] = {"status": "awaiting_book_id"}
    await message.reply("Please send the book ID to start downloading.")

@app.on_message(filters.command("cancel"))
async def cancel_command(client, message: Message):
    user_id = message.from_user.id
    if user_id not in user_tasks:
        await message.reply("You don't have any ongoing tasks.")
        return

    user_folder = f"downloads/{user_id}/"
    shutil.rmtree(user_folder, ignore_errors=True)
    user_tasks.pop(user_id, None)
    await message.reply("Your task has been canceled and all temporary data has been deleted.")

@app.on_message(filters.text)
async def handle_book_id(client, message: Message):
    user_id = message.from_user.id
    if user_id not in user_tasks:
        return

    user_task = user_tasks[user_id]
    if user_task["status"] == "awaiting_book_id":
        book_id = message.text.strip()
        user_task["book_id"] = book_id
        user_task["status"] = "downloading"
        await download_book(client, message, user_task)

async def download_book(client, message, user_task):
    user_id = message.from_user.id
    book_id = user_task["book_id"]
    user_folder = f"downloads/{user_id}/"
    os.makedirs(user_folder, exist_ok=True)

    try:
        # Fetch book details
        response = requests.get(f"https://yctpublication.com/master/api/MasterController/bookdetails?bookid={book_id}", headers={"Cookie": get_cookies()})
        if response.status_code == 200:
            content_type = response.headers.get("Content-Type", "").lower()
            if "application/json" in content_type:
                book_details = response.json()
                if book_details.get("status"):
                    book_name = book_details["data"].get("book_name", "Unknown Book").replace(" ", "_")
                    no_of_pages = int(book_details["data"].get("no_of_pages", 0))
                else:
                    raise Exception(f"API Error: {book_details.get('message', 'Unknown error')}")
            elif "text/html" in content_type:
                text_content = response.text
                json_match = re.search(r'({.*})', text_content)
                if json_match:
                    book_details = json.loads(json_match.group(0))
                    if book_details.get("status"):
                        book_name = book_details["data"].get("book_name", "Unknown Book").replace(" ", "_")
                        no_of_pages = int(book_details["data"].get("no_of_pages", 0))
                    else:
                        raise Exception(f"API Error: {book_details.get('message', 'Unknown error')}")
                else:
                    raise Exception("Failed to parse JSON from HTML response.")
            else:
                raise Exception(f"Unexpected response format: {content_type}")
        else:
            raise Exception(f"Failed to fetch book details. HTTP Status: {response.status_code}")

        if no_of_pages == 0:
            raise Exception("Invalid number of pages.")

        stage = await app.send_message(user_id, f"__**Downloading:**__\n\n📕 **Book Name:** {book_name}\n🔖 **Total Pages:** {no_of_pages}\n\n> __**Powered by Team SPY*__")

        image_files = []
        failed_pages = []

        with ThreadPoolExecutor(max_workers=20) as executor:
            loop = asyncio.get_event_loop()
            futures = []
            for page in range(1, no_of_pages + 1):
                future = loop.run_in_executor(executor, download_page, page, book_id, user_folder)
                futures.append(future)
            results = await asyncio.gather(*futures)

        for page, success in enumerate(results, 1):
            image_path = f"{user_folder}{page}.jpg"
            if success:
                image_files.append(image_path)
            else:
                failed_pages.append(page)

        if not image_files:
            raise Exception("No valid images were downloaded.")

        await stage.edit("Creating PDFs")
        pdf_path = f"{user_folder}{book_name}.pdf"
        await create_pdf_from_images(image_files, pdf_path)
        await compress_pdf(pdf_path)

        await stage.edit("__**Uploading**__")
        await client.send_document(
            chat_id=user_id, document=pdf_path, caption=f"Here is your book: {book_name}"
        )
        await stage.delete()

        if failed_pages:
            await message.reply(f"Warning: Failed to download pages: {failed_pages}")

    except Exception as e:
        await message.reply(f"An error occurred: {e}")
    finally:
        shutil.rmtree(user_folder, ignore_errors=True)
        user_tasks.pop(user_id, None)

def download_page(page, book_id, user_folder):
    page_url = f"https://yctpublication.com/getPage/{book_id}/{page}"
    output_file = f"{user_folder}{page}.jpg"
    headers = {
        "accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9,hi-IN;q=0.8,hi;q=0.7",
        "cookie": get_cookies(),
        "dnt": "1",
        "priority": "u=2, i",
        "referer": f"https://yctpublication.com/readbook/{book_id}",
        "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "image",
        "sec-fetch-mode": "no-cors",
        "sec-fetch-site": "same-origin",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }
    try:
        response = requests.get(page_url, headers=headers, timeout=10)
        if response.status_code == 200:
            with open(output_file, "wb") as f:
                f.write(response.content)
            try:
                with Image.open(output_file) as img:
                    img.verify()  # Verify if this is a valid image
                return True
            except (UnidentifiedImageError, IOError):
                print(f"Downloaded file is not a valid image: {output_file}")
                return False
        else:
            print(f"Failed to download page {page}. HTTP Status: {response.status_code}")
            return False
    except Exception as e:
        print(f"Error downloading page {page}: {e}")
        return False

async def create_pdf_from_images(image_paths, output_pdf_path):
    from fpdf import FPDF
    pdf = FPDF()
    for image_path in image_paths:
        try:
            pdf.add_page()
            pdf.image(image_path, x=0, y=0, w=210, h=297)
        except Exception as e:
            print(f"Failed to add image {image_path} to PDF: {e}")
    pdf.output(output_pdf_path)

async def compress_pdf(pdf_path):
    try:
        with open(pdf_path, "rb") as f:
            pdf_reader = PyPDF2.PdfReader(f)
            pdf_writer = PyPDF2.PdfWriter()
            for page in pdf_reader.pages:
                pdf_writer.add_page(page)
            pdf_writer.remove_links()
            with open(pdf_path, "wb") as out:
                pdf_writer.write(out)
    except Exception as e:
        print(f"Compression failed: {e}")

app.run()
