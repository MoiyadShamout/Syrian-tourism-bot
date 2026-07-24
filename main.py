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

# إخفاء تحذيرات شهادات الأمان (مهم جداً لموقع وزارة السياحة)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# إعداد السجلات (Logging)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# إعداد تطبيق Flask لاستجابة UptimeRobot
app = Flask(__name__)

@app.route('/')
def home():
    return {"status": "active", "message": "Syrian Tourism (Sana & Mots) Bot is running smoothly!"}, 200

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

# دالة تنظيف النص وإزالة التكرارات المتتالية وكلمات الوكالة
def clean_text_content(text):
    if not text:
        return ""
    
    lines = text.split('\n')
    cleaned_lines = []
    seen_lines = set()
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        
        if stripped.endswith('-سانا') and len(stripped) < 30:
            continue
            
        if stripped not in seen_lines or len(stripped) > 50:
            cleaned_lines.append(stripped)
            seen_lines.add(stripped)
            
    return "\n\n".join(cleaned_lines)

# دالة إرسال المنشور إلى تليجرام (تدعم الصور وملفات PDF)
def send_to_telegram(title, full_text, link, media_url, pub_date="", source="sana"):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logging.error("Telegram credentials are missing!")
        return False
    
    # تحديد الهوية البصرية والمصدر بناءً على مصدر الخبر
    if source == "mots":
        source_label = "وزارة السياحة السورية (التعاميم والأخبار)"
        source_tag = "#وزارة_السياحة"
    else:
        source_label = "وكالة الأنباء السورية - سانا (قسم السياحة)"
        source_tag = "#وكالة_سانا"

    formatted_date = pub_date if pub_date else "غير محدد"
    safe_text = clean_text_content(full_text)
    if not safe_text:
        safe_text = "تفاصيل أو محتوى الملف متاحة عبر الرابط الرسمي أدناه."

    # تقصير النص ليناسب حد 1024 حرف الخاص بتليجرام للمرفقات (صور أو PDF)
    max_text_len = 500
    if len(safe_text) > max_text_len:
        safe_text = safe_text[:max_text_len] + "... [اقرأ المزيد في الرابط]"

    caption = (
        f"عنوان الملف/المنشور: {title}\n"
        f"المصدر: {source_label}\n"
        f"تاريخ النشر: {formatted_date}\n\n"
        f"محتوى المنشور:\n{safe_text}\n\n"
        f"الرابط الرسمي:\n{link}\n\n"
        f"#السياحة_السورية {source_tag} #سوريا"
    )

    try:
        session = get_robust_session()
        sent_successfully = False
        
        # 1. حالة ملف PDF: إرسال كوثيقة (Document)
        if media_url and media_url.lower().endswith('.pdf'):
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "document": media_url,
                "caption": caption
            }
            response = session.post(url, json=payload, timeout=20)
            if response.status_code == 200:
                sent_successfully = True
            else:
                logging.warning(f"PDF rejected by Telegram, falling back to text: {response.text}")

        # 2. حالة صورة عادية: إرسال كصورة (Photo)
        elif media_url and media_url.startswith('http'):
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "photo": media_url,
                "caption": caption
            }
            response = session.post(url, json=payload, timeout=20)
            if response.status_code == 200:
                sent_successfully = True
            else:
                logging.warning(f"Photo rejected by Telegram, falling back to text: {response.text}")

        # 3. حالة عدم وجود مرفق أو فشل المرفقات: إرسال كنص (Text)
        if not sent_successfully:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "text": caption,
                "disable_web_page_preview": True
            }
            response = session.post(url, json=payload, timeout=15)
            if response.status_code == 200:
                sent_successfully = True
            else:
                logging.error(f"Failed to send text message to Telegram: {response.text}")

        return sent_successfully
    except Exception as e:
        logging.error(f"Exception while sending to Telegram: {e}")
        return False

# ==================== قسم وكالة سانا ====================

def fetch_sana_article_details(article_url):
    try:
        session = get_robust_session()
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = session.get(article_url, headers=headers, timeout=20)
        pub_date = ""
        media_url = None
        
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            time_tag = soup.find('time') or soup.find('span', class_='date')
            if time_tag:
                pub_date = time_tag.get_text(strip=True)

            og_image = soup.find('meta', property='og:image')
            if og_image: media_url = og_image.get('content')
            if not media_url:
                main_img = soup.select_one('.single-post-thumb img')
                if main_img: media_url = main_img.get('src')
                    
            if media_url:
                if media_url.startswith('/'): media_url = "https://sana.sy" + media_url
                elif not media_url.startswith('http'): media_url = "https://sana.sy/" + media_url

            content_div = soup.find('div', class_='entry-content')
            if content_div:
                paragraphs = [p.get_text(strip=True) for p in content_div.find_all('p') if p.get_text(strip=True)]
                full_text = "\n\n".join(paragraphs) if paragraphs else content_div.get_text(strip=True)
            else:
                full_text = "تفاصيل الخبر متاحة عبر الرابط الرسمي أدناه."

            return full_text, media_url, pub_date
    except Exception as e:
        logging.error(f"Error fetching Sana details: {e}")
    return "تفاصيل الخبر متاحة عبر الرابط الرسمي أدناه.", None, ""

