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
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = re.sub(r'\s+', '', os.environ.get("TELEGRAM_TOKEN", ""))
FORM_URL = "https://portal.spsc.gov.sa/MEH/Default.aspx?Id=454"


async def inspect_form():
    """فحص النموذج واستخراج selectors الحقيقية"""
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True, capture_output=True)
    except: pass
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        await page.goto(FORM_URL, wait_until="networkidle", timeout=60000)
        await asyncio.sleep(3)
        
        # استخراج كل الحقول
        inputs_info = await page.evaluate("""
            () => {
                const results = [];
                
                // جميع inputs
                document.querySelectorAll('input').forEach((el, i) => {
                    results.push({
                        type: 'input',
                        index: i,
                        id: el.id || 'NO_ID',
                        name: el.name || 'NO_NAME',
                        inputType: el.type,
                        placeholder: el.placeholder || 'NO_PLACEHOLDER',
                        label: (el.previousElementSibling?.innerText || el.parentElement?.previousElementSibling?.innerText || '').substring(0, 50)
                    });
                });
                
                // جميع selects
                document.querySelectorAll('select').forEach((el, i) => {
                    results.push({
                        type: 'select',
                        index: i,
                        id: el.id || 'NO_ID',
                        name: el.name || 'NO_NAME',
                        label: (el.previousElementSibling?.innerText || el.parentElement?.previousElementSibling?.innerText || '').substring(0, 50),
                        options: Array.from(el.options).map(o => o.text).slice(0, 5).join(' | ')
                    });
                });
                
                // جميع textareas
                document.querySelectorAll('textarea').forEach((el, i) => {
                    results.push({
                        type: 'textarea',
                        index: i,
                        id: el.id || 'NO_ID',
                        name: el.name || 'NO_NAME',
                        label: (el.previousElementSibling?.innerText || el.parentElement?.previousElementSibling?.innerText || '').substring(0, 50)
                    });
                });
                
                // جميع buttons
                document.querySelectorAll('button, input[type="submit"], input[type="button"]').forEach((el, i) => {
                    results.push({
                        type: 'button',
                        index: i,
                        id: el.id || 'NO_ID',
                        name: el.name || 'NO_NAME',
                        text: (el.innerText || el.value || '').substring(0, 50)
                    });
                });
                
                return results;
            }
        """)
        
        await browser.close()
        return inputs_info


async def handle_inspect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ جاري فحص النموذج...")
    
    try:
        results = await inspect_form()
        
        # تجميع النتائج
        inputs_text = "📋 الحقول (Inputs):\n"
        selects_text = "📋 القوائم (Selects):\n"
        textareas_text = "📋 Textareas:\n"
        buttons_text = "📋 الأزرار (Buttons):\n"
        
        for item in results:
            if item['type'] == 'input':
                inputs_text += f"\n#{item['index']} | id={item['id']} | name={item['name']} | type={item['inputType']} | label={item['label']}"
            elif item['type'] == 'select':
                selects_text += f"\n#{item['index']} | id={item['id']} | name={item['name']} | label={item['label']} | options={item['options']}"
            elif item['type'] == 'textarea':
                textareas_text += f"\n#{item['index']} | id={item['id']} | name={item['name']} | label={item['label']}"
            elif item['type'] == 'button':
                buttons_text += f"\n#{item['index']} | id={item['id']} | name={item['name']} | text={item['text']}"
        
        # إرسال كل قسم منفصلاً (تيليجرام يقبل 4000 حرف)
        for txt in [inputs_text, selects_text, textareas_text, buttons_text]:
            if len(txt) > 100:
                # تقسيم إذا كان طويل
                chunks = [txt[i:i+3500] for i in range(0, len(txt), 3500)]
                for chunk in chunks:
                    await update.message.reply_text(chunk)
    
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {str(e)}")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("inspect", handle_inspect))
    logger.info("Inspection bot started...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
