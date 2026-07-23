import os
import time
import logging
from datetime import datetime
import threading
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import psycopg2
from flask import Flask

# إعداد السجلات (Logging)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# إعداد تطبيق Flask لاستجابة UptimeRobot
app = Flask(__name__)

@app.route('/')
def home():
    return {"status": "active", "message": "Syrian Tourism Sana-Only Bot is running smoothly!"}, 200

# جلب الإعدادات من متغيرات البيئة
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")
DATABASE_URL = os.environ.get("DATABASE_URL")

# جلسة طلبات قوية مع دعم إعادة المحاولة
def get_robust_session():
    session = requests.Session()
    retries = Retry(total=5, backoff_factor=2, status_forcelist=[500, 502, 503, 504])
    session.mount('https://', HTTPAdapter(max_retries=retries))
    session.mount('http://', HTTPAdapter(max_retries=retries))
    return session

# دالة الاتصال بقاعدة البيانات وإعداد الجداول
def init_db():
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS posted_news (
                id SERIAL PRIMARY KEY,
                news_url TEXT UNIQUE,
                title TEXT,
                source TEXT DEFAULT 'sana',
                pub_date TEXT DEFAULT '',
                status TEXT DEFAULT 'pending'
            )
        ''')
        cur.execute("ALTER TABLE posted_news ADD COLUMN IF NOT EXISTS full_text TEXT;")
        cur.execute("ALTER TABLE posted_news ADD COLUMN IF NOT EXISTS media_url TEXT;")
        cur.execute("ALTER TABLE posted_news ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'sana';")
        cur.execute("ALTER TABLE posted_news ADD COLUMN IF NOT EXISTS pub_date TEXT DEFAULT '';")
        
        conn.commit()
        cur.close()
        conn.close()
        logging.info("Database initialized successfully.")
    except Exception as e:
        logging.error(f"Error initializing database: {e}")

# دالة إرسال المقال إلى تليجرام مع ضبط طول النص ليتوافق مع قيود تليجرام للصور
def send_to_telegram(title, full_text, link, media_url, pub_date=""):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logging.error("Telegram credentials are missing!")
        return False
    
    source_label = "وكالة الأنباء السورية - سانا (قسم السياحة)"
    source_tag = "#وكالة_سانا"

    formatted_date = pub_date if pub_date else "غير محدد"
    safe_text = full_text if full_text else "تفاصيل الخبر متاحة عبر الرابط الرسمي أدناه."
    
    # اقتصاص النص لضمان عدم تجاوز الحد الأقصى لتعليق تليجرام (1024 حرفاً)
    if len(safe_text) > 550:
        safe_text = safe_text[:550] + "..."

    caption = (
        f"مصدر المنشور: {source_label}\n"
        f"تاريخ النشر: {formatted_date}\n\n"
        f"{title}\n\n"
        f"{safe_text}\n\n"
        f"يمكنكم متابعة تفاصيل الخبر رسمياً عبر الرابط أدناه:\n"
        f"{link}\n\n"
        f"#السياحة_السورية {source_tag} #سوريا"
    )

    try:
        session = get_robust_session()
        if media_url and (media_url.endswith(('.jpg', '.png', '.jpeg', '.webp'))):
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "photo": media_url,
                "caption": caption
            }
        else:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "text": caption,
                "disable_web_page_preview": False
            }

        response = session.post(url, json=payload, timeout=15)
        if response.status_code == 200:
            logging.info("Post from [sana] sent to Telegram successfully.")
            return True
        else:
            logging.error(f"Failed to send to Telegram: {response.text}")
            return False
    except Exception as e:
        logging.error(f"Exception while sending to Telegram: {e}")
        return False

# استخراج تفاصيل مقالات سانا مع فصل اسم المدينة والمصدر في سطر مستقل
def fetch_sana_article_details(article_url):
    try:
        session = get_robust_session()
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = session.get(article_url, headers=headers, timeout=20)
        pub_date = ""
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            time_tag = soup.find('time') or soup.find('span', class_='date') or soup.find('span', class_='posted-on')
            if time_tag:
                pub_date = time_tag.get_text(strip=True)

            content_div = soup.find('div', class_='entry-content') or soup.find('div', class_='post-content')
            if content_div:
                location_prefix = ""
                paragraphs = content_div.find_all('p')
                
                cleaned_paragraphs = []
                for p in paragraphs:
                    p_text = p.get_text(strip=True)
                    if not p_text:
                        continue
                    if not location_prefix and ('-سانا' in p_text or 'سانا-' in p_text) and len(p_text) < 40:
                        location_prefix = p_text
                        for prefix_candidate in ["دمشق-سانا", "حلب-سانا", "حمص-سانا", "اللاذقية-سانا", "طرطوس-سانا", "حماة-سانا", "دير الزور-سانا", "الحسكة-سانا", "الرقة-سانا", "درعا-سانا", "السويداء-سانا", "القنيطرة-سانا", "إدلب-سانا"]:
                            if p_text.startswith(prefix_candidate):
                                location_prefix = prefix_candidate
                                p_text = p_text[len(prefix_candidate):].strip()
                                break
                        if p_text:
                            cleaned_paragraphs.append(p_text)
                    else:
                        cleaned_paragraphs.append(p_text)

                body_text = "\n\n".join(cleaned_paragraphs)
                
                if location_prefix:
                    full_text = f"{location_prefix}\n\n{body_text}"
                else:
                    full_text = body_text

                if not full_text:
                    full_text = content_div.get_text(strip=True)
            else:
                full_text = "تفاصيل الخبر متاحة عبر الرابط الرسمي أدناه."

            img_tag = soup.find('img', class_='wp-post-image') or (content_div.find('img') if content_div else None)
            media_url = img_tag.get('src') if img_tag else None
            return full_text, media_url, pub_date
    except Exception as e:
        logging.error(f"Error fetching Sana details: {e}")
    return "تفاصيل الخبر متاحة عبر الرابط الرسمي أدناه.", None, ""

# جلب وتخزين الأخبار من موقع سانا حصراً
def fetch_and_store_news():
    session = get_robust_session()
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()

        sana_url = "https://sana.sy/tourism/"
        response = session.get(sana_url, headers=headers, timeout=20)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            articles = soup.find_all('h3', class_='entry-title') or soup.find_all('h2', class_='entry-title') or soup.find_all('a', class_='item-title')
            
            for art in articles[:10]:
                link_tag = art.find('a') if art.name != 'a' else art
                if link_tag and link_tag.get('href'):
                    news_link = link_tag['href']
                    news_title = link_tag.get_text(strip=True)
                    
                    if not news_link or '/en/' in news_link:
                        continue
                        
                    cur.execute("SELECT id FROM posted_news WHERE news_url = %s", (news_link,))
                    if not cur.fetchone():
                        full_text, media_url, pub_date = fetch_sana_article_details(news_link)
                        if full_text:
                            cur.execute(
                                "INSERT INTO posted_news (news_url, title, full_text, media_url, source, pub_date, status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                                (news_link, news_title, full_text, media_url, 'sana', pub_date, 'pending')
                            )
                            conn.commit()
                            logging.info(f"Stored new Sana article: {news_title}")

        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error fetching Sana news: {e}")

# نشر عينة فورية حصراً من سانا
def send_immediate_sample_posts():
    try:
        time.sleep(5)
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        
        cur.execute("SELECT id, news_url, title, full_text, media_url, pub_date FROM posted_news WHERE status = 'pending' AND source = 'sana' ORDER BY id ASC LIMIT 1")
        row = cur.fetchone()

        if row:
            news_id, news_link, news_title, full_text, media_url, pub_date = row
            if send_to_telegram(news_title, full_text, news_link, media_url, pub_date):
                cur.execute("UPDATE posted_news SET status = 'sent' WHERE id = %s", (news_id,))
                conn.commit()
                logging.info(f"Immediate Sana sample post sent: {news_title}")

        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error in sending immediate sample: {e}")

# عامل النشر الدوري كل نصف ساعة من سانا
def alternating_publisher_worker():
    while True:
        try:
            time.sleep(1800)  # كل 30 دقيقة
            
            conn = psycopg2.connect(DATABASE_URL, sslmode='require')
            cur = conn.cursor()
            
            cur.execute("SELECT id, news_url, title, full_text, media_url, pub_date FROM posted_news WHERE status = 'pending' AND source = 'sana' ORDER BY id ASC LIMIT 1")
            row = cur.fetchone()
            
            if row:
                news_id, news_link, news_title, full_text, media_url, pub_date = row
                if send_to_telegram(news_title, full_text, news_link, media_url, pub_date):
                    cur.execute("UPDATE posted_news SET status = 'sent' WHERE id = %s", (news_id,))
                    conn.commit()
                    logging.info(f"Scheduled Sana post sent: {news_title}")
                    
            cur.close()
            conn.close()
        except Exception as e:
            logging.error(f"Error in scheduled publisher worker: {e}")

def background_scraper_worker():
    while True:
        fetch_and_store_news()
        time.sleep(900)

def start_background_tasks():
    init_db()
    fetch_and_store_news()
    
    threading.Thread(target=send_immediate_sample_posts, daemon=True).start()
    threading.Thread(target=background_scraper_worker, daemon=True).start()
    threading.Thread(target=alternating_publisher_worker, daemon=True).start()

start_background_tasks()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
