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
from urllib.parse import urljoin, unquote

# إخفاء تحذيرات شهادات الأمان
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# إعداد السجلات
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

app = Flask(__name__)

@app.route('/')
def home():
    return {"status": "active", "message": "Syrian Tourism Bot V6 is running!"}, 200

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")
DATABASE_URL = os.environ.get("DATABASE_URL")

SOURCES = [
    {"name": "وزارة السياحة السورية", "type": "mots"},
    {"name": "وكالة الأنباء السورية - سانا (قسم السياحة)", "type": "sana"},
    {"name": "عنب بلدي", "type": "enab"},
    {"name": "تلفزيون سوريا", "type": "syriatv"},
    {"name": "سيرياستيبس", "type": "syriasteps"},
    {"name": "سيريان ديز", "type": "syriandays"},
    {"name": "جهينة نيوز", "type": "jpnews"},
    {"name": "جريدة الثورة", "type": "thawra"}
]

current_source_index = 0

def get_robust_session():
    session = requests.Session()
    retries = Retry(total=1, backoff_factor=1)
    session.mount('https://', HTTPAdapter(max_retries=retries))
    session.mount('http://', HTTPAdapter(max_retries=retries))
    return session

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'ar,en-US;q=0.7,en;q=0.3'
}

def init_db():
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        # قاعدة بيانات V6 لضمان سحب النصوص والصور للأخبار الجديدة
        cur.execute('''
            CREATE TABLE IF NOT EXISTS news_db_v6 (
                id SERIAL PRIMARY KEY,
                news_url TEXT UNIQUE,
                title TEXT,
                source TEXT DEFAULT '',
                pub_date TEXT DEFAULT '',
                full_text TEXT,
                media_url TEXT,
                status TEXT DEFAULT 'pending'
            )
        ''')
        conn.commit()
        cur.close()
        conn.close()
        logging.info("🚀 تم تهيئة قاعدة البيانات V6 بنجاح.")
    except Exception as e:
        logging.error(f"❌ خطأ في قاعدة البيانات: {e}")

def get_arabic_time():
    now = datetime.now()
    am_pm = "مساءً" if now.hour >= 12 else "صباحاً"
    hour = now.hour % 12
    if hour == 0: hour = 12
    return f"{now.strftime('%Y/%m/%d')} {hour:02d}:{now.strftime('%M')} {am_pm}"

def get_source_tags(source_name):
    if "سانا" in source_name: return "#السياحة_السورية #وكالة_سانا #سوريا"
    if "عنب بلدي" in source_name: return "#السياحة_السورية #عنب_بلدي #سوريا"
    if "تلفزيون سوريا" in source_name: return "#السياحة_السورية #تلفزيون_سوريا #سوريا"
    if "سيرياستيبس" in source_name: return "#السياحة_السورية #سيرياستيبس #سوريا"
    if "سيريان ديز" in source_name: return "#السياحة_السورية #سيريان_ديز #سوريا"
    if "جهينة نيوز" in source_name: return "#السياحة_السورية #جهينة_نيوز #سوريا"
    if "الثورة" in source_name: return "#السياحة_السورية #جريدة_الثورة #سوريا"
    if "وزارة السياحة" in source_name: return "#السياحة_السورية #وزارة_السياحة #سوريا"
    return "#السياحة_السورية #سوريا"

def extract_article_details(url):
    """دالة مخصصة لجلب صورة الخبر ونص المقال من داخل الرابط"""
    session = get_robust_session()
    try:
        resp = session.get(url, headers=HEADERS, timeout=10, verify=False)
        if resp.encoding is None or resp.encoding.lower() == 'iso-8859-1':
            resp.encoding = resp.apparent_encoding
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # محاولة جلب الصورة الرئيسية للخبر
            img_url = None
            og_image = soup.find('meta', property='og:image')
            if og_image and og_image.get('content'):
                img_url = og_image['content']
            
            # محاولة جلب نص الخبر (أول فقرتين)
            paragraphs = soup.find_all('p')
            full_text = ""
            for p in paragraphs:
                txt = p.get_text(strip=True)
                if len(txt) > 40: # تجاهل النصوص القصيرة مثل "الرئيسية"
                    full_text += txt + "\n\n"
                if len(full_text) > 350: # الاكتفاء بملخص للخبر كما في صورتك
                    break
            
            if len(full_text) > 450:
                full_text = full_text[:445] + "..."
                
            return img_url, full_text.strip()
    except Exception:
        pass
    return None, ""

def send_to_telegram(title, full_text, link, media_url, pub_date="", source=""):
    is_pdf = media_url and media_url.lower().endswith('.pdf')
    decoded_link = unquote(link)
    hashtags = get_source_tags(source)
    
    # بناء الرسالة بنفس التنسيق المطلوب حرفياً (بدون إيموجي أو زخرفة)
    caption = f"مصدر المنشور: {source}\n"
    caption += f"تاريخ النشر: {pub_date}\n\n"
    caption += f"{title}\n\n"
    
    if full_text:
        caption += f"{full_text}\n\n"
        
    caption += "يمكنكم متابعة تفاصيل الخبر رسمياً عبر الرابط أدناه:\n"
    caption += f"{decoded_link}\n\n"
    caption += hashtags

    try:
        session = get_robust_session()
        sent = False
        
        # إذا كان ملف PDF
        if is_pdf:
            res = session.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument", 
                               json={"chat_id": TELEGRAM_CHANNEL_ID, "document": media_url, "caption": caption}, timeout=15)
        # إذا كان هناك صورة للخبر
        elif media_url:
            res = session.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto", 
                               json={"chat_id": TELEGRAM_CHANNEL_ID, "photo": media_url, "caption": caption}, timeout=15)
            # إذا فشلت الصورة (لأن بعض المواقع تمنع تحميل صورها)، نرسل النص فقط
            if res.status_code != 200:
                res = session.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                                   json={"chat_id": TELEGRAM_CHANNEL_ID, "text": caption, "disable_web_page_preview": False}, timeout=15)
        # إذا لم تكن هناك صورة
        else:
            res = session.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                               json={"chat_id": TELEGRAM_CHANNEL_ID, "text": caption, "disable_web_page_preview": False}, timeout=15)

        if res and res.status_code == 200:
            logging.info(f"✅ تم الإرسال لتيليجرام: {title[:30]}")
            sent = True
        else:
            logging.error(f"❌ خطأ من تيليجرام: {res.text}")
        return sent
    except Exception as e:
        logging.error(f"❌ خطأ فادح في الإرسال: {e}")
        return False

