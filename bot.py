import os
import re
import logging
import tempfile
import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

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


def fill_form(mrn: str, date: str, keyword: str, prescription_text: str) -> bool:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })

    response = session.get(FORM_URL)
    soup = BeautifulSoup(response.text, "html.parser")

    form_data = {}
    for inp in soup.find_all("input"):
        name = inp.get("name", "")
        value = inp.get("value", "")
        if name:
            form_data[name] = value

    if keyword.lower() == "omeprazole":
        description = "Doctor write medicine out of privilege"
        medication = "omeprazole"
    elif keyword.lower() == "3 days":
        description = "Doctor wrote medicine more than 3 days"
        medication = ""
    elif keyword.lower() == "no diagnosis":
        description = "Didn't write the diagnosis"
        medication = ""
    else:
        description = keyword
        medication = keyword

    form_data.update({
        "ctl00$ContentPlaceHolder1$rdlReachPatient": "No",
        "ctl00$ContentPlaceHolder1$txtEventDate": date,
        "ctl00$ContentPlaceHolder1$rdlPrescriptionType": "Others",
        "ctl00$ContentPlaceHolder1$txtPrescriptionOther": prescription_text,
        "ctl00$ContentPlaceHolder1$txtMRN": mrn,
        "ctl00$ContentPlaceHolder1$txtDescription": description,
        "ctl00$ContentPlaceHolder1$txtReporterName": "Az",
        "ctl00$ContentPlaceHolder1$txtEmail": "aalhazmi50@moh.gov.sa",
        "ctl00$ContentPlaceHolder1$txtMobile": "0547995498",
        "ctl00$ContentPlaceHolder1$ddlStaffCategory": "Pharmacist",
        "ctl00$ContentPlaceHolder1$ddlFactors": "Lack of knowledge, experience",
        "ctl00$ContentPlaceHolder1$btnSubmit": "Submit",
    })

    if medication:
        form_data["ctl00$ContentPlaceHolder1$ddlMedication"] = medication

    submit = session.post(FORM_URL, data=form_data)
    return submit.status_code == 200


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message.photo and not message.document:
        await message.reply_text("أرسل صورة الوصفة مع الكلمة المفتاحية")
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

        success = fill_form(mrn, date, keyword.strip(), keyword.strip())

        if success:
            await message.reply_text(f"Done ✔️\n\nMRN: {mrn}\nDate: {date}\nKeyword: {keyword}")
        else:
            await message.reply_text("⚠️ تم الإرسال لكن تحقق من النموذج يدوياً")

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
