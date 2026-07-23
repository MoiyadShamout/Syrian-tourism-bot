import os
import time
import logging
from datetime import datetime
import threading
import requests
from bs4 import BeautifulSoup
import psycopg2
from flask import Flask

# إعداد السجلات (Logging)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# إعداد تطبيق Flask لاستجابة UptimeRobot وضمان عدم نوم الخدمة على Render
app = Flask(__name__)

@app.route('/')
def home():
    return {"status": "active", "message": "Syrian Tourism Bot is running smoothly!"}, 200

# جلب الإعدادات من متغيرات البيئة (Environment Variables)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")
DATABASE_URL = os.environ.get("DATABASE_URL")

# دالة الاتصال بقاعدة البيانات وإنشاء الجدول إذا لم يكن موجوداً
def init_db():
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS posted_news (
                id SERIAL PRIMARY KEY,
                news_url TEXT UNIQUE,
                title TEXT,
                status TEXT DEFAULT 'pending'
            )
        ''')
        conn.commit()
        cur.close()
        conn.close()
        logging.info("Database initialized successfully.")
    except Exception as e:
        logging.error(f"Error initializing database: {e}")

# دالة إرسال الرسائل إلى قناة التليجرام
def send_to_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logging.error("Telegram credentials are missing!")
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            logging.info("Message sent to Telegram successfully.")
            return True
        else:
            logging.error(f"Failed to send to Telegram: {response.text}")
            return False
    except Exception as e:
        logging.error(f"Exception while sending to Telegram: {e}")
        return False

# دالة تحديد الأيقونة والتصنيف حسب الوقت أو نوع الخبر
def format_news_message(title, link, is_urgent=False):
    current_hour = datetime.now().hour
    
    if is_urgent:
        icon = "🚨"
        category_tag = "#عاجل #تعميم_رسمي"
        header = "عاجل | تحديث رسمي جديد"
    elif 9 <= current_hour < 12:
        icon = "☀️"
        category_tag = "#النشاطات_السياحية #قطاع_تعليمي"
        header = "نشاطات السياحة والقطاع الأكاديمي"
    elif 12 <= current_hour < 16:
        icon = "⚖️"
        category_tag = "#قرارات_رسمية #مكاتب_السفر #قوانين_السفر"
        header = "تحديثات القرارات وقوانين المكاتب"
    elif 16 <= current_hour < 21:
        icon = "🌇"
        category_tag = "#معالم_سياحية #سياحة_سورية #اثار_سوريا #دليل_السفر"
        header = "دليل السياحة السورية | محطة مسائية"
    else:
        icon = "🌙✨"
        category_tag = "#استثمار_سياحي #مشاريع_سورية"
        header = "أفق الاستثمار والمشاريع السياحية"

    formatted_message = (
        f"{icon} **{header}**\n\n"
        f"📌 **العنوان:** {title}\n"
        f"📅 **التاريخ:** {datetime.now().strftime('%d-%m-%Y')}\n\n"
        f"🔗 [قراءة التفاصيل والخبر كاملاً من الموقع الرسمي]({link})\n\n"
        f"{category_tag} #وزارة_السياحة #سانا"
    )
    return formatted_message

# دالة فحص وجلب الأخبار من المواقع الرسمية
def fetch_and_store_news():
    try:
        # كمثال مبدئي، نقوم بطلب جلب الأخبار من وكالة سانا قسم السياحة أو موقع الوزارة
        target_url = "https://sana.sy/en/tour-syria/"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(target_url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            # استخراج العناوين والروابط (تعتمد على بنية الموقع الفعلي)
            articles = soup.find_all('h3', class_='entry-title') or soup.find_all('a', class_='item-title')
            
            conn = psycopg2.connect(DATABASE_URL, sslmode='require')
            cur = conn.cursor()
            
            for art in articles[:5]: # أخذ أحدث 5 مقالات
                link_tag = art.find('a') if art.name != 'a' else art
                if link_tag and link_tag.get('href'):
                    news_link = link_tag['href']
                    news_title = link_tag.get_text(strip=True)
                    
                    # التحقق مما إذا كان الخبر موجوداً مسبقاً في قاعدة البيانات
                    cur.execute("SELECT id FROM posted_news WHERE news_url = %s", (news_link,))
                    exists = cur.fetchone()
                    
                    if not exists:
                        # إدخال الخبر بحالة pending (قيد الانتظار)
                        cur.execute(
                            "INSERT INTO posted_news (news_url, title, status) VALUES (%s, %s, %s)",
                            (news_link, news_title, 'pending')
                        )
                        conn.commit()
                        logging.info(f"New article saved to DB: {news_title}")
                        
                        # فحص هل هو خبر عاجل لنشره فوراً
                        if "عاجل" in news_title or "تعميم" in news_title:
                            msg = format_news_message(news_title, news_link, is_urgent=True)
                            if send_to_telegram(msg):
                                cur.execute("UPDATE posted_news SET status = 'sent' WHERE news_url = %s", (news_link,))
                                conn.commit()
            
            cur.close()
            conn.close()
    except Exception as e:
        logging.error(f"Error while fetching news: {e}")

# دالة النشر المجدول (منشور واحد كل ساعة)
def hourly_publisher_worker():
    while True:
        try:
            time.sleep(3600) # الانتظار لمدة ساعة كاملة (3600 ثانية)
            logging.info("Running hourly publisher worker...")
            
            conn = psycopg2.connect(DATABASE_URL, sslmode='require')
            cur = conn.cursor()
            
            # البحث عن أول خبر بحالة pending
            cur.execute("SELECT id, news_url, title FROM posted_news WHERE status = 'pending' ORDER BY id ASC LIMIT 1")
            row = cur.fetchone()
            
            if row:
                news_id, news_link, news_title = row
                msg = format_news_message(news_title, news_link, is_urgent=False)
                
                if send_to_telegram(msg):
                    cur.execute("UPDATE posted_news SET status = 'sent' WHERE id = %s", (news_id,))
                    conn.commit()
                    logging.info(f"Hourly scheduled news published: {news_title}")
            
            cur.close()
            conn.close()
        except Exception as e:
            logging.error(f"Error in hourly publisher worker: {e}")

# دالة السكربت الدوري لجلب الأخبار في الخلفية (كل 15 دقيقة)
def background_scraper_worker():
    while True:
        fetch_and_store_news()
        time.sleep(900) # الانتظار 15 دقيقة قبل الفحص التالي

# نقطة التشغيل الرئيسية
if __name__ == "__main__":
    init_db()
    
    # تشغيل مهام الخلفية في خيوط منفصلة (Threads) لتعمل بالتوازي مع خادم الويب
    scraper_thread = threading.Thread(target=background_scraper_worker, daemon=True)
    scraper_thread.start()
    
    publisher_thread = threading.Thread(target=hourly_publisher_worker, daemon=True)
    publisher_thread.start()
    
    # تشغيل خادم Flask (ضروري لمنصة Render و UptimeRobot)
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
