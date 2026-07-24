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
    return {"status": "active", "message": "Syrian Tourism & News Bot is running perfectly!"}, 200

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
                source TEXT DEFAULT '',
                pub_date TEXT DEFAULT '',
                status TEXT DEFAULT 'pending'
            )
        ''')
        cur.execute("ALTER TABLE posted_news ADD COLUMN IF NOT EXISTS full_text TEXT;")
        cur.execute("ALTER TABLE posted_news ADD COLUMN IF NOT EXISTS media_url TEXT;")
        cur.execute("ALTER TABLE posted_news ADD COLUMN IF NOT EXISTS source TEXT DEFAULT '';")
        cur.execute("ALTER TABLE posted_news ADD COLUMN IF NOT EXISTS pub_date TEXT DEFAULT '';")
        
        # حقن عينة ملف PDF تجريبي فورياً للتأكد من وصول الملفات للتلغرام مباشرة
        cur.execute("SELECT id FROM posted_news WHERE news_url = %s", ("https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf",))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO posted_news (news_url, title, full_text, media_url, source, pub_date, status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf",
                    "تعميم إداري برقم 45 الصادر عن وزارة السياحة حول المعايير التنظيمية",
                    "يحتوي هذا الملف الرسمي على تفاصيل الاشتراطات والمعايير التنظيمية الصادرة عن وزارة السياحة السورية للمنشآت والشركات السياحية.",
                    "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf",
                    "mots",
                    datetime.now().strftime("%Y/%m/%d %I:%M %p"),
                    "pending"
                )
            )
            conn.commit()

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

def send_to_telegram(title, full_text, link, media_url, pub_date="", source=""):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logging.error("Telegram credentials are missing!")
        return False
    
    is_pdf = media_url and media_url.lower().endswith('.pdf')
    
    if is_pdf:
        source_label = "وزارة السياحة السورية (التعاميم والقرارات الرسمية)"
        source_tag = "#وزارة_السياحة"
        title_prefix = "عنوان الملف:"
    else:
        source_label = "وكالة الأنباء السورية - سانا (قسم السياحة)"
        source_tag = "#وكالة_سانا"
        title_prefix = "عنوان التقرير:"

    formatted_date = pub_date if pub_date else datetime.now().strftime("%Y/%m/%d %I:%M %p")
    safe_text = clean_text_content(full_text)
    if not safe_text:
        safe_text = "التفاصيل الكاملة متوفرة عبر الرابط الرسمي أدناه."

    if len(safe_text) > 700:
        safe_text = safe_text[:700] + "..."

    caption = (
        f"مصدر المنشور: {source_label}\n"
        f"تاريخ النشر: {formatted_date}\n\n"
        f"{title_prefix}\n{title}\n\n"
        f"{safe_text}\n\n"
        f"يمكنكم متابعة تفاصيل الخبر رسمياً عبر الرابط أدناه:\n"
        f"{link}\n\n"
        f"#السياحة_السورية {source_tag} #سوريا"
    )

    try:
        session = get_robust_session()
        sent_successfully = False
        
        # إرسال ملف الـ PDF مباشرة كـ Document ليتم تنزيله من المنشور
        if is_pdf:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "document": media_url,
                "caption": caption
            }
            response = session.post(url, json=payload, timeout=30)
            if response.status_code == 200:
                sent_successfully = True
            else:
                logging.warning(f"Failed to send PDF document: {response.text}")

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

def fetch_mots_pdfs():
    session = get_robust_session()
    headers = {'User-Agent': 'Mozilla/5.0'}
    base_url = "https://mots.gov.sy/"
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        response = session.get(base_url, headers=headers, timeout=20, verify=False)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            for l in soup.find_all('a'):
                href = l.get('href')
                title = l.get_text(strip=True)
                if href and '.pdf' in href.lower() and len(title) > 5:
                    pdf_link = href if href.startswith('http') else base_url + href.lstrip('/')
                    pub_date = datetime.now().strftime("%Y/%m/%d %I:%M %p")
                    
                    cur.execute("SELECT id FROM posted_news WHERE news_url = %s", (pdf_link,))
                    if not cur.fetchone():
                        cur.execute(
                            "INSERT INTO posted_news (news_url, title, full_text, media_url, source, pub_date, status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                            (pdf_link, title, f"ملف تعميم أو قرار صادر عن وزارة السياحة بعنوان: {title}", pdf_link, 'mots', pub_date, 'pending')
                        )
                        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error fetching MOTS PDFs: {e}")

def fetch_sana_news():
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
    time.sleep(15)
    while True:
        try:
            fetch_mots_pdfs()
            fetch_sana_news()
            
            conn = psycopg2.connect(DATABASE_URL, sslmode='require')
            cur = conn.cursor()
            
            # إعطاء الأولوية المطلقة لملفات الـ PDF لتظهر أولاً
            cur.execute("SELECT id, news_url, title, full_text, media_url, pub_date, source FROM posted_news WHERE status = 'pending' ORDER BY CASE WHEN media_url LIKE '%.pdf' THEN 1 ELSE 2 END, id ASC LIMIT 1")
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
    fetch_mots_pdfs()
    fetch_sana_news()
    threading.Thread(target=background_worker, daemon=True).start()

start_bot()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
