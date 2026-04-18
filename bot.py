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
import threading
import google.generativeai as genai
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from playwright.async_api import async_playwright
from http.server import HTTPServer, BaseHTTPRequestHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = re.sub(r'\s+', '', os.environ.get("TELEGRAM_TOKEN", ""))
GEMINI_API_KEY = re.sub(r'\s+', '', os.environ.get("GEMINI_API_KEY", ""))

genai.configure(api_key=GEMINI_API_KEY)

FORM_URL = "https://portal.spsc.gov.sa/MEH/Default.aspx?Id=454"

MEDIA_GROUPS = {}
MEDIA_GROUPS_LOCK = asyncio.Lock()


def extract_from_image(image_path: str, keyword: str = "") -> dict:
    model = genai.GenerativeModel("gemini-flash-latest")
    with open(image_path, "rb") as f:
        image_data = f.read()
    
    k = keyword.lower().strip()
    
    if k == "3 days":
        med_instruction = "MEDICATION: اسم دواء واحد فقط من الوصفة مكتوب لمدة أكثر من 3 أيام. اكتب الاسم العام فقط"
    elif k == "no diagnosis":
        med_instruction = "MEDICATION: اسم دواء واحد فقط من الوصفة. اكتب الاسم العام فقط"
    else:
        med_instruction = "MEDICATION: omeprazole"
    
    prompt = f"""من صورة الوصفة الطبية استخرج البيانات بدقة تامة:

1. MRN: رقم المريض
2. DATE: التاريخ بصيغة DD/MM/YYYY
3. TIME: الوقت بصيغة HH:MM (24 ساعة). اقرأ الوقت من الوصفة. إذا ما وجدت وقت، اكتب 10:00
4. GENDER: Male أو Female
5. DIAGNOSIS: اقرأ خانة Indication من الصورة بدقة
   - إذا كان فيها رقم ICD-10 (مثل N94.6): حوّل الرقم إلى اسم المرض الرسمي
     أمثلة: N94.6 → Dysmenorrhea | J06.9 → Acute upper respiratory infection
     R51 → Headache | K29.7 → Gastritis | M79.3 → Myalgia | R10.4 → Abdominal pain
     J02.9 → Acute pharyngitis | H66.9 → Otitis media | L30.9 → Dermatitis
   - إذا كان فيها اسم مرض مكتوب بالإنجليزي: اكتبه كما هو
   - إذا كانت خانة Indication فاضية أو غير موجودة: اكتب EMPTY
   - ممنوع تختلق أو تخمّن تشخيص غير موجود في الصورة
6. {med_instruction}

أجب بهذا التنسيق بالضبط:
MRN: xxxxx
DATE: DD/MM/YYYY
TIME: HH:MM
GENDER: Male
DIAGNOSIS: اسم المرض أو EMPTY
MEDICATION: xxxxx"""
    
    response = model.generate_content([prompt, {"mime_type": "image/jpeg", "data": image_data}])
    text = response.text.strip()
    result = {"mrn": "", "date": "", "time": "10:00", "gender": "Male", "diagnosis": "", "medication": ""}
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("MRN:"): result["mrn"] = line.split("MRN:")[1].strip()
        elif line.startswith("DATE:"): result["date"] = line.split("DATE:")[1].strip()
        elif line.startswith("TIME:"): result["time"] = line.split("TIME:")[1].strip()
        elif line.startswith("GENDER:"): result["gender"] = line.split("GENDER:")[1].strip()
        elif line.startswith("DIAGNOSIS:"): result["diagnosis"] = line.split("DIAGNOSIS:")[1].strip()
        elif line.startswith("MEDICATION:"): result["medication"] = line.split("MEDICATION:")[1].strip()
    if not result["diagnosis"] or result["diagnosis"].upper() == "EMPTY":
        result["diagnosis"] = "headache"
    if not result["medication"] or result["medication"].upper() == "EMPTY":
        result["medication"] = "paracetamol"
    if not result["time"]:
        result["time"] = "10:00"
    return result


