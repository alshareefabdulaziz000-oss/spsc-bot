import os
import subprocess

try:
    subprocess.run(["playwright", "install", "chromium"], check=True)
except Exception as e:
    print(f"Install error: {e}")

import re
import logging
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = re.sub(r'\s+', '', os.environ.get("TELEGRAM_TOKEN", ""))
FORM_URL = "https://portal.spsc.gov.sa/MEH/Default.aspx?Id=454"


async def inspect_form():
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True, capture_output=True)
    except: 
        pass
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        await page.goto(FORM_URL, wait_until="networkidle", timeout=60000)
        await asyncio.sleep(3)
        
        results = await page.evaluate("""
            () => {
                const data = [];
                
                document.querySelectorAll('select').forEach((el) => {
                    const options = Array.from(el.options).map(o => o.value + ':' + o.text).join(' | ');
                    data.push({
                        type: 'select',
                        id: el.id || 'NO_ID',
                        name: el.name || 'NO_NAME',
                        options: options.substring(0, 2000)
                    });
                });
                
                document.querySelectorAll('textarea').forEach((el) => {
                    data.push({
                        type: 'textarea',
                        id: el.id || 'NO_ID',
                        name: el.name || 'NO_NAME'
                    });
                });
                
                return data;
            }
        """)
        
        await browser.close()
        return results


async def handle_inspect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ فحص القوائم...")
    
    try:
        results = await inspect_form()
        
        text = "📋 القوائم:\n"
        for item in results:
            line = f"\n━━━\nTYPE: {item['type']}\nID: {item['id']}\n"
            if 'options' in item:
                line += f"OPTIONS: {item.get('options', '')}\n"
            text += line
        
        chunks = [text[i:i+3500] for i in range(0, len(text), 3500)]
        for chunk in chunks:
            await update.message.reply_text(chunk)
    
    except Exception as e:
        await update.message.reply_text(f"❌ {str(e)}")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("inspect", handle_inspect))
    logger.info("Inspection bot started...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
