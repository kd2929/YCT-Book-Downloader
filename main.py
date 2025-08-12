import os
import shutil
import asyncio
import aiohttp
import aiofiles
from pyrogram import Client, filters
from pyrogram.types import Message
from fpdf import FPDF
import re, html as html_lib, json

# ====== BOT CONFIG ======
API_ID = 24250238            # अपना Telegram API_ID डालें
API_HASH = "cb3f118ce5553dc140127647edcf3720"  # अपना API_HASH डालें
BOT_TOKEN = "6289889847:AAHRaFFoLLkxdPCEBGJhWYVjKaCcEVXIhmM"  # अपना Bot Token डालें

app = Client("yct_booksprime_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ====== GLOBAL STORAGE ======
user_tasks = {}
XSRF_TOKEN = ""
YCT_SESSION = ""

# ====== BOT COMMANDS ======

@app.on_message(filters.command("start"))
async def start_cmd(_, m: Message):
    await m.reply(
        "📚 **YCT Books Prime Downloader Bot**\n\n"
        "1️⃣ `/cookie <XSRF-TOKEN> <yct_session>` - Cookies सेट करो\n"
        "2️⃣ `/download` - Book download शुरू करो\n"
        "3️⃣ `/cancel` - Current task cancel करो"
    )

@app.on_message(filters.command("cookie"))
async def cookie_cmd(_, m: Message):
    global XSRF_TOKEN, YCT_SESSION
    args = m.text.split()
    if len(args) != 3:
        return await m.reply("❌ Usage: `/cookie <XSRF-TOKEN> <yct_session>`")
    XSRF_TOKEN, YCT_SESSION = args[1], args[2]
    await m.reply("✅ Cookies updated successfully!")

@app.on_message(filters.command("cancel"))
async def cancel_cmd(_, m: Message):
    uid = m.from_user.id
    if uid in user_tasks:
        user_tasks.pop(uid, None)
        shutil.rmtree(f"downloads/{uid}", ignore_errors=True)
        await m.reply("🛑 Task cancelled.")
    else:
        await m.reply("ℹ️ कोई active task नहीं है।")

@app.on_message(filters.command("download"))
async def download_cmd(_, m: Message):
    uid = m.from_user.id
    if not XSRF_TOKEN or not YCT_SESSION:
        return await m.reply("❌ पहले `/cookie` command से cookies सेट करें।")
    if uid in user_tasks:
        return await m.reply("⚠️ एक task already चल रहा है।")

    user_tasks[uid] = {"status": "awaiting_book_id"}
    await m.reply("📖 Book ID भेजें...")

@app.on_message(filters.text & ~filters.command(["start", "cookie", "cancel", "download"]))
async def handle_book_id(_, m: Message):
    uid = m.from_user.id
    if uid not in user_tasks or user_tasks[uid]["status"] != "awaiting_book_id":
        return
    book_id = m.text.strip()
    user_tasks[uid]["status"] = "downloading"
    await download_book(m, book_id)

# ====== CORE FUNCTIONS ======

async def download_book(m: Message, book_id: str):
    uid = m.from_user.id
    temp_dir = f"downloads/{uid}/"
    shutil.rmtree(temp_dir, ignore_errors=True)
    os.makedirs(temp_dir, exist_ok=True)

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Cookie": f"XSRF-TOKEN={XSRF_TOKEN}; yct_session={YCT_SESSION}",
        "Referer": f"https://yctbooksprime.com/ebook/{book_id}/view-pdf"
    }

    async with aiohttp.ClientSession(headers=headers) as session:
        # STEP 1: Get Livewire payload
        payload = await build_livewire_payload(session, book_id)
        if not payload:
            user_tasks.pop(uid, None)
            return await m.reply("❌ Book details fetch नहीं हो सके।")

        # STEP 2: Livewire POST to get total pages
        async with session.post("https://yctbooksprime.com/livewire/update", json=payload) as resp:
            if resp.status != 200:
                user_tasks.pop(uid, None)
                return await m.reply("❌ Livewire request failed.")
            data = await resp.json()
            try:
                no_pages = int(eval(data["components"][0]["snapshot"])["data"]["no_of_pages"])
            except:
                user_tasks.pop(uid, None)
                return await m.reply("❌ Total pages पढ़ने में error।")

        status_msg = await m.reply(f"📚 Downloading Book ID: {book_id}\n📄 Pages: {no_pages}\n⏳ Progress: 0%")

        # STEP 3: Download pages with retry
        successful, failed = [], []
        for page in range(1, no_pages + 1):
            ok = await try_download_page(session, book_id, page, temp_dir)
            if ok:
                successful.append(ok)
            else:
                failed.append(page)

            # Progress update हर 5 pages पर
            if page % 5 == 0 or page == no_pages:
                progress = (page / no_pages) * 100
                await status_msg.edit(f"📚 Downloading Book ID: {book_id}\n📄 Pages: {no_pages}\n⏳ Progress: {progress:.1f}%")

        # STEP 4: Retry failed pages
        if failed:
            retry_failed = []
            for page in failed:
                ok = await try_download_page(session, book_id, page, temp_dir, retries=3)
                if ok:
                    successful.append(ok)
                else:
                    retry_failed.append(page)
            failed = retry_failed

        if not successful:
            user_tasks.pop(uid, None)
            return await status_msg.edit("❌ No pages downloaded.")

        # STEP 5: Create PDF
        pdf_path = os.path.join(temp_dir, f"{book_id}.pdf")
        await create_pdf(successful, pdf_path)

        # STEP 6: Send PDF
        caption = f"📚 Book ID: {book_id}\n📄 Pages: {len(successful)}"
        if failed:
            caption += f"\n⚠️ Failed pages: {', '.join(map(str, failed))}"
        await m.reply_document(pdf_path, caption=caption)

    user_tasks.pop(uid, None)
    shutil.rmtree(temp_dir, ignore_errors=True)
    await status_msg.delete()

async def build_livewire_payload(session, book_id):
    async with session.get(f"https://yctbooksprime.com/ebook/{book_id}") as resp:
        if resp.status != 200:
            return None
        html = await resp.text()
        token_match = re.search(r'name="csrf-token" content="(.*?)"', html)
        snap_match = re.search(r'wire:snapshot="(.*?)"', html)
        if not token_match or not snap_match:
            return None
        _token = token_match.group(1)
        snapshot = html_lib.unescape(snap_match.group(1))
        payload = {
            "_token": _token,
            "components": [
                {
                    "snapshot": snapshot,
                    "updates": {},
                    "calls": [{"path": "", "method": "incrementPage", "params": []}]
                }
            ]
        }
        return payload

async def try_download_page(session, book_id, page, folder, retries=1):
    for attempt in range(retries):
        path = await download_page(session, book_id, page, folder)
        if path:
            return path
    return None

async def download_page(session, book_id, page, folder):
    try:
        url = f"https://yctbooksprime.com/ebook/temp-ebook/{book_id}/?pageNumber={page}"
        out_path = os.path.join(folder, f"{page}.jpg")
        async with session.get(url) as resp:
            if resp.status != 200:
                return None
            f = await aiofiles.open(out_path, "wb")
            await f.write(await resp.read())
            await f.close()
        return out_path
    except:
        return None

async def create_pdf(images, out_pdf):
    pdf = FPDF()
    for img in sorted(images, key=lambda x: int(os.path.basename(x).split('.')[0])):
        pdf.add_page()
        pdf.image(img, x=0, y=0, w=210, h=297)
    pdf.output(out_pdf)

if __name__ == "__main__":
    app.run()
