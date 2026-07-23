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

# إعداد تطبيق Flask لاستجابة UptimeRobot
app = Flask(__name__)

@app.route('/')
def home():
    return {"status": "active", "message": "Syrian Tourism Bot is running smoothly!"}, 200

# جلب الإعدادات من متغيرات البيئة
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")
DATABASE_URL = os.environ.get("DATABASE_URL")

# دالة الاتصال بقاعدة البيانات وتحديث الأعمدة تلقائياً
def init_db():
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        
        # إنشاء الجدول أساساً إن لم يكن موجوداً
        cur.execute('''
            CREATE TABLE IF NOT EXISTS posted_news (
                id SERIAL PRIMARY KEY,
                news_url TEXT UNIQUE,
                title TEXT,
                status TEXT DEFAULT 'pending'
            )
        ''')
        
        # إضافة الأعمدة الجديدة بأمان إذا كانت مفقودة في الجداول القديمة
        cur.execute("ALTER TABLE posted_news ADD COLUMN IF NOT EXISTS full_text TEXT;")
        cur.execute("ALTER TABLE posted_news ADD COLUMN IF NOT EXISTS media_url TEXT;")
        
        conn.commit()
        cur.close()
        conn.close()
        logging.info("Database initialized and updated successfully.")
    except Exception as e:
        logging.error(f"Error initializing database: {e}")

# دالة إرسال الوسائط أو النصوص إلى تليجرام
def send_to_telegram(title, full_text, link, media_url, is_urgent=False):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logging.error("Telegram credentials are missing!")
        return False
    
    current_hour = datetime.now().hour
    
    if is_urgent:
        icon = "🚨"
        category_tag = "#عاجل #تعميم_رسمي"
        header = "عاجل | تحديث رسمي جديد"
    elif 9 <= current_hour < 12:
        icon = "☀️"
        category_tag = "#النشاطات_السياحية #قطاع_تعليمي #وزارة_السياحة"
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

    caption = (
        f"{icon} **{header}**\n\n"
        f"📌 **{title}**\n\n"
        f"{full_text[:600]}...\n\n"
        f"🔗 [قراءة التفاصيل والخبر كاملاً من الموقع الرسمي]({link})\n\n"
        f"{category_tag} #وزارة_السياحة #سانا"
    )

    try:
        if media_url and (media_url.endswith(('.jpg', '.png', '.jpeg', '.webp'))):
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "photo": media_url,
                "caption": caption,
                "parse_mode": "Markdown"
            }
        else:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "text": caption,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False
            }

        response = requests.post(url, json=payload)
        if response.status_code == 200:
            logging.info("Post sent to Telegram successfully.")
            return True
        else:
            logging.error(f"Failed to send to Telegram: {response.text}")
            return False
    except Exception as e:
        logging.error(f"Exception while sending to Telegram: {e}")
        return False

def fetch_article_details(article_url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(article_url, headers=headers, timeout=10)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            content_div = soup.find('div', class_='entry-content') or soup.find('div', class_='post-content')
            full_text = content_div.get_text(strip=True) if content_div else "التفاصيل متاحة عبر الرابط الرسمي."
            img_tag = soup.find('img', class_='wp-post-image') or (content_div.find('img') if content_div else None)
            media_url = img_tag.get('src') if img_tag else None
            return full_text, media_url
    except Exception as e:
        logging.error(f"Error fetching article details from {article_url}: {e}")
    return "التفاصيل متاحة عبر الرابط الرسمي.", None

def fetch_and_store_news():
    try:
        target_url = "https://sana.sy/en/tour-syria/"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(target_url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            articles = soup.find_all('h3', class_='entry-title') or soup.find_all('a', class_='item-title')
            
            conn = psycopg2.connect(DATABASE_URL, sslmode='require')
            cur = conn.cursor()
            
            for art in articles[:5]:
                link_tag = art.find('a') if art.name != 'a' else art
                if link_tag and link_tag.get('href'):
                    news_link = link_tag['href']
                    news_title = link_tag.get_text(strip=True)
                    
                    cur.execute("SELECT id FROM posted_news WHERE news_url = %s", (news_link,))
                    exists = cur.fetchone()
                    
                    if not exists:
                        full_text, media_url = fetch_article_details(news_link)
                        cur.execute(
                            "INSERT INTO posted_news (news_url, title, full_text, media_url, status) VALUES (%s, %s, %s, %s, %s)",
                            (news_link, news_title, full_text, media_url, 'pending')
                        )
                        conn.commit()
            cur.close()
            conn.close()
    except Exception as e:
        logging.error(f"Error while fetching news: {e}")

def send_immediate_sample_post():
    try:
        time.sleep(6)
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        cur.execute("SELECT id, news_url, title, full_text, media_url FROM posted_news WHERE status = 'pending' ORDER BY id ASC LIMIT 1")
        row = cur.fetchone()
        
        if row:
            news_id, news_link, news_title, full_text, media_url = row
            if send_to_telegram(news_title, full_text, news_link, media_url, is_urgent=False):
                cur.execute("UPDATE posted_news SET status = 'sent' WHERE id = %s", (news_id,))
                conn.commit()
                logging.info("Immediate sample post sent successfully.")
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error sending immediate sample post: {e}")

def hourly_publisher_worker():
    while True:
        try:
            time.sleep(3600)
            conn = psycopg2.connect(DATABASE_URL, sslmode='require')
            cur = conn.cursor()
            cur.execute("SELECT id, news_url, title, full_text, media_url FROM posted_news WHERE status = 'pending' ORDER BY id ASC LIMIT 1")
            row = cur.fetchone()
            
            if row:
                news_id, news_link, news_title, full_text, media_url = row
                if send_to_telegram(news_title, full_text, news_link, media_url, is_urgent=False):
                    cur.execute("UPDATE posted_news SET status = 'sent' WHERE id = %s", (news_id,))
                    conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logging.error(f"Error in hourly publisher worker: {e}")

def background_scraper_worker():
    while True:
        fetch_and_store_news()
        time.sleep(900)

def start_background_tasks():
    init_db()
    fetch_and_store_news()
    
    threading.Thread(target=send_immediate_sample_post, daemon=True).start()
    threading.Thread(target=background_scraper_worker, daemon=True).start()
    threading.Thread(target=hourly_publisher_worker, daemon=True).start()

start_background_tasks()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
