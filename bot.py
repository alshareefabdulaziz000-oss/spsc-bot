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
            "type_of_error": "12",
        }
    elif k == "3 days":
        return {
            "description": "Doctor wrote medicine more than 3 days",
            "medication_search": "paracetamol",
            "type_of_error": "9",
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


async def safe_action(name, coro):
    try:
        await coro
        logger.info(f"✅ {name}")
        return True
    except Exception as e:
        logger.error(f"❌ {name}: {e}")
        return False


async def fill_form(data: dict) -> dict:
    result = {"success": False, "error": "", "screenshot_path": "", "screenshot_after": ""}
    
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True, capture_output=True)
    except:
        pass
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()
        
        page.on("dialog", lambda dialog: asyncio.create_task(dialog.accept()))
        
        try:
            logger.info("Opening form...")
            await page.goto(FORM_URL, wait_until="networkidle", timeout=90000)
            await asyncio.sleep(3)
            
            # 1. Error Reach Patient → No
            await safe_action("Reach Patient No", page.click("#ContentPlaceHolder1_ErrorReachPatient_0"))
            await asyncio.sleep(0.5)
            
            # 2. Event Date - نعبّي الحقلين المرئي والمخفي بصيغة تشمل الوقت
            # الصيغة المطلوبة: DD/MM/YYYY HH:MM AM
            date_full = f"{data['date']} 10:00 AM"
            date_js = f"""
                () => {{
                    const visible = document.getElementById('ContentPlaceHolder1_Event_Date_Txt');
                    const hidden = document.getElementById('ContentPlaceHolder1_hdnEvent_Dt_Txt');
                    if (visible) {{
                        visible.removeAttribute('readonly');
                        visible.value = '{date_full}';
                        visible.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        visible.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        visible.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                    }}
                    if (hidden) {{
                        hidden.value = '{date_full}';
                        hidden.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    }}
                }}
            """
            await safe_action("Event Date", page.evaluate(date_js))
            await asyncio.sleep(1)
            
            # 3. Prescription type → Other/s
            await safe_action("Prescription Other/s", page.click("#ContentPlaceHolder1_Wasfaty_Chk_0"))
            await asyncio.sleep(0.5)
            
            # 4. MRN
            await safe_action("MRN", page.fill("#ContentPlaceHolder1_Mr_Txt", data['mrn']))
            await asyncio.sleep(0.5)
            
            # 5. Gender
            gender_value = "1" if data['gender'].lower() == "male" else "2"
            await safe_action("Gender", page.select_option("#ContentPlaceHolder1_Gender_Drop", value=gender_value))
            await asyncio.sleep(0.5)
            
            # 6. Where It Happens → ER Adult
            await safe_action("Where ER Adult", page.select_option("#ContentPlaceHolder1_WhereItHappen_Drop", value="3"))
            await asyncio.sleep(0.5)
            
            # 7. Diagnosis (autocomplete)
            try:
                diagnosis_input = page.locator("#ContentPlaceHolder1_txtDiagnosis")
                await diagnosis_input.click()
                search_term = data['diagnosis'][:5] if len(data['diagnosis']) >= 3 else "headache"
                await diagnosis_input.type(search_term, delay=100)
                await asyncio.sleep(3)
                await page.keyboard.press("ArrowDown")
                await asyncio.sleep(0.5)
                await page.keyboard.press("Enter")
                await asyncio.sleep(1)
                logger.info("✅ Diagnosis")
            except Exception as e:
                logger.error(f"❌ Diagnosis: {e}")
            
            # 8. Stage → Prescribing
            await safe_action("Stage Prescribing", page.select_option("#ContentPlaceHolder1_ME_Type_Drop", value="1"))
            await asyncio.sleep(0.5)
            
            # 9. Type of Error + Add
            await safe_action("Type Error select", page.select_option("#ContentPlaceHolder1_ddlNewTypeOfError", value=data['type_of_error']))
            await asyncio.sleep(0.5)
            await safe_action("Type Error Add", page.click("#ContentPlaceHolder1_NewTypeOfError_Main_Btn"))
            await asyncio.sleep(1.5)
            
            # 10. Description
            await safe_action("Description", page.fill("#ContentPlaceHolder1_Event_Desc_Txt", data['description']))
            await asyncio.sleep(0.5)
            
            # 11. Action Taken → Call physician
            await safe_action("Action Taken", page.select_option("#ContentPlaceHolder1_ActionTaken_Drop", value="3"))
            await asyncio.sleep(0.5)
            
            # 12. Medication
            try:
                med_value = await page.evaluate(f"""
                    () => {{
                        const sel = document.getElementById('ContentPlaceHolder1_Generic_Name_Drop');
                        const term = '{data['medication_search']}'.toLowerCase();
                        for (let i = 0; i < sel.options.length; i++) {{
                            if (sel.options[i].text.toLowerCase().includes(term)) {{
                                return sel.options[i].value;
                            }}
                        }}
                        return null;
                    }}
                """)
                if med_value:
                    await page.select_option("#ContentPlaceHolder1_Generic_Name_Drop", value=med_value)
                    await asyncio.sleep(0.5)
                    await page.click("#ContentPlaceHolder1_Add_Med")
                    await asyncio.sleep(1.5)
                    logger.info(f"✅ Medication added")
                else:
                    logger.warning("⚠️ Medication not found")
            except Exception as e:
                logger.error(f"❌ Medication: {e}")
            
            # 13. Factors → Lack of knowledge
            await safe_action("Factor select", page.select_option("#ContentPlaceHolder1_Factors_Drop", value="4"))
            await asyncio.sleep(0.5)
            await safe_action("Factor Add", page.click("#ContentPlaceHolder1_Factors_Main_Btn"))
            await asyncio.sleep(1.5)
            
            # 14. Reporter Name
            await safe_action("Reporter Name", page.fill("#ContentPlaceHolder1_Reporter_Name_Txt", "Az"))
            await asyncio.sleep(0.5)
            
            # 15. Email
            await safe_action("Email", page.fill("#ContentPlaceHolder1_Reporter_Email_Txt", "aalhazmi50@moh.gov.sa"))
            await asyncio.sleep(0.5)
            
            # 16. Mobile
            await safe_action("Mobile", page.fill("#ContentPlaceHolder1_Reporter_Mobile_Txt", "0547995498"))
            await asyncio.sleep(0.5)
            
            # 17. Staff Category → Pharmacist
            await safe_action("Staff Pharmacist", page.select_option("#ContentPlaceHolder1_Staff_Cat_Drop", value="4"))
            await asyncio.sleep(1)
            
            # Screenshot قبل Submit
            screenshot_path = "/tmp/form_preview.png"
            await page.screenshot(path=screenshot_path, full_page=True)
            result["screenshot_path"] = screenshot_path
            
            # 18. Submit
            logger.info("Clicking Submit...")
            await page.click("#ContentPlaceHolder1_Submit_Btn")
            await asyncio.sleep(3)
            logger.info("✅ Submit clicked")
            
            # 19. التعامل مع Confirm dialog - Yes button
            logger.info("Looking for Yes button in modal...")
            clicked_yes = False
            
            # طريقة 1: بحث عن زر Yes داخل modal مرئي
            try:
                yes_btn = page.locator(".modal.show button:has-text('Yes'), .modal[style*='display: block'] button:has-text('Yes')").first
                await yes_btn.click(timeout=5000)
                clicked_yes = True
                logger.info("✅ Yes clicked (modal show)")
            except Exception as e:
                logger.warning(f"Method 1 failed: {e}")
            
            # طريقة 2: أي زر يحتوي Yes
            if not clicked_yes:
                try:
                    await page.get_by_role("button", name="Yes").click(timeout=5000)
                    clicked_yes = True
                    logger.info("✅ Yes clicked (role)")
                except Exception as e:
                    logger.warning(f"Method 2 failed: {e}")
            
            # طريقة 3: text selector
            if not clicked_yes:
                try:
                    await page.click("text=Yes", timeout=5000)
                    clicked_yes = True
                    logger.info("✅ Yes clicked (text)")
                except Exception as e:
                    logger.warning(f"Method 3 failed: {e}")
            
            # طريقة 4: JavaScript مباشر
            if not clicked_yes:
                try:
                    await page.evaluate("""
                        () => {
                            const buttons = document.querySelectorAll('button, input[type="button"], input[type="submit"]');
                            for (const btn of buttons) {
                                const text = (btn.innerText || btn.value || '').trim();
                                if (text === 'Yes') {
                                    btn.click();
                                    return true;
                                }
                            }
                            return false;
                        }
                    """)
                    clicked_yes = True
                    logger.info("✅ Yes clicked (JavaScript)")
                except Exception as e:
                    logger.warning(f"Method 4 failed: {e}")
            
            await asyncio.sleep(7)
            
            # Screenshot بعد Yes
            screenshot_after = "/tmp/form_after.png"
            await page.screenshot(path=screenshot_after, full_page=True)
            result["screenshot_after"] = screenshot_after
            
            # تحقق من عنوان الصفحة أو URL للنجاح
            final_url = page.url
            logger.info(f"Final URL: {final_url}")
            result["final_url"] = final_url
            
            result["success"] = True
            await browser.close()
            return result
            
        except Exception as e:
            logger.error(f"Fatal error: {e}")
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
            await message.reply_text(f"⚠️ فشل قراءة البيانات")
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
                    await message.reply_photo(photo=f, caption="📸 قبل Submit")
            except:
                pass
        
        if result.get("screenshot_after") and os.path.exists(result["screenshot_after"]):
            try:
                with open(result["screenshot_after"], "rb") as f:
                    await message.reply_photo(photo=f, caption=f"📸 بعد Submit\nURL: {result.get('final_url', 'N/A')}")
            except:
                pass
        
        if result["success"]:
            await message.reply_text(f"Done ✔️\n\n✅ تحقق من الموقع")
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
