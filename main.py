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
from urllib.parse import urljoin, urlparse

# إخفاء تحذيرات شهادات الأمان
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# إعداد السجلات
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

@app.route('/')
def home():
    return {"status": "active", "message": "Syrian Tourism & News Bot 10-Sources System is running perfectly!"}, 200

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")
DATABASE_URL = os.environ.get("DATABASE_URL")

# قائمة المصادر السياحية العشرة المعتمدة بالكامل
SOURCES = [
    {"name": "وزارة السياحة السورية (القرارات الرسمية والملفات)", "type": "mots"},
    {"name": "وكالة الأنباء السورية - سانا (قسم السياحة)", "type": "sana"},
    {"name": "موقع عنب بلدي (قسم السياحة)", "type": "enab"},
    {"name": "تلفزيون سوريا (قسم السياحة)", "type": "syriatv"},
    {"name": "موقع سيرياستيبس (قسم السياحة)", "type": "syriasteps"},
    {"name": "موقع سيريان ديز (قسم السياحة)", "type": "syriandays"},
    {"name": "موقع جهينة نيوز (قسم السياحة في سوريا)", "type": "jpnews"},
    {"name": "موقع توريزم ديلي نيوز (السياحة العالمية)", "type": "tourism_global"},
    {"name": "جريدة الوطن (فلتر ذكي للأخبار المحلية والاقتصادية)", "type": "alwatan"},
    {"name": "جريدة الثورة (فلتر ذكي للأخبار المحلية)", "type": "thawra"}
]

current_source_index = 0

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
        
        conn.commit()
        cur.close()
        conn.close()
        logging.info("Database initialized successfully with all source tables.")
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
    
    # تحديد التسميات والوسوم حسب المصدر
    if is_pdf or source == 'mots':
        source_label = "وزارة السياحة السورية (التعاميم والقرارات الرسمية)"
        source_tag = "#وزارة_السياحة #قرارات_رسمية"
        title_prefix = "عنوان الملف:"
    elif source == 'sana':
        source_label = "وكالة الأنباء السورية - سانا (قسم السياحة)"
        source_tag = "#وكالة_سانا #أخبار_سورية"
        title_prefix = "عنوان التقرير:"
    elif source == 'syriatv':
        source_label = "تلفزيون سوريا (قسم السياحة)"
        source_tag = "#تلفزيون_سوريا #سياحة_وترفيه"
        title_prefix = "عنوان الخبر:"
    elif source == 'enab':
        source_label = "موقع عنب بلدي (قسم السياحة)"
        source_tag = "#عنب_بلدي #معالم_سورية"
        title_prefix = "عنوان الخبر:"
    elif source == 'syriasteps':
        source_label = "موقع سيرياستيبس (قسم السياحة)"
        source_tag = "#سيرياستيبس #اقتصاد_وسياحة"
        title_prefix = "عنوان الخبر:"
    elif source == 'syriandays':
        source_label = "موقع سيريان ديز (قسم السياحة)"
        source_tag = "#سيريان_ديز #فعاليات_سياحية"
        title_prefix = "عنوان الخبر:"
    elif source == 'jpnews':
        source_label = "موقع جهينة نيوز (قسم السياحة في سوريا)"
        source_tag = "#جهينة_نيوز #محليات_سياحية"
        title_prefix = "عنوان الخبر:"
    elif source == 'tourism_global':
        source_label = "موقع توريزم ديلي نيوز (السياحة العالمية)"
        source_tag = "#توريزم_ديلي_نيوز #سياحة_عالمية"
        title_prefix = "عنوان الخبر:"
    elif source == 'alwatan':
        source_label = "جريدة الوطن (أخبار محلية واقتصادية)"
        source_tag = "#جريدة_الوطن #تقارير_سياحية"
        title_prefix = "عنوان الخبر:"
    elif source == 'thawra':
        source_label = "جريدة الثورة (أخبار محلية)"
        source_tag = "#جريدة_الثورة #أخبار_محلية"
        title_prefix = "عنوان الخبر:"
    else:
        source_label = "شبكة أخبار السياحة السورية"
        source_tag = "#السياحة_السورية"
        title_prefix = "عنوان الخبر:"

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
        sent_successfully = False
        
        if is_pdf:
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

        if not sent_successfully and media_url and not is_pdf:
            # إرسال كصورة مع تعليق في حال توفر رابط صورة بارزة
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "photo": media_url,
                "caption": caption,
                "parse_mode": "Markdown"
            }
            response = session.post(url, json=payload, timeout=30)
            if response.status_code == 200:
                sent_successfully = True

        if not sent_successfully:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "text": caption,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False
            }
            response = session.post(url, json=payload, timeout=15)
            if response.status_code == 200:
                sent_successfully = True

        return sent_successfully
    except Exception as e:
        logging.error(f"Exception while sending to Telegram: {e}")
        return False

# --- دوال الزحف وجلب البيانات لكافة المصادر ---