def save_to_db(news_url, title, source_name, media_url=None, skip_extract=False):
    if not title or len(title) < 10: return False
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        cur.execute("SELECT id FROM news_db_v6 WHERE news_url = %s", (news_url,))
        if not cur.fetchone():
            # سحب الصورة والنص من داخل الخبر
            fetched_media, fetched_text = None, ""
            if not skip_extract:
                fetched_media, fetched_text = extract_article_details(news_url)
            
            final_media = fetched_media if fetched_media else media_url
            pub_date = get_arabic_time()
            
            cur.execute(
                "INSERT INTO news_db_v6 (news_url, title, full_text, media_url, source, pub_date, status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (news_url, title, fetched_text, final_media, source_name, pub_date, 'pending')
            )
            conn.commit()
            return True
        cur.close()
        conn.close()
    except Exception:
        pass
    return False

def fetch_mots_pdfs():
    session = get_robust_session()
    base_url = "https://mots.gov.sy/"
    try:
        resp = session.get(base_url, headers=HEADERS, timeout=15, verify=False)
        if resp.encoding is None or resp.encoding.lower() == 'iso-8859-1':
            resp.encoding = resp.apparent_encoding
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            count = 0
            for l in soup.find_all('a', href=True):
                href = l['href']
                if '.pdf' in href.lower() and count < 3:
                    pdf_link = urljoin(base_url, href)
                    title = l.get_text(strip=True) or "وثيقة رسمية من وزارة السياحة"
                    # ملفات PDF لا تحتاج إلى سحب نصوص من داخلها
                    if save_to_db(pdf_link, title, "وزارة السياحة السورية", media_url=pdf_link, skip_extract=True): count += 1
    except Exception as e:
        logging.warning(f"⚠️ خطأ في وزارة السياحة: {str(e)[:50]}")

def fetch_general_source(name, url, selector_tag='a'):
    session = get_robust_session()
    try:
        resp = session.get(url, headers=HEADERS, timeout=15, verify=False)
        if resp.encoding is None or resp.encoding.lower() == 'iso-8859-1':
            resp.encoding = resp.apparent_encoding
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            count = 0
            for item in soup.find_all(selector_tag, href=True):
                if count >= 3: break
                title = item.get_text(strip=True)
                link = item['href']
                if len(title) > 25 and link and link != '/' and not link.startswith('#'):
                    full_link = urljoin(url, link)
                    if save_to_db(full_link, title, name): count += 1
    except Exception as e:
        logging.error(f"⚠️ خطأ في {name}: {str(e)[:50]}")

def background_worker():
    global current_source_index
    while True:
        try:
            active = SOURCES[current_source_index]
            stype = active["type"]
            sname = active["name"]
            
            if stype == 'mots': fetch_mots_pdfs()
            elif stype == 'sana': fetch_general_source(sname, "https://sana.sy/tourism/")
            elif stype == 'enab': fetch_general_source(sname, "https://www.enabbaladi.net/category/mix/tourism/")
            elif stype == 'syriatv': fetch_general_source(sname, "https://www.syria.tv/tag/السياحة")
            elif stype == 'syriasteps': fetch_general_source(sname, "https://www.syriasteps.com/index.php?m=154")
            elif stype == 'syriandays': fetch_general_source(sname, "https://www.syriandays.com/index.php?page=show&select_page=52")
            elif stype == 'jpnews': fetch_general_source(sname, "https://jpnews-sy.com/ar/cats.php?subcat=31")
            elif stype == 'thawra': fetch_general_source(sname, "https://thawra.sy/")

            current_source_index = (current_source_index + 1) % len(SOURCES)

            # إرسال المعلق لتيليجرام
            conn = psycopg2.connect(DATABASE_URL, sslmode='require')
            cur = conn.cursor()
            cur.execute("SELECT id, news_url, title, full_text, media_url, pub_date, source FROM news_db_v6 WHERE status = 'pending' ORDER BY id ASC LIMIT 5")
            rows = cur.fetchall()
            for r in rows:
                nid, nlink, ntitle, ntext, nmedia, ndate, nsource = r
                if send_to_telegram(ntitle, ntext, nlink, nmedia, ndate, nsource):
                    cur.execute("UPDATE news_db_v6 SET status = 'sent' WHERE id = %s", (nid,))
                    conn.commit()
                    time.sleep(2)
            cur.close()
            conn.close()
        except Exception as e:
            logging.error(f"Worker error: {e}")
        
        time.sleep(30)

def start_bot():
    init_db()
    threading.Thread(target=background_worker, daemon=True).start()

start_bot()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
