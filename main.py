import os
import time
import sqlite3
import logging
import requests
from bs4 import BeautifulSoup
from flask import Flask
from threading import Thread
from telegram import Bot

# إعداد السجل (Logging)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# إعداد خريف Flask لضمان استمرار عمل السكربت على Render واستجابة UptimeRobot
app = Flask(__name__)

@app.route('/')
def home():
    return "Syrian Tourism & Official News Bot is running successfully!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# إعداد قاعدة البيانات لمنع تكرار نشر الأخبار
DB_FILE = "published_news.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS published (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_url TEXT UNIQUE
        )
    ''')
    conn.commit()
    conn.close()

def is_published(news_url):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM published WHERE news_url = ?", (news_url,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def mark_as_published(news_url):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO published (news_url) VALUES (?)", (news_url,))
    conn.commit()
    conn.close()

# إعداد بوت تليجرام
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHANNEL = os.environ.get("TELEGRAM_CHANNEL") # مثال: @YourChannel

bot = Bot(token=TELEGRAM_TOKEN) if TELEGRAM_TOKEN else None

# مصادر الأخبار الرسمية (وزارة السياحة وسانا)
SOURCES = [
    {
        "name": "وزارة السياحة السورية",
        "url": "http://mots.gov.sy/", 
        "tag": "#وزارة_السياحة"
    },
    {
        "name": "وكالة سانا (قسم السياحة)",
        "url": "https://sana.sy/?cat=32", 
        "tag": "#سانا #أخبار_سورية"
    }
]

def fetch_and_post_news():
    if not bot or not TELEGRAM_CHANNEL:
        logging.error("Telegram Token or Channel is missing in environment variables!")
        return

    for source in SOURCES:
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            response = requests.get(source["url"], headers=headers, timeout=15)
            if response.status_code != 200:
                continue

            soup = BeautifulSoup(response.text, 'html.parser')
            
            # استخراج الروابط والعناوين (سيتم مطابقتها مع هيكلية المواقع الرسمية)
            for a_tag in soup.find_all('a', href=True):
                title = a_tag.get_text(strip=True)
                link = a_tag['href']

                # تصفية العناوين القصيرة أو غير المفيدة
                if len(title) < 25:
                    continue

                if not link.startswith('http'):
                    continue

                if not is_published(link):
                    # صياغة رسالة الخبر الرسمية والمنسقة
                    message = (
                        f"📢 **تحديث رسمي جديد**\n\n"
                        f"📌 **العنوان:** {title}\n\n"
                        f"🏛 **المصدر:** {source['name']}\n"
                        f"🔗 [للاطلاع على التفاصيل الكاملة والمصدر]({link})\n\n"
                        f"{source['tag']} #سورية"
                    )

                    try:
                        bot.send_message(
                            chat_id=TELEGRAM_CHANNEL,
                            text=message,
                            parse_mode="Markdown",
                            disable_web_page_preview=False
                        )
                        mark_as_published(link)
                        logging.info(f"Successfully posted: {title}")
                        time.sleep(5) # فاصل زمني لتجنب حظر التليجرام
                    except Exception as e:
                        logging.error(f"Error sending message to Telegram: {e}")

        except Exception as e:
            logging.error(f"Error fetching from {source['name']}: {e}")

def news_loop():
    init_db()
    while True:
        logging.info("Checking for new official updates...")
        fetch_and_post_news()
        time.sleep(1800) # فحص التحديثات كل 30 دقيقة

if __name__ == "__main__":
    # تشغيل سيرفر Flask في خلفية مستقلة لـ UptimeRobot
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # تشغيل حلقة رصد الأخبار
    news_loop()
