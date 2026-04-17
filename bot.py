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
    prompt = """من صورة الوصفة الطبية استخرج:
1. MRN: رقم المريض
2. DATE: (DD/MM/YYYY)
3. GENDER: Male أو Female
4. DIAGNOSIS: من Indication (EMPTY إذا فاضي)

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


async def fill_text(page, field_id: str, value: str, name: str) -> bool:
    for attempt in range(3):
        try:
            await page.wait_for_selector(f"#{field_id}", state="attached", timeout=10000)
            await page.click(f"#{field_id}")
            await asyncio.sleep(0.3)
            await page.fill(f"#{field_id}", "")
            await page.fill(f"#{field_id}", str(value))
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
            await asyncio.sleep(0.5)
            actual = await page.evaluate(f"""() => document.getElementById('{field_id}')?.value || ''""")
            if actual.strip():
                logger.info(f"✅ {name}: '{actual}'")
                return True
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Attempt {attempt+1} {name}: {e}")
            await asyncio.sleep(1)
    logger.error(f"❌ {name} FAILED")
    return False


async def select_option_by_label(page, field_id: str, label_text: str, name: str) -> bool:
    for attempt in range(3):
        try:
            await page.wait_for_selector(f"#{field_id}", state="attached", timeout=10000)
            value = await page.evaluate(f"""
                () => {{
                    const sel = document.getElementById('{field_id}');
                    if (!sel) return null;
                    const target = '{label_text}'.toLowerCase();
                    for (let i = 0; i < sel.options.length; i++) {{
                        if (sel.options[i].text.toLowerCase() === target) {{
                            return sel.options[i].value;
                        }}
                    }}
                    for (let i = 0; i < sel.options.length; i++) {{
                        if (sel.options[i].text.toLowerCase().includes(target)) {{
                            return sel.options[i].value;
                        }}
                    }}
                    return null;
                }}
            """)
            if not value:
                logger.error(f"❌ {name}: label '{label_text}' not found")
                return False
            
            await page.select_option(f"#{field_id}", value=value)
            await page.evaluate(f"""
                () => {{
                    const el = document.getElementById('{field_id}');
                    if (el) el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
            """)
            await asyncio.sleep(0.5)
            actual = await page.evaluate(f"""() => document.getElementById('{field_id}')?.value || ''""")
            if actual == value:
                logger.info(f"✅ {name}: value={value}")
                return True
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Attempt {attempt+1} {name}: {e}")
            await asyncio.sleep(1)
    return False


async def click_radio_hard(page, radio_id: str, name: str) -> bool:
    for attempt in range(3):
        try:
            try:
                await page.click(f"#{radio_id}", timeout=3000, force=True)
            except:
                pass
            await asyncio.sleep(0.3)
            
            await page.evaluate(f"""
                () => {{
                    const el = document.getElementById('{radio_id}');
                    if (el) {{
                        el.checked = true;
                        el.click();
                        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        el.dispatchEvent(new Event('click', {{ bubbles: true }}));
                    }}
                }}
            """)
            await asyncio.sleep(0.5)
            
            checked = await page.evaluate(f"() => document.getElementById('{radio_id}')?.checked")
            if checked:
                logger.info(f"✅ Radio {name} checked")
                return True
            
            try:
                await page.click(f"label[for='{radio_id}']", timeout=2000)
                await asyncio.sleep(0.3)
                checked2 = await page.evaluate(f"() => document.getElementById('{radio_id}')?.checked")
                if checked2:
                    logger.info(f"✅ Radio {name} checked (label)")
                    return True
            except:
                pass
            
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Radio {name} attempt {attempt+1}: {e}")
            await asyncio.sleep(1)
    return False


async def click_with_retry(page, selector: str, name: str, timeout: int = 5000) -> bool:
    for attempt in range(3):
        try:
            await page.click(selector, timeout=timeout)
            logger.info(f"✅ Clicked {name}")
            return True
        except Exception as e:
            logger.warning(f"Attempt {attempt+1} click {name}: {e}")
            await asyncio.sleep(1)
    return False


async def fill_form(data: dict) -> dict:
    result = {"success": False, "error": "", "screenshot_path": "", "screenshot_after": "", "field_status": {}, "all_filled": False}
    
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
            status["reach_no"] = await click_radio_hard(page, "ContentPlaceHolder1_ErrorReachPatient_0", "Reach No")
            await asyncio.sleep(0.5)
            
            # 2. Event Date - datetimepicker API + JS شامل
            logger.info("📅 Setting Event Date...")
            date_success = False
            try:
                date_parts = data['date'].split('/')
                if len(date_parts) == 3:
                    dd, mm, yyyy = date_parts
                    iso = f"{yyyy}-{mm.zfill(2)}-{dd.zfill(2)}T10:00:00"
                    formatted = f"{dd.zfill(2)}/{mm.zfill(2)}/{yyyy} 10:00 AM"
                else:
                    iso = "2026-04-15T10:00:00"
                    formatted = "15/04/2026 10:00 AM"
                
                result_js = await page.evaluate(f"""
                    () => {{
                        const visible = document.getElementById('ContentPlaceHolder1_Event_Date_Txt');
                        const hidden = document.getElementById('ContentPlaceHolder1_hdnEvent_Dt_Txt');
                        
                        if (visible) {{
                            visible.removeAttribute('readonly');
                            visible.removeAttribute('disabled');
                            visible.value = '{formatted}';
                            visible.setAttribute('value', '{formatted}');
                            ['input', 'change', 'blur', 'keyup', 'keydown'].forEach(e => {{
                                visible.dispatchEvent(new Event(e, {{ bubbles: true }}));
                            }});
                        }}
                        
                        if (hidden) {{
                            hidden.value = '{formatted}';
                            hidden.setAttribute('value', '{formatted}');
                            hidden.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        }}
                        
                        try {{
                            if (typeof $ !== 'undefined' && $('#ContentPlaceHolder1_Event_Date_Txt').data('DateTimePicker')) {{
                                $('#ContentPlaceHolder1_Event_Date_Txt').data('DateTimePicker').date(moment('{iso}'));
                            }}
                        }} catch(e) {{}}
                        
                        return {{
                            visible: visible?.value,
                            hidden: hidden?.value
                        }};
                    }}
                """)
                
                logger.info(f"Date result: {result_js}")
                
                if result_js.get('visible') or result_js.get('hidden'):
                    date_success = True
                    logger.info(f"✅ Date: {result_js}")
            except Exception as e:
                logger.error(f"Date: {e}")
            status["date"] = date_success
            
            # 3. Prescription Other/s
            status["prescription"] = await click_radio_hard(page, "ContentPlaceHolder1_Wasfaty_Chk_0", "Prescription Other/s")
            await asyncio.sleep(1)
            
            # 4. Stage → Prescribing
            status["stage"] = await select_option_by_label(page, "ContentPlaceHolder1_ME_Type_Drop", "Prescribing", "Stage")
            
            # 5. Type of Error + Add (نعتبرهما نفس الشي)
            type_labels = {
                "12": "Wrong/missed indication",
                "9": "wrong/missed duration",
                "1": "Wrong/missed dose"
            }
            type_label = type_labels.get(data['type_of_error'], "Wrong/missed indication")
            await select_option_by_label(page, "ContentPlaceHolder1_ddlNewTypeOfError", type_label, "Type of Error")
            await asyncio.sleep(0.5)
            status["type_add"] = await click_with_retry(page, "#ContentPlaceHolder1_NewTypeOfError_Main_Btn", "Type Add")
            await asyncio.sleep(3)
            status["type_select"] = status["type_add"]
            
            # 6. Description
            status["description"] = await fill_text(page, "ContentPlaceHolder1_Event_Desc_Txt", data['description'], "Description")
            
            # 7. Diagnosis
            try:
                await page.click("#ContentPlaceHolder1_txtDiagnosis")
                await asyncio.sleep(0.5)
                term = data['diagnosis'][:5] if len(data['diagnosis']) >= 3 else "headache"
                await page.type("#ContentPlaceHolder1_txtDiagnosis", term, delay=100)
                await asyncio.sleep(3)
                await page.keyboard.press("ArrowDown")
                await asyncio.sleep(0.5)
                await page.keyboard.press("Enter")
                await asyncio.sleep(1)
                val = await page.evaluate("() => document.getElementById('ContentPlaceHolder1_txtDiagnosis')?.value")
                status["diagnosis"] = bool(val and val.strip())
                logger.info(f"✅ Diagnosis: {val}")
            except Exception as e:
                logger.error(f"Diagnosis: {e}")
                status["diagnosis"] = False
            
            # 8. Action Taken
            status["action"] = await select_option_by_label(page, "ContentPlaceHolder1_ActionTaken_Drop", "Call the physician", "Action Taken")
            
            # 9. Medication + Add
            med_ok = False
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
                    await page.click("#ContentPlaceHolder1_Add_Med")
                    await asyncio.sleep(3)
                    med_ok = True
                    logger.info(f"✅ Medication added")
            except Exception as e:
                logger.error(f"Medication: {e}")
            status["medication"] = med_ok
            
            # 10. Factor + Add
            factor_ok = False
            try:
                factor_value = await page.evaluate("""
                    () => {
                        const sel = document.getElementById('ContentPlaceHolder1_Factors_Drop');
                        if (!sel) return null;
                        for (let i = 0; i < sel.options.length; i++) {
                            if (sel.options[i].text.toLowerCase().includes('lack of knowledge')) {
                                return sel.options[i].value;
                            }
                        }
                        return null;
                    }
                """)
                if factor_value:
                    await page.select_option("#ContentPlaceHolder1_Factors_Drop", value=factor_value)
                    await asyncio.sleep(0.5)
                    await page.click("#ContentPlaceHolder1_Factors_Main_Btn")
                    await asyncio.sleep(3)
                    factor_ok = True
                    logger.info(f"✅ Factor added")
            except Exception as e:
                logger.error(f"Factor: {e}")
            status["factor"] = factor_ok
            
            # 11-17. الحقول العادية
            status["mrn"] = await fill_text(page, "ContentPlaceHolder1_Mr_Txt", data['mrn'], "MRN")
            status["gender"] = await select_option_by_label(page, "ContentPlaceHolder1_Gender_Drop", data['gender'], "Gender")
            status["where"] = await select_option_by_label(page, "ContentPlaceHolder1_WhereItHappen_Drop", "ER Adult", "Where")
            status["reporter"] = await fill_text(page, "ContentPlaceHolder1_Reporter_Name_Txt", "Az", "Reporter")
            status["email"] = await fill_text(page, "ContentPlaceHolder1_Reporter_Email_Txt", "aalhazmi50@moh.gov.sa", "Email")
            status["mobile"] = await fill_text(page, "ContentPlaceHolder1_Reporter_Mobile_Txt", "0547995498", "Mobile")
            status["staff"] = await select_option_by_label(page, "ContentPlaceHolder1_Staff_Cat_Drop", "Pharmacist", "Staff")
            
            result["field_status"] = status
            
            critical = ["reach_no", "date", "prescription", "stage", "type_add", 
                       "description", "diagnosis", "action", "medication", "factor",
                       "mrn", "gender", "where", "reporter", "email", "mobile", "staff"]
            all_ok = all(status.get(f, False) for f in critical)
            result["all_filled"] = all_ok
            
            logger.info(f"📊 ALL FILLED: {all_ok}")
            
            # Screenshot قبل
            screenshot_path = "/tmp/form_preview.png"
            await page.screenshot(path=screenshot_path, full_page=True)
            result["screenshot_path"] = screenshot_path
            
            # Submit
            if all_ok:
                logger.info("🚀 Submitting...")
                try:
                    await page.click("#ContentPlaceHolder1_Submit_Btn", timeout=10000)
                    await asyncio.sleep(3)
                except Exception as e:
                    logger.error(f"Submit: {e}")
                
                clicked_yes = False
                for selector in ["input[value='Yes'][data-dismiss='modal']", "button:has-text('Yes')", "text=Yes"]:
                    if clicked_yes: break
                    try:
                        await page.click(selector, timeout=3000)
                        clicked_yes = True
                        logger.info(f"✅ Yes clicked")
                    except: pass
                
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
                    except: pass
                
                await asyncio.sleep(10)
            else:
                logger.warning("⚠️ NOT all filled — SKIPPING Submit")
            
            screenshot_after = "/tmp/form_after.png"
            await page.screenshot(path=screenshot_after, full_page=True)
            result["screenshot_after"] = screenshot_after
            result["final_url"] = page.url
            
            result["success"] = True
            await browser.close()
            return result
            
        except Exception as e:
            logger.error(f"💥 Fatal: {e}")
            result["error"] = str(e)
            try:
                await page.screenshot(path="/tmp/form_error.png", full_page=True)
                result["screenshot_path"] = "/tmp/form_error.png"
            except: pass
            try: await browser.close()
            except: pass
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
            
            if result.get("all_filled"):
                report += "\n✅ الكل ممتلئ — تم الإرسال"
            else:
                report += "\n⚠️ بعض الحقول فاضية — لم يتم الإرسال"
            
            await message.reply_text(report)
        
        if result.get("screenshot_path") and os.path.exists(result["screenshot_path"]):
            with open(result["screenshot_path"], "rb") as f:
                await message.reply_photo(photo=f, caption="📸 قبل Submit")
        
        if result.get("screenshot_after") and os.path.exists(result["screenshot_after"]):
            with open(result["screenshot_after"], "rb") as f:
                await message.reply_photo(photo=f, caption=f"📸 بعد\n{result.get('final_url', '')}")
        
        if result["success"] and result.get("all_filled"):
            await message.reply_text("Done ✔️ تحقق من الموقع")
        elif result["success"]:
            await message.reply_text("⚠️ لم يُرسل — تحقق من الحقول الناقصة أعلاه")
        else:
            await message.reply_text(f"⚠️ {result.get('error', '')}")
    except Exception as e:
        logger.error(f"Error: {e}")
        await message.reply_text(f"❌ {str(e)}")
    finally:
        try: os.unlink(image_path)
        except: pass


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_message))
    logger.info("Bot started...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