def get_case_details(keyword: str, extracted_medication: str = "") -> dict:
    k = keyword.lower().strip()
    if k == "omeprazole":
        return {"description": "Doctor write medicine out of privilege", "medication_search": "omeprazole", "type_of_error": "12"}
    elif k == "3 days":
        med = extracted_medication if extracted_medication else "paracetamol"
        return {"description": "Doctor wrote medicine more than 3 days", "medication_search": med, "type_of_error": "9"}
    elif k == "no diagnosis":
        med = extracted_medication if extracted_medication else "paracetamol"
        return {"description": "Didn't write the diagnosis", "medication_search": med, "type_of_error": "12"}
    else:
        return {"description": keyword, "medication_search": keyword, "type_of_error": "1"}


async def safe_wait_after_postback(page, seconds=5):
    try:
        await asyncio.sleep(seconds)
        await page.wait_for_load_state("networkidle", timeout=15000)
    except:
        pass
    await asyncio.sleep(1)


async def fill_text(page, field_id: str, value: str, name: str) -> bool:
    for attempt in range(3):
        try:
            await page.wait_for_selector(f"#{field_id}", state="attached", timeout=15000)
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
            logger.error(f"{name}: {e}")
            await asyncio.sleep(1)
    return False


async def fill_text_by_name(page, field_name: str, value: str, name: str) -> bool:
    for attempt in range(3):
        try:
            filled = await page.evaluate(f"""
                () => {{
                    let el = document.getElementById('{field_name}');
                    if (!el) el = document.getElementById('ContentPlaceHolder1_{field_name}');
                    if (!el) el = document.querySelector(`[name="{field_name}"]`);
                    if (!el) el = document.querySelector(`[name*="{field_name}" i]`);
                    if (!el) {{
                        const all = document.querySelectorAll('input[type="text"], textarea');
                        for (const i of all) {{
                            if ((i.id || '').toLowerCase().includes('{field_name.lower()}') ||
                                (i.name || '').toLowerCase().includes('{field_name.lower()}')) {{
                                el = i;
                                break;
                            }}
                        }}
                    }}
                    if (el) {{
                        el.value = '{value}';
                        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        el.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                        return el.id || el.name || 'found';
                    }}
                    return null;
                }}
            """)
            if filled:
                logger.info(f"✅ {name}: '{filled}'")
                return True
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"{name}: {e}")
            await asyncio.sleep(1)
    return False


async def select_option_by_label(page, field_id: str, label_text: str, name: str) -> bool:
    for attempt in range(3):
        try:
            await page.wait_for_selector(f"#{field_id}", state="attached", timeout=15000)
            value = await page.evaluate(f"""
                () => {{
                    const sel = document.getElementById('{field_id}');
                    if (!sel) return null;
                    const target = '{label_text}'.toLowerCase();
                    for (let i = 0; i < sel.options.length; i++) {{
                        if (sel.options[i].text.toLowerCase() === target) return sel.options[i].value;
                    }}
                    for (let i = 0; i < sel.options.length; i++) {{
                        if (sel.options[i].text.toLowerCase().includes(target)) return sel.options[i].value;
                    }}
                    return null;
                }}
            """)
            if not value:
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
                logger.info(f"✅ {name}: {value}")
                return True
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"{name}: {e}")
            await asyncio.sleep(1)
    return False


async def click_radio_hard(page, radio_id: str, name: str) -> bool:
    for attempt in range(3):
        try:
            try:
                await page.click(f"#{radio_id}", timeout=5000, force=True)
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
                logger.info(f"✅ Radio {name}")
                return True
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Radio {name}: {e}")
            await asyncio.sleep(1)
    return False


