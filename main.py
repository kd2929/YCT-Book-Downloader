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
import PyPDF2

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

@app.on_message(filters.text & ~filters.command("start") & ~filters.command("download") & ~filters.command("cancel") & ~filters.command("cookie"))
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

def download_page(page, book_id, user_folder):
    
    try:page_url = f"https://yctbooksprime.com/ebook/{book_id}/view-pdf?pageNumber={page}"
        output_file = f"{user_folder}{page}.jpg"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Cookie": get_cookies(),
            "Referer": f"https://yctbooksprime.com/ebook/{book_id}"
        }
        
        response = requests.get(page_url, headers=headers, timeout=30)
        response.raise_for_status()
        
        with open(output_file, "wb") as f:
            f.write(response.content)
            
        # Verify image
        with Image.open(output_file) as img:
            img.verify()
            return True
            
    except Exception as e:
        print(f"Error downloading page {page}: {str(e)}")
        if os.path.exists(output_file):
            os.remove(output_file)
        return False

async def download_book(client, message, user_task):
    user_id = message.from_user.id
    book_id = user_task["book_id"]
    user_folder = f"downloads/{user_id}/"
    
    if os.path.exists(user_folder):
        shutil.rmtree(user_folder)
    os.makedirs(user_folder)

    try:
        # Fetch book details
        headers = {
            "Cookie": get_cookies(),
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        
        response = requests.get(
            f"https://yctbooksprime.com/ebook/{book_id}",
            headers=headers,
            timeout=30
        )
        response.raise_for_status()
        
        book_details = response.json()
        if not book_details.get("status"):
            raise Exception("Invalid book ID or unable to fetch book details")
            
        book_name = book_details["data"]["book_name"].replace(" ", "_")
        no_of_pages = int(book_details["data"]["no_of_pages"])
        
        if no_of_pages <= 0:
            raise Exception("Invalid number of pages")

        status_message = await message.reply(
            f"📚 Downloading: {book_name}\n"
            f"📄 Total Pages: {no_of_pages}\n"
            "⏳ Progress: 0%"
        )

        # Download pages
        successful_downloads = []
        failed_pages = []
        
        for page in range(1, no_of_pages + 1):
            if download_page(page, book_id, user_folder):
                successful_downloads.append(f"{user_folder}{page}.jpg")
            else:
                failed_pages.append(page)
                
            if page % 5 == 0:
                progress = (page / no_of_pages) * 100
                await status_message.edit_text(
                    f"📚 Downloading: {book_name}\n"
                    f"📄 Total Pages: {no_of_pages}\n"
                    f"⏳ Progress: {progress:.1f}%"
                )

        if not successful_downloads:
            raise Exception("Failed to download any pages")

        await status_message.edit_text("📑 Creating PDF...")
        
        # Create PDF
        pdf_path = f"{user_folder}{book_name}.pdf"
        await create_pdf_from_images(successful_downloads, pdf_path)
        
        # Compress PDF
        await status_message.edit_text("🗜 Compressing PDF...")
        await compress_pdf(pdf_path)

        # Upload PDF
        await status_message.edit_text("📤 Uploading PDF...")
        await client.send_document(
            chat_id=user_id,
            document=pdf_path,
            caption=f"📚 {book_name}\n📄 Pages: {len(successful_downloads)}"
        )
        
        if failed_pages:
            await message.reply(f"⚠️ Failed to download pages: {', '.join(map(str, failed_pages))}")
            
        await status_message.delete()

    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}")
        
    finally:
        if os.path.exists(user_folder):
            shutil.rmtree(user_folder)
        user_tasks.pop(user_id, None)

async def create_pdf_from_images(image_paths, output_pdf_path):
    from fpdf import FPDF
    
    pdf = FPDF()
    for image_path in sorted(image_paths, key=lambda x: int(os.path.basename(x).split('.')[0])):
        try:
            pdf.add_page()
            pdf.image(image_path, x=0, y=0, w=210, h=297)
        except Exception as e:
            print(f"Error adding page to PDF: {str(e)}")
    
    pdf.output(output_pdf_path)

async def compress_pdf(pdf_path):
    try:
        reader = PyPDF2.PdfReader(pdf_path)
        writer = PyPDF2.PdfWriter()
        
        for page in reader.pages:
            writer.add_page(page)
            
        with open(pdf_path, "wb") as f:
            writer.write(f)
            
    except Exception as e:
        print(f"Error compressing PDF: {str(e)}")

if __name__ == "__main__":
    app.run()
