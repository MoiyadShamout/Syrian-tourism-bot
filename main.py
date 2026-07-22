import requests
from bs4 import BeautifulSoup
import sqlite3
import os
from flask import Flask
import threading
import time

app = Flask(__name__)

# إعداد قاعدة البيانات لتخزين الروابط المرسلة وتجنب التكرار
def init_db():
    conn = sqlite3.connect('bot_database.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sent_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def send_telegram_message(text):
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    channel_id = os.environ.get('TELEGRAM_CHANNEL_ID')
    if bot_token and channel_id:
        telegram_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        try:
            requests.post(telegram_url, json={
                'chat_id': channel_id,
                'text': text,
                'parse_mode': 'Markdown'
            }, timeout=10)
        except Exception as e:
            print(f"Telegram error: {e}")

def fetch_and_send_single_news():
    """جلب خبر واحد فقط كاختبار أو كأحدث خبر مهم"""
    targets = [
        "https://sana.sy/",
        "https://sana.sy/syria-news/"
    ]
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    for target_url in targets:
        try:
            response = requests.get(target_url, headers=headers, timeout=15)
            if response.status_code != 200:
                continue
                
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # البحث عن الروابط والعناوين داخل العناوين الرئيسية
            for item in soup.find_all(['h2', 'h3', 'h4'], limit=10):
                a_tag = item.find('a', href=True)
                if not a_tag and item.name == 'a' and item.get('href'):
                    a_tag = item
                
                if a_tag:
                    title = a_tag.get_text(strip=True)
                    link = a_tag['href']
                    
                    # فلترة للتأكد أنه خبر حقيقي
                    if len(title) > 15 and link.startswith('http'):
                        conn = sqlite3.connect('bot_database.db')
                        cursor = conn.cursor()
                        cursor.execute('SELECT id FROM sent_news WHERE url = ?', (link,))
                        exists = cursor.fetchone()
                        
                        if not exists:
                            # حفظ الرابط لعدم تكراره مستقبلاً
                            cursor.execute('INSERT INTO sent_news (url) VALUES (?)', (link,))
                            conn.commit()
                            conn.close()
                            
                            # إرسال الخبر الأول الذي يتم العثور عليه فقط
                            message = f"📢 **خبر حصري / مهم:**\n\n{title}\n\n🔗 الرابط: {link}"
                            send_telegram_message(message)
                            return True  # الخروج بعد إرسال خبر واحد فقط لعدم إحداث أي سيل من الإشعارات
                        else:
                            conn.close()
        except Exception as e:
            print(f"Error fetching news: {e}")
    return False

# حلقة خلفية تعمل كل نصف ساعة لفحص وإرسال خبر جديد ومهم فقط
def background_worker():
    while True:
        time.sleep(1800)  # الانتظار لمدة 30 دقيقة (1800 ثانية)
        fetch_and_send_single_news()

# تشغيل العامل في الخلفية مع الخادم
thread = threading.Thread(target=background_worker, daemon=True)
thread.start()

@app.route('/')
def home():
    # عند زيارة الرابط (أو طلب UptimeRobot)، سيقوم بإرسال إشعار اختبار واحد فوري إذا لم يُرسل من قبل
    fetched = fetch_and_send_single_news()
    if fetched:
        return "Syrian Tourism & SANA Bot: Test news sent successfully to Telegram!"
    else:
        return "Syrian Tourism & SANA Bot is running successfully (No new unread news found)."

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