async def fill_all_simple_fields(page, data):
    logger.info("📝 Filling ALL simple fields...")
    await click_radio_hard(page, "ContentPlaceHolder1_ErrorReachPatient_1", "Reach No")
    await asyncio.sleep(0.3)
    await click_radio_hard(page, "ContentPlaceHolder1_Wasfaty_Chk_1", "Prescription Other/s")
    await asyncio.sleep(0.5)
    await fill_text_by_name(page, "other", "ER", "Other ER")
    await asyncio.sleep(0.3)
    
    # التاريخ والوقت
    date_parts = data['date'].split('/')
    time_str = data.get('time', '10:00')
    try:
        time_parts = time_str.split(':')
        hh = time_parts[0].zfill(2)
        mm_time = time_parts[1].zfill(2) if len(time_parts) > 1 else '00'
        hh_int = int(hh)
        if hh_int == 0:
            hh_12 = '12'
            period = 'AM'
        elif hh_int < 12:
            hh_12 = str(hh_int).zfill(2)
            period = 'AM'
        elif hh_int == 12:
            hh_12 = '12'
            period = 'PM'
        else:
            hh_12 = str(hh_int - 12).zfill(2)
            period = 'PM'
    except:
        hh = '10'
        mm_time = '00'
        hh_12 = '10'
        period = 'AM'
    
    if len(date_parts) == 3:
        dd, mm, yyyy = date_parts
        iso = f"{yyyy}-{mm.zfill(2)}-{dd.zfill(2)}T{hh}:{mm_time}:00"
        formatted = f"{dd.zfill(2)}/{mm.zfill(2)}/{yyyy} {hh_12}:{mm_time} {period}"
    else:
        iso = "2026-04-15T10:00:00"
        formatted = "15/04/2026 10:00 AM"
    
    await page.evaluate(f"""
        () => {{
            const visible = document.getElementById('ContentPlaceHolder1_Event_Date_Txt');
            const hidden = document.getElementById('ContentPlaceHolder1_hdnEvent_Dt_Txt');
            if (visible) {{
                visible.removeAttribute('readonly');
                visible.value = '{formatted}';
                visible.setAttribute('value', '{formatted}');
                ['input', 'change', 'blur'].forEach(e => visible.dispatchEvent(new Event(e, {{ bubbles: true }})));
            }}
            if (hidden) {{
                hidden.value = '{formatted}';
                hidden.dispatchEvent(new Event('change', {{ bubbles: true }}));
            }}
            try {{
                if (typeof $ !== 'undefined' && $('#ContentPlaceHolder1_Event_Date_Txt').data('DateTimePicker')) {{
                    $('#ContentPlaceHolder1_Event_Date_Txt').data('DateTimePicker').date(moment('{iso}'));
                }}
            }} catch(e) {{}}
        }}
    """)
    await asyncio.sleep(0.5)
    
    await fill_text(page, "ContentPlaceHolder1_Mr_Txt", data['mrn'], "MRN")
    await select_option_by_label(page, "ContentPlaceHolder1_Gender_Drop", data['gender'], "Gender")
    await select_option_by_label(page, "ContentPlaceHolder1_WhereItHappen_Drop", "ER Adult", "Where")
    
    # Diagnosis - نكتب الاسم كامل
    try:
        await page.click("#ContentPlaceHolder1_txtDiagnosis")
        await asyncio.sleep(0.5)
        # نكتب التشخيص كامل لضمان الدقة
        term = data['diagnosis'] if data['diagnosis'] else "headache"
        await page.fill("#ContentPlaceHolder1_txtDiagnosis", "")
        await asyncio.sleep(0.3)
        
        for char in term:
            await page.keyboard.type(char, delay=150)
        
        await asyncio.sleep(5)
        
        diagnosis_clicked = await page.evaluate("""
            () => {
                const uiMenu = document.querySelector('.ui-autocomplete:not([style*="display: none"]), .ui-menu:not([style*="display: none"])');
                if (uiMenu) {
                    const firstItem = uiMenu.querySelector('li a, li.ui-menu-item, .ui-menu-item-wrapper');
                    if (firstItem) {
                        firstItem.click();
                        return 'clicked: ui-autocomplete';
                    }
                }
                
                const input = document.getElementById('ContentPlaceHolder1_txtDiagnosis');
                if (input) {
                    const allLists = document.querySelectorAll('ul');
                    for (const ul of allLists) {
                        if (ul.offsetParent !== null && ul.children.length > 0) {
                            const rect = ul.getBoundingClientRect();
                            const inputRect = input.getBoundingClientRect();
                            if (Math.abs(rect.top - inputRect.bottom) < 200) {
                                const firstLi = ul.querySelector('li');
                                if (firstLi) {
                                    firstLi.click();
                                    return 'clicked: nearby list';
                                }
                            }
                        }
                    }
                }
                
                return 'not found';
            }
        """)
        
        logger.info(f"Diagnosis: {diagnosis_clicked}")
        await asyncio.sleep(1)
        
        val = await page.evaluate("() => document.getElementById('ContentPlaceHolder1_txtDiagnosis')?.value")
        if not val or len(val) < 3:
            await page.focus("#ContentPlaceHolder1_txtDiagnosis")
            await asyncio.sleep(0.5)
            await page.keyboard.press("ArrowDown")
            await asyncio.sleep(0.5)
            await page.keyboard.press("Enter")
            await asyncio.sleep(1)
    except Exception as e:
        logger.error(f"Diagnosis: {e}")
    
    await select_option_by_label(page, "ContentPlaceHolder1_ME_Type_Drop", "Prescribing", "Stage")
    await fill_text(page, "ContentPlaceHolder1_Event_Desc_Txt", data['description'], "Description")
    await select_option_by_label(page, "ContentPlaceHolder1_ActionTaken_Drop", "Call the physician", "Action Taken")
    await fill_text(page, "ContentPlaceHolder1_Reporter_Name_Txt", "Az", "Reporter")
    await fill_text(page, "ContentPlaceHolder1_Reporter_Email_Txt", "aalhazmi50@moh.gov.sa", "Email")
    await fill_text(page, "ContentPlaceHolder1_Reporter_Mobile_Txt", "0547995498", "Mobile")
    await select_option_by_label(page, "ContentPlaceHolder1_Staff_Cat_Drop", "Pharmacist", "Staff")


