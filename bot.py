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
    "where_it_happens": "ER Adult",
    "stage": "Prescribing",
    "action_taken": "Call the physician to complete the missing information",
    "factors": "Lack of knowledge, experience",
    "staff_category": "Pharmacist",
}


def extract_from_image(image_path: str) -> dict:
    """استخراج البيانات من صورة الوصفة"""
    model = genai.GenerativeModel("gemini-flash-latest")
    with open(image_path, "rb") as f:
        image_data = f.read()
    
    prompt = """من هذه الصورة لوصفة طبية، استخرج البيانات التالية بدقة:

1. MRN: رقم المريض (Medical Record Number)
2. DATE: تاريخ الوصفة بصيغة DD/MM/YYYY (استخرج السنة الصحيحة من الصورة)
3. GENDER: جنس المريض (Male أو Female) - ابحث عن كلمة Gender أو علامة ذكر/أنثى
4. DIAGNOSIS: التشخيص من قسم Indication أو Diagnosis. إذا كان فاضياً اكتب "EMPTY"

أجب بهذا الشكل بالضبط بدون أي نص إضافي:
MRN: xxxxx
DATE: DD/MM/YYYY
GENDER: Male
DIAGNOSIS: xxxxx"""
    
    response = model.generate_content([
        prompt,
        {"mime_type": "image/jpeg", "data": image_data}
    ])
    
    text = response.text.strip()
    result = {"mrn": "", "date": "", "gender": "", "diagnosis": ""}
    
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
    
    # إذا Diagnosis فارغ، نستخدم خيار افتراضي
    if not result["diagnosis"] or result["diagnosis"].upper() == "EMPTY":
        result["diagnosis"] = "headache"
    
    return result


def get_case_details(keyword: str) -> dict:
    """تحديد تفاصيل الحالة حسب الكلمة المفتاحية"""
    k = keyword.lower().strip()
    if k == "omeprazole":
        return {
            "medication": "omeprazole",
            "description": "Doctor write medicine out of privilege",
            "prescription_text": keyword,
        }
    elif k == "3 days":
        return {
            "medication": "paracetamol",
            "description": "Doctor wrote medicine more than 3 days",
            "prescription_text": keyword,
        }
    elif k == "no diagnosis":
        return {
            "medication": "paracetamol",
            "description": "Didn't write the diagnosis",
            "prescription_text": keyword,
        }
    else:
        return {
            "medication": keyword,
            "description": keyword,
            "prescription_text": keyword,
        }


async def select_custom_dropdown(page, label_text: str, option_text: str):
    """اختيار قيمة من dropdown مخصص"""
    try:
        # محاولة الضغط على الحقل بناءً على النص المجاور
        dropdown = page.locator(f"xpath=//label[contains(text(),'{label_text}')]/following::div[contains(@class,'dropdown') or contains(@class,'select')][1]").first
        await dropdown.click(timeout=5000)
        await asyncio.sleep(1)
        
        # اختيار الخيار
        option = page.locator(f"text={option_text}").first
        await option.click(timeout=5000)
        await asyncio.sleep(1)
        return True
    except Exception as e:
        logger.error(f"Dropdown error for {label_text}: {e}")
        return False


