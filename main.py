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
    return {"status": "active", "message": "Syrian Tourism Ministry-Focused Bot is running smoothly!"}, 200

# جلب الإعدادات من متغيرات البيئة
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")
DATABASE_URL = os.environ.get("DATABASE_URL")

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
        
        cur.execute("DELETE FROM posted_news WHERE news_url LIKE '%/en/%';")
        
        conn.commit()
        cur.close()
        conn.close()
        logging.info("Database initialized successfully.")
    except Exception as e:
        logging.error(f"Error initializing database: {e}")

# دالة إرسال المقال إلى تليجرام بالتنسيق المطلوب
def send_to_telegram(title, full_text, link, media_url, source_name="sana", pub_date=""):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logging.error("Telegram credentials are missing!")
        return False
    
    if source_name == "ministry":
        source_label = "موقع وزارة السياحة السورية"
        source_tag = "#وزارة_السياحة_السورية"
    else:
        source_label = "وكالة الأنباء السورية - سانا (قسم السياحة)"
        source_tag = "#وكالة_سانا"

    formatted_date = pub_date if pub_date else "غير محدد"
    safe_text = full_text if full_text else "تفاصيل الخبر متاحة عبر الرابط الرسمي أدناه."
    
    caption = (
        f"🏛️ **مصدر المنشور:** {source_label}\n"
        f"📅 **تاريخ النشر:** {formatted_date}\n\n"
        f"📌 **{title}**\n\n"
        f"{safe_text[:650]}...\n\n"
        f"يمكنكم متابعة تفاصيل الخبر رسمياً عبر الرابط أدناه:\n"
        f"🔗 {link}\n\n"
        f"#السياحة_السورية {source_tag} #سوريا"
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
            logging.info(f"Formatted post from [{source_name}] sent to Telegram successfully.")
            return True
        else:
            if "Markdown" in response.text:
                payload.pop("parse_mode", None)
                response = requests.post(url, json=payload)
                if response.status_code == 200:
                    return True
            logging.error(f"Failed to send to Telegram: {response.text}")
            return False
    except Exception as e:
        logging.error(f"Exception while sending to Telegram: {e}")
        return False

# استخراج تفاصيل سانا
def fetch_sana_article_details(article_url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(article_url, headers=headers, timeout=10)
        pub_date = ""
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            time_tag = soup.find('time') or soup.find('span', class_='date') or soup.find('span', class_='posted-on')
            if time_tag:
                pub_date = time_tag.get_text(strip=True)

            content_div = soup.find('div', class_='entry-content') or soup.find('div', class_='post-content')
            if content_div:
                paragraphs = content_div.find_all('p')
                full_text = "\n".join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])
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

# استخراج تفاصيل موقع وزارة السياحة
def fetch_ministry_article_details(article_url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(article_url, headers=headers, timeout=10)
        pub_date = ""
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            time_tag = soup.find('time') or soup.find('span', class_='date') or soup.find('div', class_='news-date') or soup.find('span', class_='published')
            if time_tag:
                pub_date = time_tag.get_text(strip=True)

            content_div = soup.find('div', class_='content') or soup.find('div', class_='article-body') or soup.find('article') or soup.find('main')
            if content_div:
                paragraphs = content_div.find_all('p')
                full_text = "\n".join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])
                if not full_text:
                    full_text = content_div.get_text(strip=True)
            else:
                full_text = "تفاصيل الخبر متاحة عبر الرابط الرسمي أدناه."

            img_tag = content_div.find('img') if content_div else soup.find('img')
            media_url = img_tag.get('src') if img_tag else None
            if media_url and not media_url.startswith('http'):
                media_url = "https://mots.gov.sy" + media_url
            return full_text, media_url, pub_date
    except Exception as e:
        logging.error(f"Error fetching Ministry details: {e}")
    return "تفاصيل الخبر متاحة عبر الرابط الرسمي أدناه.", None, ""