def fetch_and_store_sana_news():
    session = get_robust_session()
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        for sana_url in ["https://sana.sy/tourism/", "https://sana.sy/tourism/page/2/"]:
            response = session.get(sana_url, headers=headers, timeout=20)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                articles = soup.find_all('h3', class_='entry-title')
                for art in articles:
                    link_tag = art.find('a') if art.name != 'a' else art
                    if link_tag and link_tag.get('href'):
                        news_link, news_title = link_tag['href'], link_tag.get_text(strip=True)
                        if '/tourism/' not in news_link: continue
                        
                        cur.execute("SELECT id FROM posted_news WHERE news_url = %s", (news_link,))
                        if not cur.fetchone():
                            full_text, media_url, pub_date = fetch_sana_article_details(news_link)
                            if full_text:
                                cur.execute(
                                    "INSERT INTO posted_news (news_url, title, full_text, media_url, source, pub_date, status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                                    (news_link, news_title, full_text, media_url, 'sana', pub_date, 'pending')
                                )
                                conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error fetching Sana news: {e}")

# ==================== قسم وزارة السياحة ====================

def fetch_and_store_mots_news():
    session = get_robust_session()
    headers = {'User-Agent': 'Mozilla/5.0'}
    base_url = "https://mots.gov.sy/"
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        
        # استخدام verify=False لتخطي مشاكل الشهادات الخاصة بموقع الوزارة
        response = session.get(base_url, headers=headers, timeout=25, verify=False)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            # البحث عن الروابط في الموقع (سواء مقالات أو ملفات PDF مباشرة)
            articles = soup.find_all('a')
            for art in articles:
                href = art.get('href')
                news_title = art.get_text(strip=True)
                
                if not href or len(news_title) < 10: # تجاهل الروابط الفارغة أو التي بلا عنوان واضح
                    continue
                
                news_link = href if href.startswith('http') else base_url + href.lstrip('/')
                
                # فحص ما إذا كان الرابط هو ملف PDF للتعاميم
                media_url = news_link if news_link.lower().endswith('.pdf') else None
                full_text = "تفاصيل التعميم/الخبر تجدونها داخل الملف المرفق أو عبر الرابط." if media_url else "تفاصيل النشاط السياحي أو القرار متاحة عبر الرابط الرسمي لوزارة السياحة."
                pub_date = datetime.now().strftime("%Y/%m/%d") # نضع تاريخ اليوم كافتراضي لغياب هيكلية موحدة
                
                cur.execute("SELECT id FROM posted_news WHERE news_url = %s", (news_link,))
                if not cur.fetchone() and ('news' in news_link.lower() or '.pdf' in news_link.lower()):
                    cur.execute(
                        "INSERT INTO posted_news (news_url, title, full_text, media_url, source, pub_date, status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (news_link, news_title, full_text, media_url, 'mots', pub_date, 'pending')
                    )
                    conn.commit()
                    logging.info(f"Stored verified MOTS item: {news_title}")

        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error fetching MOTS news: {e}")

# ==================== مهام النشر المجدولة ====================

def send_immediate_sample_posts():
    try:
        time.sleep(10)
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        cur.execute("SELECT id, news_url, title, full_text, media_url, pub_date, source FROM posted_news WHERE status = 'pending' AND source = 'sana' ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        if row:
            news_id, news_link, news_title, full_text, media_url, pub_date, source = row
            if send_to_telegram(news_title, full_text, news_link, media_url, pub_date, source):
                cur.execute("UPDATE posted_news SET status = 'sent' WHERE id = %s", (news_id,))
                conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error in sending immediate sample: {e}")

def hourly_sana_publisher_worker():
    time.sleep(3600)
    while True:
        try:
            fetch_and_store_sana_news()
            conn = psycopg2.connect(DATABASE_URL, sslmode='require')
            cur = conn.cursor()
            cur.execute("SELECT id, news_url, title, full_text, media_url, pub_date, source FROM posted_news WHERE status = 'pending' AND source = 'sana' ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            if row:
                news_id, news_link, news_title, full_text, media_url, pub_date, source = row
                if send_to_telegram(news_title, full_text, news_link, media_url, pub_date, source):
                    cur.execute("UPDATE posted_news SET status = 'sent' WHERE id = %s", (news_id,))
                    conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logging.error(f"Error in scheduled Sana publisher: {e}")
        time.sleep(3600) # كل ساعة

def mots_publisher_worker():
    # يبدأ بعد دقيقتين من التشغيل لتفادي التعارض مع سانا الفوري
    time.sleep(120)
    while True:
        try:
            fetch_and_store_mots_news()
            conn = psycopg2.connect(DATABASE_URL, sslmode='require')
            cur = conn.cursor()
            # جلب أخبار أو ملفات وزارة السياحة
            cur.execute("SELECT id, news_url, title, full_text, media_url, pub_date, source FROM posted_news WHERE status = 'pending' AND source = 'mots' ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            if row:
                news_id, news_link, news_title, full_text, media_url, pub_date, source = row
                if send_to_telegram(news_title, full_text, news_link, media_url, pub_date, source):
                    cur.execute("UPDATE posted_news SET status = 'sent' WHERE id = %s", (news_id,))
                    conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logging.error(f"Error in scheduled MOTS publisher: {e}")
        
        # 3 منشورات يومياً تعني الانتظار 8 ساعات (28800 ثانية)
        time.sleep(28800) 

def start_background_tasks():
    init_db()
    fetch_and_store_sana_news()
    fetch_and_store_mots_news()
    
    threading.Thread(target=send_immediate_sample_posts, daemon=True).start()
    threading.Thread(target=hourly_sana_publisher_worker, daemon=True).start()
    threading.Thread(target=mots_publisher_worker, daemon=True).start()

start_background_tasks()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
