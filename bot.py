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
    
    prompt = """انت مساعد دقيق. من صورة الوصفة الطبية استخرج:
1. MRN: رقم المريض
2. DATE: التاريخ (DD/MM/YYYY)
3. GENDER: Male أو Female
4. DIAGNOSIS: من قسم Indication (EMPTY إذا فاضي)

أجب:
MRN: xxxxx
DATE: DD/MM/YYYY
GENDER: Male
DIAGNOSIS: xxxxx"""
    
    response = model.generate_content([prompt, {"mime_type": "image/jpeg", "data": image_data}])
    text = response.text.strip()
    result = {"mrn": "", "date": "", "gender": "Male", "diagnosis": ""}
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("MRN:"): result["mrn"] = line.split("MRN:")[1].strip()
        elif line.startswith("DATE:"): result["date"] = line.split("DATE:")[1].strip()
        elif line.startswith("GENDER:"): result["gender"] = line.split("GENDER:")[1].strip()
        elif line.startswith("DIAGNOSIS:"): result["diagnosis"] = line.split("DIAGNOSIS:")[1].strip()
    if not result["diagnosis"] or result["diagnosis"].upper() == "EMPTY":
        result["diagnosis"] = "headache"
    return result


def get_case_details(keyword: str) -> dict:
    k = keyword.lower().strip()
    if k == "omeprazole":
        return {"description": "Doctor write medicine out of privilege", "medication_search": "omeprazole", "type_of_error": "12"}
    elif k == "3 days":
        return {"description": "Doctor wrote medicine more than 3 days", "medication_search": "paracetamol", "type_of_error": "9"}
    elif k == "no diagnosis":
        return {"description": "Didn't write the diagnosis", "medication_search": "paracetamol", "type_of_error": "12"}
    else:
        return {"description": keyword, "medication_search": keyword, "type_of_error": "1"}


async def verify_field(page, field_id: str, field_name: str) -> bool:
    try:
        actual = await page.evaluate(f"""() => document.getElementById('{field_id}')?.value || ''""")
        if str(actual).strip() != '':
            logger.info(f"✅ VERIFIED {field_name}: '{actual}'")
            return True
        else:
            logger.warning(f"⚠️ EMPTY {field_name}")
            return False
    except Exception as e:
        logger.error(f"❌ Verify {field_name}: {e}")
        return False


async def fill_field_robust(page, field_id: str, value: str, field_name: str, max_retries: int = 3):
    for attempt in range(max_retries):
        try:
            await page.wait_for_selector(f"#{field_id}", state="attached", timeout=10000)
            await page.click(f"#{field_id}", timeout=5000)
            await asyncio.sleep(0.3)
            await page.fill(f"#{field_id}", "", timeout=5000)
            await page.fill(f"#{field_id}", str(value), timeout=5000)
            await asyncio.sleep(0.5)
            
            await page.evaluate(f"""
                () => {{
                    const el = document.getElementById('{field_id}');
                    if (el) {{
                        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        el.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                    }}
                }}
            """)
            await asyncio.sleep(0.3)
            
            if await verify_field(page, field_id, field_name):
                return True
            
            logger.warning(f"Retry {attempt + 1} for {field_name}")
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Attempt {attempt + 1} failed for {field_name}: {e}")
            await asyncio.sleep(1)
    
    return False


async def select_dropdown_robust(page, field_id: str, value: str, field_name: str, max_retries: int = 3):
    for attempt in range(max_retries):
        try:
            await page.wait_for_selector(f"#{field_id}", state="attached", timeout=10000)
            await page.select_option(f"#{field_id}", value=str(value), timeout=5000)
            await asyncio.sleep(0.5)
            
            await page.evaluate(f"""
                () => {{
                    const el = document.getElementById('{field_id}');
                    if (el) el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
            """)
            await asyncio.sleep(0.3)
            
            actual = await page.evaluate(f"""() => document.getElementById('{field_id}')?.value || ''""")
            if actual == str(value):
                logger.info(f"✅ VERIFIED dropdown {field_name}: '{actual}'")
                return True
            
            logger.warning(f"Retry {attempt + 1} for dropdown {field_name}")
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Attempt {attempt + 1} failed for {field_name}: {e}")
            await asyncio.sleep(1)
    
    return False