async def fill_form(data: dict) -> dict:
    result = {"success": False, "error": "", "field_status": {}, "all_filled": False,
              "before_submit": "", "after_submit": "", "after_yes": "",
              "submit_clicked": False, "yes_success": False, "final_url": ""}
    
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True, capture_output=True)
    except:
        pass
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        context.set_default_timeout(120000)
        page = await context.new_page()
        page.on("dialog", lambda dialog: asyncio.create_task(dialog.accept()))
        
        try:
            logger.info("🌐 Opening form...")
            await page.goto(FORM_URL, wait_until="networkidle", timeout=120000)
            await asyncio.sleep(5)
            
            logger.info("========== ROUND 1 ==========")
            await fill_all_simple_fields(page, data)
            await asyncio.sleep(2)
            
            logger.info("========== Type + Add ==========")
            type_labels = {"12": "Wrong/missed indication", "9": "wrong/missed duration", "1": "Wrong/missed dose"}
            type_label = type_labels.get(data['type_of_error'], "Wrong/missed indication")
            await select_option_by_label(page, "ContentPlaceHolder1_ddlNewTypeOfError", type_label, "Type")
            await asyncio.sleep(0.5)
            try:
                await page.click("#ContentPlaceHolder1_NewTypeOfError_Main_Btn", timeout=10000)
                await safe_wait_after_postback(page, 5)
            except Exception as e:
                logger.error(f"Type Add: {e}")
            
            logger.info("========== ROUND 2 ==========")
            await fill_all_simple_fields(page, data)
            await asyncio.sleep(2)
            
            logger.info("========== Medication + Add ==========")
            try:
                med_value = await page.evaluate(f"""
                    () => {{
                        const sel = document.getElementById('ContentPlaceHolder1_Generic_Name_Drop');
                        if (!sel) return null;
                        const term = '{data['medication_search']}'.toLowerCase();
                        for (let i = 0; i < sel.options.length; i++) {{
                            if (sel.options[i].text.toLowerCase().includes(term)) return sel.options[i].value;
                        }}
                        return null;
                    }}
                """)
                if med_value:
                    await page.select_option("#ContentPlaceHolder1_Generic_Name_Drop", value=med_value)
                    await asyncio.sleep(0.5)
                    await page.click("#ContentPlaceHolder1_Add_Med", timeout=10000)
                    await safe_wait_after_postback(page, 5)
            except Exception as e:
                logger.error(f"Medication: {e}")
            
            logger.info("========== ROUND 3 ==========")
            await fill_all_simple_fields(page, data)
            await asyncio.sleep(2)
            
            logger.info("========== Factor + Add ==========")
            try:
                await asyncio.sleep(2)
                factor_value = await page.evaluate("""
                    () => {
                        const sel = document.getElementById('ContentPlaceHolder1_Factors_Drop');
                        if (!sel) return null;
                        for (let i = 0; i < sel.options.length; i++) {
                            if (sel.options[i].text.toLowerCase().includes('lack of knowledge')) return sel.options[i].value;
                        }
                        return null;
                    }
                """)
                if factor_value:
                    await page.select_option("#ContentPlaceHolder1_Factors_Drop", value=factor_value)
                    await asyncio.sleep(1)
                    await page.click("#ContentPlaceHolder1_Factors_Main_Btn", timeout=10000, force=True)
                    await safe_wait_after_postback(page, 6)
                else:
                    logger.error("Factor value not found!")
            except Exception as e:
                logger.error(f"Factor: {e}")
            
            logger.info("========== ROUND 4 FINAL ==========")
            await fill_all_simple_fields(page, data)
            await asyncio.sleep(3)
            
            final_check = await page.evaluate("""
                () => ({
                    mrn: document.getElementById('ContentPlaceHolder1_Mr_Txt')?.value || '',
                    date: document.getElementById('ContentPlaceHolder1_Event_Date_Txt')?.value || '',
                    gender: document.getElementById('ContentPlaceHolder1_Gender_Drop')?.value || '',
                    where: document.getElementById('ContentPlaceHolder1_WhereItHappen_Drop')?.value || '',
                    diagnosis: document.getElementById('ContentPlaceHolder1_txtDiagnosis')?.value || '',
                    description: document.getElementById('ContentPlaceHolder1_Event_Desc_Txt')?.value || '',
                    reporter: document.getElementById('ContentPlaceHolder1_Reporter_Name_Txt')?.value || '',
                    email: document.getElementById('ContentPlaceHolder1_Reporter_Email_Txt')?.value || '',
                    mobile: document.getElementById('ContentPlaceHolder1_Reporter_Mobile_Txt')?.value || '',
                    stage: document.getElementById('ContentPlaceHolder1_ME_Type_Drop')?.value || '',
                    action: document.getElementById('ContentPlaceHolder1_ActionTaken_Drop')?.value || '',
                    staff: document.getElementById('ContentPlaceHolder1_Staff_Cat_Drop')?.value || '',
                    reach_no: document.getElementById('ContentPlaceHolder1_ErrorReachPatient_1')?.checked || false,
                    wasfaty_other: document.getElementById('ContentPlaceHolder1_Wasfaty_Chk_1')?.checked || false
                })
            """)
            logger.info(f"🔍 FINAL: {final_check}")
            
            status = {
                "mrn": bool(str(final_check.get('mrn', '')).strip()),
                "date": bool(str(final_check.get('date', '')).strip()),
                "gender": bool(str(final_check.get('gender', '')).strip() and str(final_check.get('gender', '')) != ''),
                "where": bool(str(final_check.get('where', '')).strip() and str(final_check.get('where', '')) != ''),
                "diagnosis": bool(str(final_check.get('diagnosis', '')).strip()),
                "description": bool(str(final_check.get('description', '')).strip()),
                "reporter": bool(str(final_check.get('reporter', '')).strip()),
                "email": bool(str(final_check.get('email', '')).strip()),
                "mobile": bool(str(final_check.get('mobile', '')).strip()),
                "stage": bool(str(final_check.get('stage', '')).strip() and str(final_check.get('stage', '')) != ''),
                "action": bool(str(final_check.get('action', '')).strip() and str(final_check.get('action', '')) != ''),
                "staff": bool(str(final_check.get('staff', '')).strip() and str(final_check.get('staff', '')) != ''),
                "reach_no": final_check.get('reach_no', False),
                "prescription": final_check.get('wasfaty_other', False),
            }
            result["field_status"] = status
            
            critical = ["reach_no", "date", "prescription", "stage", "description", "diagnosis", "action",
                       "mrn", "gender", "where", "reporter", "email", "mobile", "staff"]
            all_ok = all(status.get(f, False) for f in critical)
            result["all_filled"] = all_ok
            
            if all_ok:
                logger.info("🚀 Submitting...")
                await page.evaluate("""
                    () => {
                        const btn = document.getElementById('ContentPlaceHolder1_Submit_Btn');
                        if (btn) btn.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    }
                """)
                await asyncio.sleep(2)
                
                await page.screenshot(path="/tmp/before_submit.png", full_page=True)
                result["before_submit"] = "/tmp/before_submit.png"
                
                submit_clicked = False
                try:
                    await page.click("#ContentPlaceHolder1_Submit_Btn", timeout=15000, force=True)
                    submit_clicked = True
                except Exception as e:
                    logger.error(f"Submit: {e}")
                
                if not submit_clicked:
                    try:
                        await page.evaluate("""
                            () => {
                                const btn = document.getElementById('ContentPlaceHolder1_Submit_Btn');
                                if (btn) { btn.click(); return true; }
                                return false;
                            }
                        """)
                        submit_clicked = True
                    except: pass
                
                result["submit_clicked"] = submit_clicked
                await asyncio.sleep(5)
                
                yes_success = False
                for yes_attempt in range(5):
                    try:
                        yes_result = await page.evaluate("""
                            () => {
                                const allButtons = document.querySelectorAll('input[type="button"], input[type="submit"], button, a');
                                for (const b of allButtons) {
                                    const text = (b.value || b.innerText || '').trim();
                                    const visible = b.offsetParent !== null;
                                    if (visible && text === 'Yes') {
                                        b.click();
                                        return {clicked: true};
                                    }
                                }
                                return {clicked: false};
                            }
                        """)
                        if yes_result.get('clicked'):
                            yes_success = True
                            break
                    except:
                        pass
                    await asyncio.sleep(2)
                
                result["yes_success"] = yes_success
                await asyncio.sleep(15)
                
                await page.screenshot(path="/tmp/after_yes.png", full_page=True)
                result["after_yes"] = "/tmp/after_yes.png"
            else:
                await page.screenshot(path="/tmp/before_submit.png", full_page=True)
                result["before_submit"] = "/tmp/before_submit.png"
            
            result["final_url"] = page.url
            result["success"] = True
            await browser.close()
            return result
            
        except Exception as e:
            logger.error(f"💥 {e}")
            result["error"] = str(e)
            try: await browser.close()
            except: pass
            return result