# جلب وتخزين الأخبار من المصدرين
def fetch_and_store_news():
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    cur = conn.cursor()
    headers = {'User-Agent': 'Mozilla/5.0'}

    # 1. سانا
    try:
        sana_url = "https://sana.sy/tourism/"
        response = requests.get(sana_url, headers=headers, timeout=15)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            articles = soup.find_all('h3', class_='entry-title') or soup.find_all('h2', class_='entry-title') or soup.find_all('a', class_='item-title')
            
            for art in articles[:3]:
                link_tag = art.find('a') if art.name != 'a' else art
                if link_tag and link_tag.get('href'):
                    news_link = link_tag['href']
                    news_title = link_tag.get_text(strip=True)
                    
                    if '/en/' in news_link:
                        continue
                        
                    cur.execute("SELECT id FROM posted_news WHERE news_url = %s", (news_link,))
                    if not cur.fetchone():
                        full_text, media_url, pub_date = fetch_sana_article_details(news_link)
                        cur.execute(
                            "INSERT INTO posted_news (news_url, title, full_text, media_url, source, pub_date, status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                            (news_link, news_title, full_text, media_url, 'sana', pub_date, 'pending')
                        )
                        conn.commit()
    except Exception as e:
        logging.error(f"Error fetching Sana news: {e}")

    # 2. وزارة السياحة
    try:
        ministry_url = "https://mots.gov.sy/"
        response = requests.get(ministry_url, headers=headers, timeout=15)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            links = soup.find_all('a', href=True)
            
            for tag in links:
                news_link = tag['href']
                news_title = tag.get_text(strip=True)
                
                if ('mots.gov.sy' in news_link or news_link.startswith('/')) and len(news_title) > 20:
                    if news_link.startswith('/'):
                        news_link = "https://mots.gov.sy" + news_link
                        
                    if 'index' in news_link or 'contact' in news_link:
                        continue

                    cur.execute("SELECT id FROM posted_news WHERE news_url = %s", (news_link,))
                    if not cur.fetchone():
                        full_text, media_url, pub_date = fetch_ministry_article_details(news_link)
                        cur.execute(
                            "INSERT INTO posted_news (news_url, title, full_text, media_url, source, pub_date, status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                            (news_link, news_title, full_text, media_url, 'ministry', pub_date, 'pending')
                        )
                        conn.commit()
    except Exception as e:
        logging.error(f"Error fetching Ministry news: {e}")

    cur.close()
    conn.close()

# نشر عينة فورية حصراً من موقع وزارة السياحة للمعاينة
def send_immediate_sample_posts():
    try:
        time.sleep(5)
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        
        # جلب ونشر خبر فوري من موقع وزارة السياحة فقط
        cur.execute("SELECT id, news_url, title, full_text, media_url, source, pub_date FROM posted_news WHERE status = 'pending' AND source = 'ministry' ORDER BY id ASC LIMIT 1")
        row_min = cur.fetchone()
        if row_min:
            news_id, news_link, news_title, full_text, media_url, source_name, pub_date = row_min
            if send_to_telegram(news_title, full_text, news_link, media_url, source_name, pub_date):
                cur.execute("UPDATE posted_news SET status = 'sent' WHERE id = %s", (news_id,))
                conn.commit()

        cur.close()
        conn.close()
        logging.info("Immediate Ministry preview sample sent.")
    except Exception as e:
        logging.error(f"Error sending immediate sample post: {e}")

# عامل النشر الدوري كل نصف ساعة بالتناوب بين المصدرين
def alternating_publisher_worker():
    last_source = 'ministry'  # لضمان أن يبدأ النشر الدوري تليها من سانا
    while True:
        try:
            time.sleep(1800)  # كل 30 دقيقة
            
            conn = psycopg2.connect(DATABASE_URL, sslmode='require')
            cur = conn.cursor()
            
            next_source = 'ministry' if last_source == 'sana' else 'sana'
            
            cur.execute("SELECT id, news_url, title, full_text, media_url, source, pub_date FROM posted_news WHERE status = 'pending' AND source = %s ORDER BY id ASC LIMIT 1", (next_source,))
            row = cur.fetchone()
            
            if not row:
                next_source = 'sana' if next_source == 'ministry' else 'ministry'
                cur.execute("SELECT id, news_url, title, full_text, media_url, source, pub_date FROM posted_news WHERE status = 'pending' AND source = %s ORDER BY id ASC LIMIT 1", (next_source,))
                row = cur.fetchone()
            
            if row:
                news_id, news_link, news_title, full_text, media_url, source_name, pub_date = row
                if send_to_telegram(news_title, full_text, news_link, media_url, source_name, pub_date):
                    cur.execute("UPDATE posted_news SET status = 'sent' WHERE id = %s", (news_id,))
                    conn.commit()
                    last_source = source_name
                    
            cur.close()
            conn.close()
        except Exception as e:
            logging.error(f"Error in alternating publisher worker: {e}")

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
