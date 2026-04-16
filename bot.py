import os
import subprocess

try:
    subprocess.run(["playwright", "install", "chromium"], check=True)
except Exception as e:
    print(f"Install error: {e}")

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

TELEGRAM_TOKEN = re.sub(r'\s+', '', os.environ.get("TELEGRAM_TOKEN", ""))
GEMINI_API_KEY = re.sub(r'\s+', '', os.environ.get("GEMINI_API_KEY", ""))

genai.configure(api_key=GEMINI_API_KEY)

FORM_URL = "https://portal.spsc.gov.sa/MEH/Default.aspx?Id=454"


def extract_from_image(image_path: str) -> dict:
    model = genai.GenerativeModel("gemini-flash-latest")
    with open(image_path, "rb") as f:
        image_data = f.read()
    
    prompt = """من صورة الوصفة الطبية، استخرج:
1. MRN (رقم المريض)
2. DATE (تاريخ الوصفة بصيغة DD/MM/YYYY)
3. GENDER (Male أو Female)
4. DIAGNOSIS (من قسم Indication، اكتب EMPTY إذا فاضي)

أجب بهذا الشكل فقط:
MRN: xxxxx
DATE: DD/MM/YYYY
GENDER: Male
DIAGNOSIS: xxxxx"""
    
    response = model.generate_content([
        prompt,
        {"mime_type": "image/jpeg", "data": image_data}
    ])
    
    text = response.text.strip()
    result = {"mrn": "", "date": "", "gender": "Male", "diagnosis": ""}
    
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("MRN:"):
            result["mrn"] = line.split("MRN:")[1].strip()
        elif line.startswith("DATE:"):
            result["date"] = line.split("DATE:")[1].strip()
        elif line.startswith("GENDER:"):
            result["gender"] = line.split("GENDER:")[1].strip()
        elif line.startswith("DIAGNOSIS:"):
            result["diagnosis"] = line.split("DIAGNOSIS:")[1].strip()
    
    if not result["diagnosis"] or result["diagnosis"].upper() == "EMPTY":
        result["diagnosis"] = "headache"
    
    return result


def get_case_details(keyword: str) -> dict:
    k = keyword.lower().strip()
    if k == "omeprazole":
        return {
            "description": "Doctor write medicine out of privilege",
            "medication_search": "omeprazole",
            "type_of_error": "12",  # Wrong/missed indication
        }
    elif k == "3 days":
        return {
            "description": "Doctor wrote medicine more than 3 days",
            "medication_search": "paracetamol",
            "type_of_error": "9",  # wrong/missed duration
        }
    elif k == "no diagnosis":
        return {
            "description": "Didn't write the diagnosis",
            "medication_search": "paracetamol",
            "type_of_error": "12",
        }
    else:
        return {
            "description": keyword,
            "medication_search": keyword,
            "type_of_error": "1",
        }