async def process_one(message, context, image_path, keyword, prefix=""):
    try:
        extracted = extract_from_image(image_path, keyword)
        if not extracted["mrn"] or not extracted["date"]:
            await message.reply_text(f"{prefix}⚠️ فشل قراءة البيانات")
            return False
        
        case = get_case_details(keyword, extracted.get("medication", ""))
        full_data = {**extracted, **case}
        
        await message.reply_text(
            f"{prefix}📋 {full_data['mrn']} | {full_data['date']} {full_data.get('time', '10:00')} | {full_data['gender']}\n"
            f"Dx: {full_data['diagnosis']}\n"
            f"💊 {full_data['medication_search']}\n\n⏳ ملء النموذج..."
        )
        
        result = await fill_form(full_data)
        
        if result.get("field_status"):
            status = result["field_status"]
            report = f"{prefix}📊 تقرير:\n"
            for k, v in status.items():
                report += f"{'✅' if v else '❌'} {k}\n"
            if result.get("all_filled"):
                report += "\n✅ الكل ممتلئ"
            else:
                report += "\n⚠️ بعض الحقول فاضية"
            await message.reply_text(report)
        
        if result.get("after_yes") and os.path.exists(result["after_yes"]):
            try:
                with open(result["after_yes"], "rb") as f:
                    await message.reply_photo(photo=f, caption=f"{prefix}📸 النتيجة")
            except: pass
        elif result.get("before_submit") and os.path.exists(result["before_submit"]):
            try:
                with open(result["before_submit"], "rb") as f:
                    await message.reply_photo(photo=f, caption=f"{prefix}📸")
            except: pass
        
        diag = f"{prefix}🔍 Submit: {'✅' if result.get('submit_clicked') else '❌'} | Yes: {'✅' if result.get('yes_success') else '❌'}"
        await message.reply_text(diag)
        
        if result["success"] and result.get("all_filled") and result.get("yes_success"):
            await message.reply_text(f"{prefix}Done ✔️")
            return True
        elif result["success"] and result.get("all_filled"):
            await message.reply_text(f"{prefix}⚠️ Submit ضُغط لكن Yes فشل")
            return False
        elif result["success"]:
            await message.reply_text(f"{prefix}⚠️ الحقول غير مكتملة")
            return False
        else:
            await message.reply_text(f"{prefix}⚠️ {result.get('error', '')}")
            return False
    except Exception as e:
        logger.error(f"process_one: {e}")
        await message.reply_text(f"{prefix}❌ {str(e)}")
        return False