async def fill_form(data: dict) -> dict:
    result = {"success": False, "error": "", "screenshot_path": "", "screenshot_after": "", "field_status": {}}
    
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
            logger.info("🌐 Opening form...")
            await page.goto(FORM_URL, wait_until="networkidle", timeout=90000)
            await asyncio.sleep(5)
            
            status = {}
            
            # 1. Reach Patient → No
            try:
                await page.click("#ContentPlaceHolder1_ErrorReachPatient_0", timeout=10000)
                await asyncio.sleep(0.5)
                checked = await page.evaluate("() => document.getElementById('ContentPlaceHolder1_ErrorReachPatient_0')?.checked")
                status["reach_no"] = checked
                logger.info(f"✅ Reach No: {checked}")
            except Exception as e:
                logger.error(f"❌ Reach: {e}")
                status["reach_no"] = False
            
            # 2. Event Date - بالكيبورد مباشرة
            logger.info("📅 Setting Event Date with keyboard...")
            date_success = False
            
            try:
                # إزالة readonly أولاً
                await page.evaluate("""
                    () => {
                        const el = document.getElementById('ContentPlaceHolder1_Event_Date_Txt');
                        if (el) el.removeAttribute('readonly');
                    }
                """)
                await asyncio.sleep(0.5)
                
                # ننقر الحقل
                await page.click("#ContentPlaceHolder1_Event_Date_Txt", timeout=5000)
                await asyncio.sleep(0.5)
                
                # نغلق أي picker مفتوح
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.3)
                
                # نركّز ونكتب
                await page.focus("#ContentPlaceHolder1_Event_Date_Txt")
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Delete")
                await asyncio.sleep(0.3)
                
                date_str = f"{data['date']} 10:00 AM"
                await page.keyboard.type(date_str, delay=50)
                await asyncio.sleep(0.5)
                await page.keyboard.press("Tab")
                await asyncio.sleep(1)
                
                val = await page.evaluate("() => document.getElementById('ContentPlaceHolder1_Event_Date_Txt')?.value")
                if val and val.strip():
                    date_success = True
                    logger.info(f"✅ Date (keyboard): {val}")
                else:
                    # محاولة أخيرة: JS مع hidden field
                    await page.evaluate(f"""
                        () => {{
                            const el = document.getElementById('ContentPlaceHolder1_Event_Date_Txt');
                            const hidden = document.getElementById('ContentPlaceHolder1_hdnEvent_Dt_Txt');
                            if (el) {{
                                el.value = '{data['date']} 10:00 AM';
                                el.setAttribute('value', '{data['date']} 10:00 AM');
                            }}
                            if (hidden) {{
                                hidden.value = '{data['date']} 10:00 AM';
                                hidden.setAttribute('value', '{data['date']} 10:00 AM');
                            }}
                        }}
                    """)
                    await asyncio.sleep(0.5)
                    val2 = await page.evaluate("() => document.getElementById('ContentPlaceHolder1_Event_Date_Txt')?.value")
                    if val2 and val2.strip():
                        date_success = True
                        logger.info(f"✅ Date (final JS): {val2}")
            except Exception as e:
                logger.error(f"❌ Date: {e}")
            
            status["date"] = date_success
            
            # 3. Prescription Other/s
            try:
                await page.click("#ContentPlaceHolder1_Wasfaty_Chk_0", timeout=5000)
                await asyncio.sleep(0.5)
                status["prescription"] = True
                logger.info("✅ Prescription Other/s")
            except Exception as e:
                logger.error(f"❌ Prescription: {e}")
                status["prescription"] = False
            
            # 4. Stage → Prescribing
            status["stage"] = await select_dropdown_robust(page, "ContentPlaceHolder1_ME_Type_Drop", "1", "Stage")
            
            # 5. Type of Error + Add
            status["type_select"] = await select_dropdown_robust(page, "ContentPlaceHolder1_ddlNewTypeOfError", data['type_of_error'], "Type of Error")
            try:
                await page.click("#ContentPlaceHolder1_NewTypeOfError_Main_Btn", timeout=5000)
                await asyncio.sleep(3)
                status["type_add"] = True
                logger.info("✅ Type Add (postback)")
            except Exception as e:
                logger.error(f"❌ Type Add: {e}")
                status["type_add"] = False
            
            # 6. Description
            status["description"] = await fill_field_robust(page, "ContentPlaceHolder1_Event_Desc_Txt", data['description'], "Description")
            
            # 7. Diagnosis
            try:
                await page.click("#ContentPlaceHolder1_txtDiagnosis", timeout=5000)
                await asyncio.sleep(0.5)
                search_term = data['diagnosis'][:5] if len(data['diagnosis']) >= 3 else "headache"
                await page.type("#ContentPlaceHolder1_txtDiagnosis", search_term, delay=100)
                await asyncio.sleep(3)
                await page.keyboard.press("ArrowDown")
                await asyncio.sleep(0.5)
                await page.keyboard.press("Enter")
                await asyncio.sleep(1)
                val = await page.evaluate("() => document.getElementById('ContentPlaceHolder1_txtDiagnosis')?.value")
                status["diagnosis"] = bool(val and val.strip())
                logger.info(f"✅ Diagnosis: {val}")
            except Exception as e:
                logger.error(f"❌ Diagnosis: {e}")
                status["diagnosis"] = False
            
            # 8. Action Taken
            status["action"] = await select_dropdown_robust(page, "ContentPlaceHolder1_ActionTaken_Drop", "3", "Action Taken")
            
            # 9. Medication + Add
            try:
                med_value = await page.evaluate(f"""
                    () => {{
                        const sel = document.getElementById('ContentPlaceHolder1_Generic_Name_Drop');
                        if (!sel) return null;
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
                    await page.click("#ContentPlaceHolder1_Add_Med", timeout=5000)
                    await asyncio.sleep(3)
                    status["medication"] = True
                    logger.info(f"✅ Medication Add (postback)")
                else:
                    status["medication"] = False
            except Exception as e:
                logger.error(f"❌ Medication: {e}")
                status["medication"] = False
            
            # 10. Factor + Add
            try:
                await page.select_option("#ContentPlaceHolder1_Factors_Drop", value="4", timeout=5000)
                await asyncio.sleep(0.5)
                await page.click("#ContentPlaceHolder1_Factors_Main_Btn", timeout=5000)
                await asyncio.sleep(3)
                status["factor"] = True
                logger.info("✅ Factor Add (postback)")
            except Exception as e:
                logger.error(f"❌ Factor: {e}")
                status["factor"] = False
            
            # 11. MRN
            status["mrn"] = await fill_field_robust(page, "ContentPlaceHolder1_Mr_Txt", data['mrn'], "MRN")
            
            # 12. Gender
            gender_value = "1" if data['gender'].lower() == "male" else "2"
            status["gender"] = await select_dropdown_robust(page, "ContentPlaceHolder1_Gender_Drop", gender_value, "Gender")
            
            # 13. Where It Happens
            status["where"] = await select_dropdown_robust(page, "ContentPlaceHolder1_WhereItHappen_Drop", "3", "Where It Happens")
            
            # 14. Reporter Name
            status["reporter"] = await fill_field_robust(page, "ContentPlaceHolder1_Reporter_Name_Txt", "Az", "Reporter Name")
            
            # 15. Email
            status["email"] = await fill_field_robust(page, "ContentPlaceHolder1_Reporter_Email_Txt", "aalhazmi50@moh.gov.sa", "Email")
            
            # 16. Mobile
            status["mobile"] = await fill_field_robust(page, "ContentPlaceHolder1_Reporter_Mobile_Txt", "0547995498", "Mobile")
            
            # 17. Staff Category → Pharmacist (value=2)
            status["staff"] = await select_dropdown_robust(page, "ContentPlaceHolder1_Staff_Cat_Drop", "2", "Staff Category")
            
            result["field_status"] = status
            
            logger.info("📊 Final field status:")
            for k, v in status.items():
                logger.info(f"   {k}: {'✅' if v else '❌'}")
            
            # Screenshot قبل Submit
            screenshot_path = "/tmp/form_preview.png"
            await page.screenshot(path=screenshot_path, full_page=True)
            result["screenshot_path"] = screenshot_path
            
            # Submit
            logger.info("🚀 Clicking Submit...")
            try:
                await page.click("#ContentPlaceHolder1_Submit_Btn", timeout=10000)
                await asyncio.sleep(3)
            except Exception as e:
                logger.error(f"Submit: {e}")
            
            # Yes in modal
            logger.info("👆 Clicking Yes in modal...")
            clicked_yes = False
            for method_name, selector in [
                ("input value", "input[value='Yes'][data-dismiss='modal']"),
                ("button text", "button:has-text('Yes')"),
                ("text selector", "text=Yes"),
            ]:
                if clicked_yes:
                    break
                try:
                    await page.click(selector, timeout=3000)
                    clicked_yes = True
                    logger.info(f"✅ Yes clicked ({method_name})")
                except Exception as e:
                    logger.warning(f"{method_name}: {e}")
            
            if not clicked_yes:
                try:
                    await page.evaluate("""
                        () => {
                            const btns = document.querySelectorAll('input[value="Yes"], button');
                            for (const b of btns) {
                                if ((b.value === 'Yes' || b.innerText?.trim() === 'Yes') && b.offsetParent !== null) {
                                    b.click();
                                    return true;
                                }
                            }
                        }
                    """)
                    logger.info("✅ Yes clicked (JS fallback)")
                except:
                    pass
            
            await asyncio.sleep(10)
            
            screenshot_after = "/tmp/form_after.png"
            await page.screenshot(path=screenshot_after, full_page=True)
            result["screenshot_after"] = screenshot_after
            
            final_url = page.url
            result["final_url"] = final_url
            logger.info(f"🌐 Final URL: {final_url}")
            
            result["success"] = True
            await browser.close()
            return result
            
        except Exception as e:
            logger.error(f"💥 Fatal: {e}")
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
        await message.reply_text("أرسل صورة مع caption")
        return
    keyword = message.caption or ""
    if not keyword:
        await message.reply_text("⚠️ اكتب الكلمة المفتاحية")
        return
    
    await message.reply_text("⏳ جاري المعالجة... (2-3 دقائق)")
    
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
            await message.reply_text("⚠️ فشل قراءة البيانات")
            return
        
        case = get_case_details(keyword)
        full_data = {**extracted, **case}
        
        await message.reply_text(
            f"📋 {full_data['mrn']} | {full_data['date']} | {full_data['gender']}\n"
            f"Dx: {full_data['diagnosis']}\n\n⏳ ملء النموذج..."
        )
        
        result = await fill_form(full_data)
        
        if result.get("field_status"):
            status = result["field_status"]
            report = "📊 تقرير الحقول:\n"
            for k, v in status.items():
                report += f"{'✅' if v else '❌'} {k}\n"
            await message.reply_text(report)
        
        if result.get("screenshot_path") and os.path.exists(result["screenshot_path"]):
            with open(result["screenshot_path"], "rb") as f:
                await message.reply_photo(photo=f, caption="📸 قبل Submit")
        
        if result.get("screenshot_after") and os.path.exists(result["screenshot_after"]):
            with open(result["screenshot_after"], "rb") as f:
                await message.reply_photo(photo=f, caption=f"📸 بعد Submit\n{result.get('final_url', '')}")
        
        if result["success"]:
            await message.reply_text("Done ✔️ تحقق من الموقع")
        else:
            await message.reply_text(f"⚠️ {result.get('error', '')}")
    except Exception as e:
        logger.error(f"Error: {e}")
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
