import os
import subprocess

# تحميل Chromium عند بدء التشغيل
try:
    print("Installing Chromium...")
    subprocess.run(["playwright", "install", "chromium"], check=True)
    print("Chromium installed successfully")
except Exception as e:
    print(f"Playwright install error: {e}")

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
    # تأكد من تثبيت Chromium لحظة الاستخدام
    try:
        logger.info("Verifying Chromium installation...")
        subprocess.run(["playwright", "install", "chromium"], check=True, capture_output=True)
    except Exception as e:
        logger.error(f"Install error: {e}")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context()
        page = await context.new_page()

        try:
            await page.goto(FORM_URL, wait_until="networkidle", timeout=60000)
            await asyncio.sleep(2)

            try:
                await page.click("input[type='radio'][value='No']", timeout=5000)
            except:
                pass

            try:
                await page.fill("input[id*='MRN'], input[name*='MRN']", mrn, timeout=5000)
            except:
                pass

            try:
                await page.fill("input[id*='EventDate'], input[name*='EventDate']", date, timeout=5000)
            except:
                pass

            try:
                await page.click("input[type='radio'][value*='Other']", timeout=5000)
            except:
                pass

            try:
                await page.fill("input[id*='Other'], textarea[id*='Other']", case["prescription_text"], timeout=5000)
            except:
                pass

            try:
                await page.f​​​​​​​​​​​​​​​​
