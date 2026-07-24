# -*- coding: utf-8 -*-
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
from urllib.parse import urljoin

# إخفاء تحذيرات شهادات الأمان
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# إعداد السجلات
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

@app.route('/')
def home():
    return {"status": "active", "message": "Syrian Tourism Test Scraper Bot is running!"}, 200

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")
DATABASE_URL = os.environ.get("DATABASE_URL")

# قائمة المصادر المعتمدة
SOURCES = [
    {"name": "وزارة السياحة السورية (القرارات الرسمية والملفات)", "type": "mots"},
    {"name": "وكالة الأنباء السورية - سانا (قسم السياحة)", "type": "sana"},
    {"name": "موقع عنب بلدي (قسم السياحة)", "type": "enab"},
    {"name": "تلفزيون سوريا (قسم السياحة)", "type": "syriatv"},
    {"name": "موقع سيرياستيبس (قسم السياحة)", "type": "syriasteps"},
    {"name": "موقع سيريان ديز (قسم السياحة)", "type": "syriandays"},
    {"name": "موقع جهينة نيوز (قسم السياحة في سوريا)", "type": "jpnews"},
    {"name": "موقع توريزم ديلي نيوز (السياحة العالمية)", "type": "tourism_global"},
    {"name": "جريدة الوطن (أخبار محلية واقتصادية)", "type": "alwatan"},
    {"name": "جريدة الثورة (أخبار محلية)", "type": "thawra"}
]

current_source_index = 0

def get_robust_session():
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    session.mount('https://', HTTPAdapter(max_retries=retries))
    session.mount('http://', HTTPAdapter(max_retries=retries))
    return session

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ar,en-US;q=0.7,en;q=0.3'
}

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
    
    labels = {
        'mots': ("وزارة السياحة السورية (القرارات الرسمية والملفات)", "#وزارة_السياحة #قرارات_رسمية", "عنوان الملف:"),
        'sana': ("وكالة الأنباء السورية - سانا", "#وكالة_سانا #أخبار_سورية", "عنوان التقرير:"),
        'enab': ("موقع عنب بلدي (قسم السياحة)", "#عنب_بلدي #معالم_سورية", "عنوان الخبر:"),
        'syriatv': ("تلفزيون سوريا (قسم السياحة)", "#تلفزيون_سوريا #سياحة_وترفيه", "عنوان الخبر:"),
        'syriasteps': ("موقع سيرياستيبس (قسم السياحة)", "#سيرياستيبس #اقتصاد_وسياحة", "عنوان الخبر:"),
        'syriandays': ("موقع سيريان ديز (قسم السياحة)", "#سيريان_ديز #فعاليات_سياحية", "عنوان الخبر:"),
        'jpnews': ("موقع جهينة نيوز", "#جهينة_نيوز #محليات_سياحية", "عنوان الخبر:"),
        'tourism_global': ("موقع توريزم ديلي نيوز", "#توريزم_ديلي_نيوز #سياحة_عالمية", "عنوان الخبر:"),
        'alwatan': ("جريدة الوطن", "#جريدة_الوطن #تقارير_سياحية", "عنوان الخبر:"),
        'thawra': ("جريدة الثورة", "#جريدة_الثورة #أخبار_محلية", "عنوان الخبر:")
    }
    
    source_label, source_tag, title_prefix = labels.get(source, ("شبكة أخبار السياحة السورية", "#السياحة_السورية", "عنوان الخبر:"))
    if is_pdf:
        source_label = "وزارة السياحة السورية (التعاميم والقرارات الرسمية)"
        source_tag = "#وزارة_السياحة #قرارات_رسمية"
        title_prefix = "عنوان الملف:"

    formatted_date = pub_date if pub_date else datetime.now().strftime("%Y/%m/%d %I:%M %p")
    safe_text = clean_text_content(full_text)
    if not safe_text:
        safe_text = "التفاصيل الكاملة والتقارير الخدمية متوفرة عبر الرابط الرسمي أدناه."

    if len(safe_text) > 700:
        safe_text = safe_text[:700] + "..."

    caption = (
        f"🏛 **مصدر المنشور:** {source_label}\n"
        f"📅 **تاريخ النشر:** {formatted_date}\n\n"
        f"📌 **{title_prefix}**\n{title}\n\n"
        f"📝 **تفاصيل الخبر:**\n{safe_text}\n\n"
        f"🔗 **رابط المتابعة:**\n{link}\n\n"
        f"#السياحة_السورية {source_tag} #سوريا"
    )

    try:
        session = get_robust_session()
        sent = False
        if is_pdf:
            res = session.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument", json={"chat_id": TELEGRAM_CHANNEL_ID, "document": media_url, "caption": caption, "parse_mode": "Markdown"}, timeout=30)
            if res.status_code == 200: sent = True
        
        if not sent and media_url and not is_pdf:
            res = session.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto", json={"chat_id": TELEGRAM_CHANNEL_ID, "photo": media_url, "caption": caption, "parse_mode": "Markdown"}, timeout=30)
            if res.status_code == 200: sent = True

        if not sent:
            res = session.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHANNEL_ID, "text": caption, "parse_mode": "Markdown", "disable_web_page_preview": False}, timeout=15)
            if res.status_code == 200: sent = True

        return sent
    except Exception as e:
        logging.error(f"Telegram send error: {e}")
        return False