async def process_media_group(context, group_id):
    await asyncio.sleep(3)
    async with MEDIA_GROUPS_LOCK:
        if group_id not in MEDIA_GROUPS:
            return
        group = MEDIA_GROUPS.pop(group_id)
    
    messages = group["messages"]
    keyword = group["keyword"]
    first_message = messages[0]
    
    if not keyword:
        await first_message.reply_text("⚠️ اكتب الكلمة المفتاحية")
        return
    
    total = len(messages)
    if total > 5:
        messages = messages[:5]
        total = 5
    
    await first_message.reply_text(f"📸 {total} صور | 🔑 {keyword}\n⏳ سأعالجها واحدة واحدة")
    
    heartbeat_running = True
    async def heartbeat():
        while heartbeat_running:
            await asyncio.sleep(25)
            try:
                await context.bot.send_chat_action(chat_id=first_message.chat_id, action="typing")
            except: pass
    heartbeat_task = asyncio.create_task(heartbeat())
    
    try:
        success_count = 0
        for i, msg in enumerate(messages, 1):
            prefix = f"[{i}/{total}] "
            if msg.photo:
                file_id = msg.photo[-1].file_id
            elif msg.document:
                file_id = msg.document.file_id
            else:
                continue
            
            await first_message.reply_text(f"{prefix}⏳ بدأت المعالجة...")
            
            try:
                file = await context.bot.get_file(file_id)
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                    await file.download_to_drive(tmp.name)
                    image_path = tmp.name
                
                success = await process_one(first_message, context, image_path, keyword, prefix)
                if success:
                    success_count += 1
                try: os.unlink(image_path)
                except: pass
            except Exception as e:
                logger.error(f"Image {i}: {e}")
                await first_message.reply_text(f"{prefix}❌ {str(e)}")
        
        await first_message.reply_text(f"🎉 انتهيت!\n✅ نجح: {success_count}/{total}\n❌ فشل: {total - success_count}/{total}")
    finally:
        heartbeat_running = False
        try: heartbeat_task.cancel()
        except: pass


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message.photo and not message.document:
        await message.reply_text("أرسل صورة مع caption")
        return
    
    if message.media_group_id:
        group_id = message.media_group_id
        async with MEDIA_GROUPS_LOCK:
            if group_id not in MEDIA_GROUPS:
                MEDIA_GROUPS[group_id] = {"messages": [], "keyword": message.caption or "", "task_started": False}
            MEDIA_GROUPS[group_id]["messages"].append(message)
            if message.caption and not MEDIA_GROUPS[group_id]["keyword"]:
                MEDIA_GROUPS[group_id]["keyword"] = message.caption
            if not MEDIA_GROUPS[group_id]["task_started"]:
                MEDIA_GROUPS[group_id]["task_started"] = True
                asyncio.create_task(process_media_group(context, group_id))
        return
    
    keyword = message.caption or ""
    if not keyword:
        await message.reply_text("⚠️ اكتب الكلمة المفتاحية")
        return
    
    await message.reply_text("⏳ جاري المعالجة... (5-7 دقائق ☕)")
    
    heartbeat_running = True
    async def heartbeat():
        while heartbeat_running:
            await asyncio.sleep(25)
            try:
                await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")
            except: pass
    heartbeat_task = asyncio.create_task(heartbeat())
    
    try:
        if message.photo:
            file = await context.bot.get_file(message.photo[-1].file_id)
        else:
            file = await context.bot.get_file(message.document.file_id)
        
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            await file.download_to_drive(tmp.name)
            image_path = tmp.name
        
        await process_one(message, context, image_path, keyword)
        try: os.unlink(image_path)
        except: pass
    except Exception as e:
        logger.error(f"{e}")
        await message.reply_text(f"❌ {str(e)}")
    finally:
        heartbeat_running = False
        try: heartbeat_task.cancel()
        except: pass


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is running")
    def log_message(self, format, *args):
        pass


def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info(f"🌐 Health server on port {port}")
    server.serve_forever()


def main():
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    app = Application.builder().token(TELEGRAM_TOKEN).read_timeout(600).write_timeout(600).connect_timeout(600).pool_timeout(600).build()
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_message))
    logger.info("🤖 Bot started...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
