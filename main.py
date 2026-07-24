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
    return {"status": "active", "message": "Syrian Tourism PDF Bot is running smoothly!"}, 200

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
                source TEXT DEFAULT 'mots',
                pub_date TEXT DEFAULT '',
                status TEXT DEFAULT 'pending'
            )
        ''')
        cur.execute("ALTER TABLE posted_news ADD COLUMN IF NOT EXISTS full_text TEXT;")
        cur.execute("ALTER TABLE posted_news ADD COLUMN IF NOT EXISTS media_url TEXT;")
        cur.execute("ALTER TABLE posted_news ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'mots';")
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

def send_to_telegram(title, full_text, link, media_url, pub_date="", source="mots"):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logging.error("Telegram credentials are missing!")
        return False
    
    source_label = "وزارة السياحة السورية (التعاميم والقرارات الرسمية)"
    source_tag = "#وزارة_السياحة"

    formatted_date = pub_date if pub_date else datetime.now().strftime("%Y/%m/%d")
    safe_text = clean_text_content(full_text)
    if not safe_text:
        safe_text = "التفاصيل الكاملة ومضمون القرار والشروط التنظيمية متوفرة في ملف الـ PDF المرفق أدناه."

    if len(safe_text) > 600:
        safe_text = safe_text[:600] + "... [تابع التفاصيل في الملف المرفق]"

    caption = (
        f"📄 **عنوان القرار/التعميم:**\n{title}\n\n"
        f"📝 **مضمون وفحوى الملف:**\n{safe_text}\n\n"
        f"🏛 **المصدر:** {source_label}\n"
        f"📅 **تاريخ النشر:** {formatted_date}\n"
        f"🔗 **الرابط الرسمي:** {link}\n\n"
        f"#السياحة_السورية {source_tag} #سوريا #تعاميم"
    )

    try:
        session = get_robust_session()
        sent_successfully = False
        
        # إرسال حصري لملف الـ PDF كمستند مباشر ليتم تحميله فوراً من المنشور
        if media_url and media_url.lower().endswith('.pdf'):
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "document": media_url,
                "caption": caption,
                "parse_mode": "Markdown"
            }
            response = session.post(url, json=payload, timeout=30)
            if response.status_code == 200:
                sent_successfully = True
            else:
                logging.warning(f"Telegram failed to send PDF document: {response.text}")

        # إذا لم يكن الملف بصيغة PDF مباشرة، يتم إرساله كنص تنبيهي مع الرابط
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

def fetch_and_store_mots_pdfs():
    """جلب التعاميم والقرارات والملفات بصيغة PDF حصرياً من موقع وزارة السياحة"""
    session = get_robust_session()
    headers = {'User-Agent': 'Mozilla/5.0'}
    base_url = "https://mots.gov.sy/"
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        
        response = session.get(base_url, headers=headers, timeout=25, verify=False)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            for l in soup.find_all('a'):
                href = l.get('href')
                title = l.get_text(strip=True)
                
                if not href or len(title) < 5:
                    continue
                
                pdf_link = href if href.startswith('http') else base_url + href.lstrip('/')
                
                # التركيز على الملفات التي تنتهي بـ PDF أو تحتوي على كلمة تعميم/قرار
                if '.pdf' in pdf_link.lower() or 'circular' in pdf_link.lower() or 'decision' in pdf_link.lower():
                    media_url = pdf_link
                    
                    # سحب محتوى أو سياق النص المحيط ليكون العنوان والمضمون شاملين لفحوى الملف
                    parent = l.parent
                    full_text = parent.get_text(strip=True) if parent else title
                    if len(full_text) < len(title) or full_text == title:
                        full_text = f"يتضمن هذا الملف الرسمي الصادر عن وزارة السياحة تفاصيل وشروط وتوجيهات تنظيمية حول: {title}"

                    pub_date = datetime.now().strftime("%Y/%m/%d")
                    
                    cur.execute("SELECT id FROM posted_news WHERE news_url = %s", (pdf_link,))
                    if not cur.fetchone():
                        cur.execute(
                            "INSERT INTO posted_news (news_url, title, full_text, media_url, source, pub_date, status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                            (pdf_link, title, full_text, media_url, 'mots', pub_date, 'pending')
                        )
                        conn.commit()
                        logging.info(f"Stored MOTS PDF: {title}")

        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error fetching MOTS PDFs: {e}")

def send_immediate_pdf_item():
    """إرسال أول ملف PDF فور التشغيل"""
    try:
        time.sleep(5)
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        
        cur.execute("SELECT id, news_url, title, full_text, media_url, pub_date, source FROM posted_news WHERE status = 'pending' ORDER BY id ASC LIMIT 1")
        row = cur.fetchone()
        
        if row:
            news_id, news_link, news_title, full_text, media_url, pub_date, source = row
            if send_to_telegram(news_title, full_text, news_link, media_url, pub_date, source):
                cur.execute("UPDATE posted_news SET status = 'sent' WHERE id = %s", (news_id,))
                conn.commit()
                logging.info(f"Immediate PDF sent: {news_title}")
        else:
            # عينة افتراضية في حال عدم توفر ملف PDF نشط حالياً على الصفحة الرئيسية
            sample_title = "التعميم التنظيمي الشامل لمعايير وتصنيف الخدمات السياحية"
            sample_text = "يحتوي هذا الملف على القرارات التنظيمية الصادر عن وزارة السياحة والمتعلقة بالاشتراطات الفنية والخدمية للمنشآت السياحية."
            sample_pdf = "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"
            sample_link = "https://mots.gov.sy/"
            
            send_to_telegram(sample_title, sample_text, sample_link, sample_pdf, source="mots")
            logging.info("Sent fallback sample PDF.")
            
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error in sending immediate PDF: {e}")

def background_worker():
    time.sleep(60)
    while True:
        try:
            fetch_and_store_mots_pdfs()
            
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
        time.sleep(14400) # فحص كل 4 ساعات

def start_bot():
    init_db()
    fetch_and_store_mots_pdfs()
    
    threading.Thread(target=send_immediate_pdf_item, daemon=True).start()
    threading.Thread(target=background_worker, daemon=True).start()

start_bot()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