def save_to_db(news_url, title, full_text, source_key, media_url=None):
    if not news_url or not title: return
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        cur.execute("SELECT id FROM posted_news WHERE news_url = %s", (news_url,))
        if not cur.fetchone():
            pub_date = datetime.now().strftime("%Y/%m/%d %I:%M %p")
            cur.execute(
                "INSERT INTO posted_news (news_url, title, full_text, media_url, source, pub_date, status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (news_url, title, full_text, media_url, source_key, pub_date, 'pending')
            )
            conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"DB save error: {e}")

# --- دوال جلب مرنة تلتقط أحدث المتاح حالياً ---

def fetch_mots_pdfs():
    session = get_robust_session()
    base_url = "https://mots.gov.sy/"
    try:
        resp = session.get(base_url, headers=HEADERS, timeout=15, verify=False)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            count = 0
            for l in soup.find_all('a', href=True):
                href = l['href']
                if '.pdf' in href.lower() and count < 3:
                    pdf_link = urljoin(base_url, href)
                    title = l.get_text(strip=True) or "وثيقة رسمية من وزارة السياحة"
                    save_to_db(pdf_link, title, f"ملف تعميم أو قرار رسمي صادر عن وزارة السياحة بعنوان: {title}", 'mots', pdf_link)
                    count += 1
    except Exception as e:
        logging.warning(f"MOTS error: {e}")

def fetch_flexible_source(name, key, url):
    session = get_robust_session()
    try:
        resp = session.get(url, headers=HEADERS, timeout=15, verify=False)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            count = 0
            for a in soup.find_all('a', href=True):
                title = a.get_text(strip=True)
                link = a['href']
                if len(title) > 20 and count < 3:
                    full_link = urljoin(url, link)
                    save_to_db(full_link, title, f"تغطية ومتابعة من {name} لواقع الخدمات والفعاليات.", key)
                    count += 1
    except Exception as e:
        logging.error(f"{name} error: {e}")

def background_worker():
    time.sleep(10)
    global current_source_index
    while True:
        try:
            active = SOURCES[current_source_index]
            stype = active["type"]
            logging.info(f"Running flexible fetcher for: {active['name']}")
            
            if stype == 'mots': fetch_mots_pdfs()
            elif stype == 'sana': fetch_flexible_source("وكالة سانا", "sana", "https://sana.sy/tourism/")
            elif stype == 'enab': fetch_flexible_source("عنب بلدي", "enab", "https://www.enabbaladi.net/category/mix/tourism/")
            elif stype == 'syriatv': fetch_flexible_source("تلفزيون سوريا", "syriatv", "https://www.syria.tv/tag/السياحة")
            elif stype == 'syriasteps': fetch_flexible_source("سيرياستيبس", "syriasteps", "https://www.syriasteps.com/index.php?m=154")
            elif stype == 'syriandays': fetch_flexible_source("سيريان ديز", "syriandays", "https://www.syriandays.com/index.php?page=show&select_page=52")
            elif stype == 'jpnews': fetch_flexible_source("جهينة نيوز", "jpnews", "https://jpnews-sy.com/ar/cats.php?subcat=31")
            elif stype == 'tourism_global': fetch_flexible_source("توريزم ديلي نيوز", "tourism_global", "https://tourismdailynews.com/")
            elif stype == 'alwatan': fetch_flexible_source("جريدة الوطن", "alwatan", "https://alwatan.sy/")
            elif stype == 'thawra': fetch_flexible_source("جريدة الثورة", "thawra", "https://thawra.sy/")

            current_source_index = (current_source_index + 1) % len(SOURCES)

            # معالجة الأخبار المعلقة ونشرها فوراً
            conn = psycopg2.connect(DATABASE_URL, sslmode='require')
            cur = conn.cursor()
            cur.execute("SELECT id, news_url, title, full_text, media_url, pub_date, source FROM posted_news WHERE status = 'pending' ORDER BY id ASC LIMIT 5")
            rows = cur.fetchall()
            for r in rows:
                nid, nlink, ntitle, ntext, nmedia, ndate, nsource = r
                if send_to_telegram(ntitle, ntext, nlink, nmedia, ndate, nsource):
                    cur.execute("UPDATE posted_news SET status = 'sent' WHERE id = %s", (nid,))
                    conn.commit()
                    time.sleep(2)
            cur.close()
            conn.close()
        except Exception as e:
            logging.error(f"Worker cycle error: {e}")
        
        # دورة قصيرة جداً (دقيقة واحدة) للاختبار الفوري
        time.sleep(60)

def start_bot():
    init_db()
    fetch_mots_pdfs()
    fetch_flexible_source("وكالة سانا", "sana", "https://sana.sy/tourism/")
    threading.Thread(target=background_worker, daemon=True).start()

start_bot()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
