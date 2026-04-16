import os
import re
import logging
import tempfile
import asyncio
import google.generativeai as genai
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_raw_token = os.environ.get("TELEGRAM_TOKEN", "")
_raw_key = os.environ.get("GEMINI_API_KEY", "")

TELEGRAM_TOKEN = re.sub(r'\s+', '', _raw_token)
GEMINI_API_KEY = re.sub(r'\s+', '', _raw_key)

print(f"Token length: {len(TELEGRAM_TOKEN)}")
print(f"Key length: {len(GEMINI_API_KEY)}")

genai.configure(api_key=GEMINI_API_KEY)

FORM_URL = "https://portal.spsc.gov.sa/MEH/Default.aspx?Id=454"

FIXED_DATA = {
    "reporter": "Az",
    "email": "aalhazmi50@moh.gov.sa",
    "mobile": "0547995498",
}


def extract_from_image(image_path: str) -> dict:
    model = genai.GenerativeModel("gemini-flash-latest")
    with open(image_path, "rb") as f:
        image_data = f.read()
    prompt = """من هذه الصورة لوصفة طبية، استخرج فقط:
1. MRN (رقم المريض)
2. تاريخ الوصفة بصيغة DD/MM/YYYY
أجب بهذا الشكل فقط:
MRN: xxxxx
DATE: DD/MM/YYYY"""
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


def get_case_details(keyword: str) -> dict:
    k = keyword.lower().strip()
    if k == "omeprazole":
        return {
            "medication": "omeprazole",
            "description": "Doctor write medicine out of privilege",
            "prescription_text": keyword,
        }
    elif k == "3 days":
        return {
            "medication": "",
            "description": "Doctor wrote medicine more than 3 days",
            "prescription_text": keyword,
        }
    elif k == "no diagnosis":
        return {
            "medication": "",
            "description": "Didn't write the diagnosis",
            "prescription_text": keyword,
        }
    else:
        return {
            "medication": keyword,
            "description": keyword,
            "prescription_text": keyword,
        }


async def fill_form_playwright(mrn: str, date: str, case: dict) -> bool:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context()
        page = await context.new_page()

        try:
            await page.goto(FORM_URL, wait_until="networkidle", timeout=60000)
            await asyncio.sleep(2)

            # Did error reach patient → No
            try:
                await page.click("input[type='radio'][value='No']", timeout=5000)
            except:
                pass

            # MRN
            try:
                await page.fill("input[id*='MRN'], input[name*='MRN']", mrn, timeout=5000)
            except:
                pass

            # Event Date
            try:
                await page.fill("input[id*='EventDate'], input[name*='EventDate']", date, timeout=5000)
            except:
                pass

            # Prescription type → Others
            try:
                await page.click("input[type='radio'][value*='Other']", timeout=5000)
            except:
                pass

            # Prescription other text
            try:
                await page.fill("input[id*='Other'], textarea[id*='Other']", case["prescription_text"], timeout=5000)
            except:
                pass

            # Description
            try:
                await page.fill("textarea[id*='Description']", case["description"], timeout=5000)
            except:
                pass

            # Reporter name
            try:
                await page.fill("input[id*='Reporter'], input[id*='reporter']", FIXED_DATA["reporter"], timeout=5000)
            except:
                pass

            # Email
            try:
                await page.fill("input[type='email'], input[id*='Email']", FIXED_DATA["email"], timeout=5000)
            except:
                pass

            # Mobile
            try:
                await page.fill("input[id*='Mobile'], input[id*='mobile']", FIXED_DATA["mobile"], timeout=5000)
            except:
                pass

            await asyncio.sleep(2)

            # Submit
            try:
                await page.click("input[type='submit'], button[type='submit']", timeout=5000)
                await asyncio.sleep(3)
            except:
                pass

            await browser.close()
            return True

        except Exception as e:
            logger.error(f"Playwright error: {e}")
            await browser.close()
            return False


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message.photo and not message.document:
        await message.reply_text("أرسل صورة الوصفة مع الكلمة المفتاحية في caption")
        return
    keyword = message.caption or ""
    if not keyword:
        await message.reply_text("⚠️ اكتب الكلمة المفتاحية في caption الصورة")
        return

    await message.reply_text("⏳ جاري المعالجة...")

    if message.photo:
        file = await context.bot.get_file(message.photo[-1].file_id)
    else:
        file = await context.bot.get_file(message.document.file_id)

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        image_path = tmp.name

    try:
        extracted = extract_from_image(image_path)
        mrn = extracted["mrn"]
        date = extracted["date"]

        if not mrn or not date:
            await message.reply_text(f"⚠️ ما قدرت أقرأ البيانات\nMRN: {mrn}\nDate: {date}")
            return

        case = get_case_details(keyword)

        success = await fill_form_playwright(mrn, date, case)

        if success:
            await message.reply_text(
                f"Done ✔️\n\n"
                f"MRN: {mrn}\n"
                f"Date: {date}\n"
                f"Keyword: {keyword}\n"
                f"Description: {case['description']}"
            )
        else:
            await message.reply_text("⚠️ فشل الإرسال، تحقق من اللوجز")

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
