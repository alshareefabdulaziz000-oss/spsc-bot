import os
import asyncio
import logging
import tempfile
import google.generativeai as genai
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from playwright.async_api import async_playwright
from datetime import datetime
import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

import re

_raw_token = os.environ.get("TELEGRAM_TOKEN", "")
_raw_key = os.environ.get("GEMINI_API_KEY", "")

TELEGRAM_TOKEN = re.sub(r'\s+', '', _raw_token)
GEMINI_API_KEY = re.sub(r'\s+', '', _raw_key)

print(f"Token length: {len(TELEGRAM_TOKEN)}")
print(f"Key length: {len(GEMINI_API_KEY)}")


genai.configure(api_key=GEMINI_API_KEY)

FORM_URL = "https://portal.spsc.gov.sa/MEH/Default.aspx?Id=454"

FIXED = {
    "reporter": "Az",
    "email": "aalhazmi50@moh.gov.sa",
    "mobile": "0547995498",
    "category": "Pharmacist",
    "reached_patient": "No",
    "factors": "Lack of knowledge, experience"
}

async def extract_from_image(image_path: str) -> dict:
    model = genai.GenerativeModel("gemini-flash-latest")
    with open(image_path, "rb") as f:
        image_data = f.read()

    prompt = """
    من هذه الصورة لوصفة طبية، استخرج فقط:
    1. MRN (رقم المريض)
    2. تاريخ الوصفة بصيغة DD/MM/YYYY

    أجب بهذا الشكل فقط بدون أي كلام إضافي:
    MRN: xxxxx
    DATE: DD/MM/YYYY
    """

    

    response = model.generate_content([
        prompt,
        {"mime_type": "image/jpeg", "data": image_data}
    ])

    text = response.text.strip()
    mrn = ""
    date = ""

    for line in text.split("\n"):
        if "MRN:" in line:
            mrn = line.split("MRN:")[1].strip()
        if "DATE:" in line:
            date = line.split("DATE:")[1].strip()

    return {"mrn": mrn, "date": date}


async def fill_form(mrn: str, date: str, keyword: str, prescription_type_text: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(FORM_URL, wait_until="networkidle")

        # Did error reach patient → No
        await page.click("input[type='radio'][value='No']")

        # Event Date
        await page.fill("input[id*='EventDate'], input[placeholder*='date'], input[name*='date']", date)

        # Prescription type → Others
        await page.click("input[type='radio'][value*='Other'], label:has-text('Other')")

        # Prescription type text
        await page.fill("input[id*='PrescriptionOther'], textarea[id*='PrescriptionOther']", prescription_type_text)

        # MRN
        await page.fill("input[id*='MRN'], input[name*='MRN']", mrn)

        # Keyword logic
        if keyword.lower() == "omeprazole":
            medication = "omeprazole"
            description = "Doctor write medicine out of privilege"
        elif keyword.lower() == "3 days":
            medication = ""
            description = "Doctor wrote medicine more than 3 days"
        elif keyword.lower() == "no diagnosis":
            medication = ""
            description = "Didn't write the diagnosis"
        else:
            medication = keyword
            description = keyword

        # Description
        await page.fill("textarea[id*='Description'], textarea[id*='Event']", description)

        # Reporter name
        await page.fill("input[id*='Reporter'], input[id*='reporter']", FIXED["reporter"])

        # Email
        await page.fill("input[id*='Email'], input[type='email']", FIXED["email"])

        # Mobile
        await page.fill("input[id*='Mobile'], input[id*='mobile']", FIXED["mobile"])

        # Medication name if omeprazole
        if medication == "omeprazole":
            try:
                await page.select_option("select[id*='Medication']", label="omeprazole")
            except:
                pass

        await asyncio.sleep(1)

        # Submit
        await page.click("input[type='submit'], button[type='submit'], input[value*='Submit']")
        await asyncio.sleep(3)

        await browser.close()


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    
    if not message.photo and not message.document:
        await message.reply_text("📎 أرسل صورة الوصفة مع الكلمة المفتاحية")
        return

    keyword = message.caption or ""
    if not keyword:
        await message.reply_text("⚠️ لم تكتب الكلمة المفتاحية في caption الصورة")
        return

    await message.reply_text("⏳ جاري المعالجة...")

    # Download image
    if message.photo:
        photo = message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
    else:
        file = await context.bot.get_file(message.document.file_id)

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        image_path = tmp.name

    try:
        # Extract MRN and date from image
        extracted = await extract_from_image(image_path)
        mrn = extracted["mrn"]
        date = extracted["date"]

        if not mrn or not date:
            await message.reply_text(f"⚠️ لم أتمكن من قراءة البيانات\nMRN: {mrn}\nDate: {date}")
            return

        # Fill form
        await fill_form(mrn, date, keyword.strip(), keyword.strip())

        await message.reply_text(
            f"Done ✔️\n\nMRN: {mrn}\nDate: {date}\nKeyword: {keyword}"
        )

    except Exception as e:
        logger.error(f"Error: {e}")
        await message.reply_text(f"❌ خطأ: {str(e)}")

    finally:
        os.unlink(image_path)


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_message))
    logger.info("Bot started...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