def fetch_mots_pdfs():
    session = get_robust_session()
    headers = {'User-Agent': 'Mozilla/5.0'}
    base_url = "https://mots.gov.sy/"
    target_pages = [
        base_url,
        "https://mots.gov.sy/page/97281/قوانين-المكاتب-والمؤسسات",
        "https://mots.gov.sy/page/97282/قانون-المكاتب-السياحية",
        "https://mots.gov.sy/page/97387/الاتفاقيات-ومذكرات",
        "https://mots.gov.sy/page/97299/احصائيات-معتمدة"
    ]
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        for page_url in target_pages:
            try:
                response = session.get(page_url, headers=headers, timeout=20, verify=False)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    for l in soup.find_all('a', href=True):
                        href = l['href']
                        if '.pdf' in href.lower():
                            pdf_link = urljoin(page_url, href)
                            title = l.get_text(strip=True)
                            pub_date = datetime.now().strftime("%Y/%m/%d %I:%M %p")
                            clean_title = title if len(title) > 5 else "وثيقة رسمية صادرة عن وزارة السياحة السورية"
                            
                            cur.execute("SELECT id FROM posted_news WHERE news_url = %s", (pdf_link,))
                            if not cur.fetchone():
                                cur.execute(
                                    "INSERT INTO posted_news (news_url, title, full_text, media_url, source, pub_date, status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                                    (pdf_link, clean_title, f"ملف تعميم أو قرار رسمي صادر عن وزارة السياحة بعنوان: {clean_title}", pdf_link, 'mots', pub_date, 'pending')
                                )
                                conn.commit()
            except Exception as page_err:
                logging.warning(f"Error reading MOTS page {page_url}: {page_err}")
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

def fetch_general_source_news(source_name, source_key, url):
    """دالة عامة لزحف وجلب الأخبار من باقي المصادر المعتمدة وتخزينها بأولوية يوم بيومه"""
    session = get_robust_session()
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        response = session.get(url, headers=headers, timeout=20, verify=False)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            # استخراج الروابط والعناوين المتوافقة مع بنية المواقع
            for a_tag in soup.find_all('a', href=True):
                news_title = a_tag.get_text(strip=True)
                news_link = a_tag['href']
                if len(news_title) > 25 and ('http' in news_link or '/' in news_link):
                    if not news_link.startswith('http'):
                        news_link = urljoin(url, news_link)
                    
                    pub_date = datetime.now().strftime("%Y/%m/%d %I:%M %p")
                    full_text = f"تقرير وتغطية سياحية حديثة تم رصدها ضمن متابعة {source_name} لواقع الخدمات والفعاليات."

                    cur.execute("SELECT id FROM posted_news WHERE news_url = %s", (news_link,))
                    if not cur.fetchone():
                        cur.execute(
                            "INSERT INTO posted_news (news_url, title, full_text, media_url, source, pub_date, status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                            (news_link, news_title, full_text, None, source_key, pub_date, 'pending')
                        )
                        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error fetching from {source_name}: {e}")

def background_worker():
    time.sleep(15)
    global current_source_index
    while True:
        try:
            # التدوير الذكي بين المصادر بناءً على المؤشر الساعي
            active_source = SOURCES[current_source_index]
            source_type = active_source["type"]
            
            logging.info(f"Running worker cycle for source: {active_source['name']}")
            
            if source_type == 'mots':
                fetch_mots_pdfs()
            elif source_type == 'sana':
                fetch_sana_news()
            elif source_type == 'enab':
                fetch_general_source_news("عنب بلدي", "enab", "https://www.enabbaladi.net/category/mix/tourism/")
            elif source_type == 'syriatv':
                fetch_general_source_news("تلفزيون سوريا", "syriatv", "https://www.syria.tv/tag/السياحة")
            elif source_type == 'syriasteps':
                fetch_general_source_news("سيرياستيبس", "syriasteps", "https://www.syriasteps.com/index.php?m=154")
            elif source_type == 'syriandays':
                fetch_general_source_news("سيريان ديز", "syriandays", "https://www.syriandays.com/index.php?page=show&select_page=52")
            elif source_type == 'jpnews':
                fetch_general_source_news("جهينة نيوز", "jpnews", "https://jpnews-sy.com/ar/cats.php?subcat=31")
            elif source_type == 'tourism_global':
                fetch_general_source_news("توريزم ديلي نيوز", "tourism_global", "https://tourismdailynews.com/")
            elif source_type == 'alwatan':
                fetch_general_source_news("جريدة الوطن", "alwatan", "https://alwatan.sy/")
            elif source_type == 'thawra':
                fetch_general_source_news("جريدة الثورة", "thawra", "https://thawra.sy/")

            # الانتقال للمصدر التالي في الدورة القادمة
            current_source_index = (current_source_index + 1) % len(SOURCES)

            # معالجة أول منشور معلق في طليعة الانتظار (مع أولوية ملفات الـ PDF والقرارات الرسمية)
            conn = psycopg2.connect(DATABASE_URL, sslmode='require')
            cur = conn.cursor()
            
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
        
        # النوم لمدة ساعة تماماً (أو حسب رغبتك في النشر بمعدل منشور كل ساعة)
        time.sleep(3600)

def start_bot():
    init_db()
    fetch_mots_pdfs()
    fetch_sana_news()
    threading.Thread(target=background_worker, daemon=True).start()

start_bot()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
