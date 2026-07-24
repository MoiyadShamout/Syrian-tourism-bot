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
import urllib3

# إخفاء تحذيرات شهادات الأمان
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# إعداد السجلات
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

@app.route('/')
def home():
    return {"status": "active", "message": "Syrian News Bot is running perfectly!"}, 200

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_robust_session():
    session = requests.Session()
    retries = Retry(total=5, backoff_factor=2, status_forcelist=[500, 502, 503, 504])
    session.mount('https://', HTTPAdapter(max_retries=retries))
    session.mount('http://', HTTPAdapter(max_retries=retries))
    return session

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

def clean_text_content(text):
    if not text:
        return ""
    lines = text.split('\n')
    cleaned_lines = [l.strip() for l in lines if l.strip() and not l.strip().endswith('-سانا')]
    return "\n\n".join(cleaned_lines)

def send_to_telegram(title, full_text, link, media_url, pub_date="", source="sana"):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logging.error("Telegram credentials are missing!")
        return False
    
    # التسميات الدقيقة للمصدر ونوع المسمى المطلوب
    if "sana.sy" in link:
        source_label = "وكالة الأنباء السورية - سانا (قسم السياحة)"
        source_tag = "#وكالة_سانا"
        title_label = "عنوان التقرير"
    elif media_url and media_url.lower().endswith('.pdf'):
        source_label = "وزارة السياحة السورية (التعاميم والقرارات)"
        source_tag = "#وزارة_السياحة"
        title_label = "عنوان الملف"
    else:
        source_label = "وزارة السياحة السورية"
        source_tag = "#وزارة_السياحة"
        title_label = "عنوان المنشور"

    formatted_date = pub_date if pub_date else datetime.now().strftime("%Y/%m/%d %I:%M %p")
    safe_text = clean_text_content(full_text)
    if not safe_text:
        safe_text = "التفاصيل الكاملة متوفرة عبر الرابط الرسمي أدناه."

    if len(safe_text) > 700:
        safe_text = safe_text[:700] + "..."

    # هيكل المنشور بالتنسيق المطلوب تماماً
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
        sent_successfully = False
        
        # إرسال ملف PDF حصرياً كمستند مباشر إذا وجد رابط ملف حقيقي
        if media_url and media_url.lower().endswith('.pdf'):
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "document": media_url,
                "caption": caption
            }
            response = session.post(url, json=payload, timeout=30)
            if response.status_code == 200:
                sent_successfully = True

        # الإرسال كنص مباشر للتقارير والأخبار الصحفية
        if not sent_successfully:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "text": caption,
                "disable_web_page_preview": False
            }
            response = session.post(url, json=payload, timeout=15)
            if response.status_code == 200:
                sent_successfully = True

        return sent_successfully
    except Exception as e:
        logging.error(f"Exception while sending to Telegram: {e}")
        return False

def fetch_sana_news():
    """جلب التقارير والأخبار مباشرة من موقع وكالة سانا قسم السياحة مع تاريخ النشر الفعلي"""
    session = get_robust_session()
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        response = session.get("https://sana.sy/tourism/", headers=headers, timeout=20)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            for art in soup.find_all('h3', class_='entry-title'):
                link_tag = art.find('a')
                if link_tag and link_tag.get('href'):
                    news_link, news_title = link_tag['href'], link_tag.get_text(strip=True)
                    
                    sub_resp = session.get(news_link, headers=headers, timeout=15)
                    full_text = news_title
                    pub_date = datetime.now().strftime("%Y/%m/%d %I:%M %p")
                    
                    if sub_resp.status_code == 200:
                        sub_soup = BeautifulSoup(sub_resp.text, 'html.parser')
                        content_div = sub_soup.find('div', class_='entry-content')
                        if content_div:
                            paragraphs = [p.get_text(strip=True) for p in content_div.find_all('p') if p.get_text(strip=True)]
                            if paragraphs:
                                full_text = "\n\n".join(paragraphs)
                        
                        time_tag = sub_soup.find('time')
                        if time_tag:
                            pub_date = time_tag.get_text(strip=True)

                    cur.execute("SELECT id FROM posted_news WHERE news_url = %s", (news_link,))
                    if not cur.fetchone():
                        cur.execute(
                            "INSERT INTO posted_news (news_url, title, full_text, media_url, source, pub_date, status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                            (news_link, news_title, full_text, None, 'sana', pub_date, 'pending')
                        )
                        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error fetching Sana news: {e}")

def background_worker():
    time.sleep(30)
    while True:
        try:
            fetch_sana_news()
            
            conn = psycopg2.connect(DATABASE_URL, sslmode='require')
            cur = conn.cursor()
            
            cur.execute("SELECT id, news_url, title, full_text, media_url, pub_date, source FROM posted_news WHERE status = 'pending' ORDER BY id ASC LIMIT 1")
            row = cur.fetchone()
            if row:
                news_id, news_link, news_title, full_text, media_url, pub_date, source = row
                if send_to_telegram(news_title, full_text, news_link, media_url, pub_date, source):
                    cur.execute("UPDATE posted_news SET status = 'sent' WHERE id = %s", (news_id,))
                    conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logging.error(f"Error in background worker: {e}")
        time.sleep(1800)

def start_bot():
    init_db()
    fetch_sana_news()
    threading.Thread(target=background_worker, daemon=True).start()

start_bot()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