async def fill_form_playwright(data: dict) -> dict:
    """ملء النموذج كاملاً"""
    result = {"success": False, "error": "", "screenshot_path": ""}
    
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True, capture_output=True)
    except Exception as e:
        logger.error(f"Install error: {e}")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, 
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()
        
        try:
            logger.info("Opening form...")
            await page.goto(FORM_URL, wait_until="networkidle", timeout=90000)
            await asyncio.sleep(3)
            
            # 1. Did the error reach the patient → No
            logger.info("Setting 'reach patient' to No...")
            try:
                await page.get_by_text("No", exact=True).first.click(timeout=10000)
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Radio No error: {e}")
            
            # 2. Event Date
            logger.info(f"Filling Event Date: {data['date']}")
            try:
                date_inputs = await page.locator("input[type='text']").all()
                for inp in date_inputs:
                    placeholder = await inp.get_attribute("placeholder") or ""
                    parent_text = await inp.evaluate("el => el.parentElement.parentElement.innerText")
                    if "event date" in parent_text.lower() or "event date" in placeholder.lower():
                        await inp.fill(data['date'])
                        break
            except Exception as e:
                logger.error(f"Event date error: {e}")
            
            # 3. Prescription type → Other/s
            logger.info("Selecting Other/s prescription type...")
            try:
                await page.get_by_text("Other/s", exact=False).first.click(timeout=10000)
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Prescription type error: {e}")
            
            # 4. Patient MRN
            logger.info(f"Filling MRN: {data['mrn']}")
            try:
                # البحث عن الحقل بعد label "Patient MRN"
                mrn_input = page.locator("xpath=//label[contains(text(),'MRN')]/following::input[1]").first
                await mrn_input.fill(data['mrn'], timeout=10000)
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"MRN error: {e}")
            
            # 5. Gender (Male/Female)
            logger.info(f"Selecting Gender: {data['gender']}")
            try:
                gender_dropdown = page.locator("xpath=//label[contains(text(),'Gender')]/following::div[1]").first
                await gender_dropdown.click(timeout=10000)
                await asyncio.sleep(1)
                await page.get_by_text(data['gender'], exact=True).first.click(timeout=5000)
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Gender error: {e}")
            
            # 6. Where It Happens → ER Adult
            logger.info("Selecting Where It Happens: ER Adult")
            try:
                where_dropdown = page.locator("xpath=//label[contains(text(),'Where It Happens')]/following::div[1]").first
                await where_dropdown.click(timeout=10000)
                await asyncio.sleep(1)
                await page.get_by_text("ER Adult", exact=True).first.click(timeout=5000)
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Where error: {e}")
            
            # 7. Diagnosis (autocomplete)
            logger.info(f"Filling Diagnosis: {data['diagnosis']}")
            try:
                diagnosis_input = page.locator("xpath=//label[contains(text(),'Diagnosis')]/following::input[1]").first
                # أول 3 أحرف من التشخيص
                search_term = data['diagnosis'][:5] if len(data['diagnosis']) >= 3 else "headache"
                await diagnosis_input.fill(search_term, timeout=10000)
                await asyncio.sleep(3)  # انتظار ظهور القائمة
                
                # محاولة اختيار أول نتيجة
                try:
                    await page.keyboard.press("ArrowDown")
                    await asyncio.sleep(0.5)
                    await page.keyboard.press("Enter")
                except:
                    pass
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Diagnosis error: {e}")
            
            # 8. Stage of Medication Error → Prescribing
            logger.info("Selecting Stage: Prescribing")
            try:
                stage_dropdown = page.locator("xpath=//label[contains(text(),'Stage of Medication')]/following::div[1]").first
                await stage_dropdown.click(timeout=10000)
                await asyncio.sleep(1)
                await page.get_by_text("Prescribing", exact=True).first.click(timeout=5000)
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Stage error: {e}")
            
            # 9. Description of event
            logger.info(f"Filling Description: {data['description']}")
            try:
                desc_textarea = page.locator("xpath=//label[contains(text(),'Description')]/following::textarea[1]").first
                await desc_textarea.fill(data['description'], timeout=10000)
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Description error: {e}")
            
            # 10. Action Taken → Call the physician...
            logger.info("Selecting Action Taken")
            try:
                action_dropdown = page.locator("xpath=//label[contains(text(),'Action Taken')]/following::div[1]").first
                await action_dropdown.click(timeout=10000)
                await asyncio.sleep(1)
                await page.get_by_text("Call the physician", exact=False).first.click(timeout=5000)
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Action error: {e}")
            
            # 11. Medication Name + Add
            logger.info(f"Adding Medication: {data['medication']}")
            try:
                med_dropdown = page.locator("xpath=//label[contains(text(),'Medication Name')]/following::div[contains(@class,'dropdown') or contains(@class,'select')][1]").first
                await med_dropdown.click(timeout=10000)
                await asyncio.sleep(1)
                # البحث عن الدواء
                await page.keyboard.type(data['medication'])
                await asyncio.sleep(2)
                await page.keyboard.press("ArrowDown")
                await page.keyboard.press("Enter")
                await asyncio.sleep(1)
                
                # ضغط زر Add الخاص بالدواء
                add_med_btn = page.locator("xpath=//label[contains(text(),'Medication Name')]/following::button[contains(text(),'Add')][1]").first
                await add_med_btn.click(timeout=5000)
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Medication error: {e}")
            
            # 12. Factors → Lack of knowledge + Add
            logger.info("Adding Factor: Lack of knowledge")
            try:
                factors_dropdown = page.locator("xpath=//label[contains(text(),'Factors')]/following::div[contains(@class,'dropdown') or contains(@class,'select')][1]").first
                await factors_dropdown.click(timeout=10000)
                await asyncio.sleep(1)
                await page.get_by_text("Lack of knowledge", exact=False).first.click(timeout=5000)
                await asyncio.sleep(1)
                
                # ضغط زر Add الخاص بالعوامل
                add_factor_btn = page.locator("xpath=//label[contains(text(),'Factors')]/following::button[contains(text(),'Add')][1]").first
                await add_factor_btn.click(timeout=5000)
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Factors error: {e}")
            
            # 13. Reporter Name
            logger.info("Filling Reporter Name")
            try:
                reporter_input = page.locator("xpath=//label[contains(text(),'Reporter Name')]/following::input[1]").first
                await reporter_input.fill(FIXED_DATA["reporter"], timeout=10000)
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Reporter error: {e}")
            
            # 14. Email
            logger.info("Filling Email")
            try:
                email_input = page.locator("xpath=//label[contains(text(),'Email')]/following::input[1]").first
                await email_input.fill(FIXED_DATA["email"], timeout=10000)
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Email error: {e}")
            
            # 15. Mobile
            logger.info("Filling Mobile")
            try:
                mobile_input = page.locator("xpath=//label[contains(text(),'Mobile')]/following::input[1]").first
                await mobile_input.fill(FIXED_DATA["mobile"], timeout=10000)
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Mobile error: {e}")
            
            # 16. Staff Category → Pharmacist
            logger.info("Selecting Staff Category: Pharmacist")
            try:
                staff_dropdown = page.locator("xpath=//label[contains(text(),'Staff Category')]/following::div[1]").first
                await staff_dropdown.click(timeout=10000)
                await asyncio.sleep(1)
                await page.get_by_text("Pharmacist", exact=True).first.click(timeout=5000)
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Staff error: {e}")
            
            # التقاط screenshot قبل الإرسال للتحقق
            screenshot_path = "/tmp/form_preview.png"
            await page.screenshot(path=screenshot_path, full_page=True)
            result["screenshot_path"] = screenshot_path
            
            await asyncio.sleep(2)
            
            # 17. Submit
            logger.info("Clicking Submit...")
            try:
                submit_btn = page.get_by_text("Submit form", exact=False).first
                await submit_btn.click(timeout=10000)
                await asyncio.sleep(5)
                result["success"] = True
            except Exception as e:
                logger.error(f"Submit error: {e}")
                result["error"] = f"Submit failed: {str(e)}"
            
            await browser.close()
            return result
            
        except Exception as e:
            logger.error(f"Playwright fatal error: {e}")
            result["error"] = str(e)
            try:
                await browser.close()
            except:
                pass
            return result


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message.photo and not message.document:
        await message.reply_text("أرسل صورة الوصفة مع الكلمة المفتاحية في caption")
        return
    
    keyword = message.caption or ""
    if not keyword:
        await message.reply_text("⚠️ اكتب الكلمة المفتاحية في caption الصورة")
        return
    
    await message.reply_text("⏳ جاري المعالجة... (قد تأخذ دقيقتين)")
    
    if message.photo:
        file = await context.bot.get_file(message.photo[-1].file_id)
    else:
        file = await context.bot.get_file(message.document.file_id)
    
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        image_path = tmp.name
    
    try:
        # استخراج البيانات من الصورة
        extracted = extract_from_image(image_path)
        
        if not extracted["mrn"] or not extracted["date"]:
            await message.reply_text(
                f"⚠️ ما قدرت أقرأ البيانات كاملة\n"
                f"MRN: {extracted['mrn']}\n"
                f"Date: {extracted['date']}"
            )
            return
        
        case = get_case_details(keyword)
        
        # دمج البيانات
        full_data = {
            "mrn": extracted["mrn"],
            "date": extracted["date"],
            "gender": extracted["gender"] or "Male",
            "diagnosis": extracted["diagnosis"],
            "medication": case["medication"],
            "description": case["description"],
            "prescription_text": case["prescription_text"],
        }
        
        await message.reply_text(
            f"📋 البيانات المستخرجة:\n"
            f"MRN: {full_data['mrn']}\n"
            f"Date: {full_data['date']}\n"
            f"Gender: {full_data['gender']}\n"
            f"Diagnosis: {full_data['diagnosis']}\n"
            f"Medication: {full_data['medication']}\n\n"
            f"جاري ملء النموذج..."
        )
        
        # ملء النموذج
        result = await fill_form_playwright(full_data)
        
        # إرسال screenshot للتأكد
        if result.get("screenshot_path") and os.path.exists(result["screenshot_path"]):
            try:
                with open(result["screenshot_path"], "rb") as f:
                    await message.reply_photo(
                        photo=f,
                        caption="📸 لقطة قبل الإرسال"
                    )
            except Exception as e:
                logger.error(f"Screenshot send error: {e}")
        
        if result["success"]:
            await message.reply_text(
                f"Done ✔️\n\n"
                f"MRN: {full_data['mrn']}\n"
                f"Date: {full_data['date']}\n"
                f"Keyword: {keyword}\n\n"
                f"⚠️ تحقق من الموقع للتأكد من نجاح الإرسال"
            )
        else:
            await message.reply_text(
                f"⚠️ حدث خطأ: {result.get('error', 'Unknown')}\n\n"
                f"البيانات جاهزة للنسخ اليدوي:\n"
                f"MRN: {full_data['mrn']}\n"
                f"Date: {full_data['date']}\n"
                f"Gender: {full_data['gender']}\n"
                f"Diagnosis: {full_data['diagnosis']}"
            )
    
    except Exception as e:
        logger.error(f"Main error: {e}")
        await message.reply_text(f"❌ خطأ: {str(e)}")
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