async def fill_form(data: dict) -> dict:
    result = {"success": False, "error": "", "screenshot_path": ""}
    
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True, capture_output=True)
    except:
        pass
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()
        
        try:
            logger.info("Opening form...")
            await page.goto(FORM_URL, wait_until="networkidle", timeout=90000)
            await asyncio.sleep(3)
            
            # 1. Error Reach Patient → No
            await page.click("#ContentPlaceHolder1_ErrorReachPatient_0")
            await asyncio.sleep(0.5)
            
            # 2. Event Date
            await page.fill("#ContentPlaceHolder1_Event_Date_Txt", data['date'])
            await asyncio.sleep(0.5)
            
            # 3. Prescription type → Other/s (Wasfaty_Chk_0)
            await page.click("#ContentPlaceHolder1_Wasfaty_Chk_0")
            await asyncio.sleep(0.5)
            
            # 4. MRN
            await page.fill("#ContentPlaceHolder1_Mr_Txt", data['mrn'])
            await asyncio.sleep(0.5)
            
            # 5. Gender
            gender_value = "1" if data['gender'].lower() == "male" else "2"
            await page.select_option("#ContentPlaceHolder1_Gender_Drop", value=gender_value)
            await asyncio.sleep(0.5)
            
            # 6. Where It Happens → ER Adult (value=3)
            await page.select_option("#ContentPlaceHolder1_WhereItHappen_Drop", value="3")
            await asyncio.sleep(0.5)
            
            # 7. Diagnosis (autocomplete)
            diagnosis_input = page.locator("#ContentPlaceHolder1_txtDiagnosis")
            await diagnosis_input.click()
            await diagnosis_input.fill(data['diagnosis'][:5])
            await asyncio.sleep(3)
            # اختار أول اقتراح
            try:
                await page.keyboard.press("ArrowDown")
                await asyncio.sleep(0.3)
                await page.keyboard.press("Enter")
            except:
                pass
            await asyncio.sleep(1)
            
            # 8. Stage → Prescribing (value=1)
            await page.select_option("#ContentPlaceHolder1_ME_Type_Drop", value="1")
            await asyncio.sleep(0.5)
            
            # 9. Type of Medication Error + Add
            try:
                await page.select_option("#ContentPlaceHolder1_ddlNewTypeOfError", value=data['type_of_error'])
                await asyncio.sleep(0.5)
                await page.click("#ContentPlaceHolder1_NewTypeOfError_Main_Btn")
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Type error: {e}")
            
            # 10. Description
            await page.fill("#ContentPlaceHolder1_Event_Desc_Txt", data['description'])
            await asyncio.sleep(0.5)
            
            # 11. Action Taken → Call physician (value=3)
            await page.select_option("#ContentPlaceHolder1_ActionTaken_Drop", value="3")
            await asyncio.sleep(0.5)
            
            # 12. Medication - نختار خيار يحتوي على البحث
            try:
                med_options = await page.locator("#ContentPlaceHolder1_Generic_Name_Drop option").all_inner_texts()
                med_value = None
                for i, opt in enumerate(med_options):
                    if data['medication_search'].lower() in opt.lower():
                        med_value = str(i)
                        break
                if med_value:
                    await page.select_option("#ContentPlaceHolder1_Generic_Name_Drop", index=int(med_value))
                    await asyncio.sleep(0.5)
                    await page.click("#ContentPlaceHolder1_Add_Med")
                    await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Medication error: {e}")
            
            # 13. Factors → Lack of knowledge (value=4)
            try:
                await page.select_option("#ContentPlaceHolder1_Factors_Drop", value="4")
                await asyncio.sleep(0.5)
                await page.click("#ContentPlaceHolder1_Factors_Main_Btn")
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Factors error: {e}")
            
            # 14. Reporter Name
            await page.fill("#ContentPlaceHolder1_Reporter_Name_Txt", "Az")
            await asyncio.sleep(0.5)
            
            # 15. Email
            await page.fill("#ContentPlaceHolder1_Reporter_Email_Txt", "aalhazmi50@moh.gov.sa")
            await asyncio.sleep(0.5)
            
            # 16. Mobile
            await page.fill("#ContentPlaceHolder1_Reporter_Mobile_Txt", "0547995498")
            await asyncio.sleep(0.5)
            
            # 17. Staff Category → Pharmacist (value=4)
            await page.select_option("#ContentPlaceHolder1_Staff_Cat_Drop", value="4")
            await asyncio.sleep(1)
            
            # Screenshot قبل الإرسال
            screenshot_path = "/tmp/form_preview.png"
            await page.screenshot(path=screenshot_path, full_page=True)
            result["screenshot_path"] = screenshot_path
            
            # 18. Submit
            await page.click("#ContentPlaceHolder1_Submit_Btn")
            await asyncio.sleep(5)
            
            result["success"] = True
            await browser.close()
            return result
            
        except Exception as e:
            logger.error(f"Error: {e}")
            result["error"] = str(e)
            try:
                screenshot_path = "/tmp/form_error.png"
                await page.screenshot(path=screenshot_path, full_page=True)
                result["screenshot_path"] = screenshot_path
            except:
                pass
            try:
                await browser.close()
            except:
                pass
            return result


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message.photo and not message.document:
        await message.reply_text("أرسل صورة الوصفة مع الكلمة المفتاحية")
        return
    
    keyword = message.caption or ""
    if not keyword:
        await message.reply_text("⚠️ اكتب الكلمة المفتاحية")
        return
    
    await message.reply_text("⏳ جاري المعالجة... (دقيقتين)")
    
    if message.photo:
        file = await context.bot.get_file(message.photo[-1].file_id)
    else:
        file = await context.bot.get_file(message.document.file_id)
    
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        image_path = tmp.name
    
    try:
        extracted = extract_from_image(image_path)
        
        if not extracted["mrn"] or not extracted["date"]:
            await message.reply_text(f"⚠️ فشل قراءة البيانات\nMRN: {extracted['mrn']}\nDate: {extracted['date']}")
            return
        
        case = get_case_details(keyword)
        
        full_data = {
            "mrn": extracted["mrn"],
            "date": extracted["date"],
            "gender": extracted["gender"],
            "diagnosis": extracted["diagnosis"],
            "description": case["description"],
            "medication_search": case["medication_search"],
            "type_of_error": case["type_of_error"],
        }
        
        await message.reply_text(
            f"📋 البيانات:\n"
            f"MRN: {full_data['mrn']}\n"
            f"Date: {full_data['date']}\n"
            f"Gender: {full_data['gender']}\n"
            f"Diagnosis: {full_data['diagnosis']}\n\n"
            f"جاري ملء النموذج..."
        )
        
        result = await fill_form(full_data)
        
        if result.get("screenshot_path") and os.path.exists(result["screenshot_path"]):
            try:
                with open(result["screenshot_path"], "rb") as f:
                    await message.reply_photo(photo=f, caption="📸 لقطة قبل الإرسال")
            except:
                pass
        
        if result["success"]:
            await message.reply_text(f"Done ✔️\n\nMRN: {full_data['mrn']}\nDate: {full_data['date']}\n\n✅ تحقق من الموقع")
        else:
            await message.reply_text(f"⚠️ خطأ: {result.get('error', '')}")
    
    except Exception as e:
        logger.error(f"Main error: {e}")
        await message.reply_text(f"❌ {str(e)}")
    finally:
        try:
            os.unlink(image_path)
        except:
            pass


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_message))
    logger.info("Bot started...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
