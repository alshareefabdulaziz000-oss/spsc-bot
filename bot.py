async def inspect_form():
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True, capture_output=True)
    except: pass
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        await page.goto(FORM_URL, wait_until="networkidle", timeout=60000)
        await asyncio.sleep(3)
        
        inputs_info = await page.evaluate("""
            () => {
                const results = [];
                
                // كل selects فقط مع كل الخيارات
                document.querySelectorAll('select').forEach((el, i) => {
                    const options = Array.from(el.options).map(o => o.value + ':' + o.text).join(' | ');
                    results.push({
                        type: 'select',
                        id: el.id || 'NO_ID',
                        name: el.name || 'NO_NAME',
                        options: options
                    });
                });
                
                // كل textareas
                document.querySelectorAll('textarea').forEach((el, i) => {
                    results.push({
                        type: 'textarea',
                        id: el.id || 'NO_ID',
                        name: el.name || 'NO_NAME'
                    });
                });
                
                return results;
            }
        """)
        
        await browser.close()
        return inputs_info


async def handle_inspect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ فحص القوائم...")
    
    try:
        results = await inspect_form()
        
        text = "📋 القوائم:\n\n"
        for item in results:
            line = f"\n━━━\nTYPE: {item['type']}\nID: {item['id']}\n"
            if 'options' in item:
                line += f"OPTIONS: {item.get('options', '')}\n"
            text += line
        
        # تقسيم وإرسال
        chunks = [text[i:i+3500] for i in range(0, len(text), 3500)]
        for chunk in chunks:
            await update.message.reply_text(chunk)
    
    except Exception as e:
        await update.message.reply_text(f"❌ {str(e)}")
