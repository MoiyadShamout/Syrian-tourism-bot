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
import re

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

app = Flask(__name__)

@app.route('/')
def home():
    return {"status": "active", "message": "Syrian Tourism Bot V13 is running with full image extraction and max Telegram text length!"}, 200

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")
DATABASE_URL = os.environ.get("DATABASE_URL")

SOURCES = [
    {"name": "وكالة الأنباء السورية - سانا (قسم السياحة)", "type": "sana", "url": "https://sana.sy/tourism/"},
    {"name": "وزارة السياحة السورية", "type": "mots", "url": "https://mots.gov.sy/"},
    {"name": "عنب بلدي", "type": "enab", "url": "https://www.enabbaladi.net/category/mix/tourism/"},
    {"name": "تلفزيون سوريا", "type": "syriatv", "url": "https://www.syria.tv/tag/السياحة"},
    {"name": "سيرياستيبس", "type": "syriasteps", "url": "https://www.syriasteps.com/index.php?m=154"},
    {"name": "سيريان ديز", "type": "syriandays", "url": "https://www.syriandays.com/index.php?page=show&select_page=52"},
    {"name": "جهينة نيوز", "type": "jpnews", "url": "https://jpnews-sy.com/ar/cats.php?subcat=31"},
    {"name": "جريدة الثورة", "type": "thawra", "url": "https://thawra.sy/"}
]

current_source_index = 0

TOURISM_KEYWORDS = [
    "سياحة", "سياحي", "سياحية", "آثار", "أثري", "فندق", "فنادق", "منتجع", "منتجعات", 
    "طيران", "مطار", "رحلة", "رحلات", "سفر", "مسافرين", "شاطئ", "شواطئ", "معالم", 
    "ترفيه", "قلعة", "قلاع", "وزير السياحة", "وزارة السياحة",
    "tourism", "tourist", "travel", "hotel", "resort", "airline", "airport", "trip", "destination", "heritage", "antiquity"
]

def is_tourism_related(text):
    if not text:
        return False
    text_lower = text.lower()
    for kw in TOURISM_KEYWORDS:
        if kw in text_lower:
            return True
    return False

def get_robust_session():
    session = requests.Session()
    retries = Retry(total=1, backoff_factor=1)
    session.mount('https://', HTTPAdapter(max_retries=retries))
    session.mount('http://', HTTPAdapter(max_retries=retries))
    return session

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'ar,en-US;q=0.7,en;q=0.3'
}

def init_db():
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS news_db_v13 (
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
        logging.info("🚀 تم تهيئة قاعدة البيانات V13 بنجاح.")
    except Exception as e:
        logging.error(f"❌ خطأ في قاعدة البيانات: {e}")

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
    session = get_robust_session()
    img_url, full_text, pub_date = None, "", ""
    try:
        resp = session.get(url, headers=HEADERS, timeout=12, verify=False)
        if resp.encoding is None or resp.encoding.lower() == 'iso-8859-1':
            resp.encoding = resp.apparent_encoding
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # جلب الصورة الأصلية بدقة عالية وبحث متعدد في وسوم الصفحة
            og_image = soup.find('meta', property='og:image') or soup.find('meta', attrs={'name': 'twitter:image'}) or soup.find('link', rel='image_src')
            if og_image:
                img_url = og_image.get('content') or og_image.get('href')
            
            if not img_url:
                # البحث عن أول صورة بارزة داخل محتوى المقال
                article_body = soup.find('article') or soup.find('div', class_=['post-content', 'content', 'entry-content', 'article-body', 'details-body'])
                if article_body:
                    img_tag = article_body.find('img')
                else:
                    img_tag = soup.find('img')
                
                if img_tag:
                    img_url = img_tag.get('src') or img_tag.get('data-src')

            if img_url and img_url.startswith('/'):
                img_url = urljoin(url, img_url)

            # استخراج التاريخ الأصلي
            time_tag = soup.find('time') or soup.find(class_=['date', 'post-date', 'publish-date', 'time', 'article-date', 'published'])
            if time_tag:
                pub_date = time_tag.get_text(strip=True)
            else:
                page_text_all = soup.get_text()
                date_match = re.search(r'\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b|\b\d{1,2}[-/]\d{1,2}[-/]\d{4}\b', page_text_all)
                if date_match:
                    pub_date = date_match.group(0)

            # سحب النص بالحد الأقصى المسموح به في تيليجرام (نطاق واسع يغطي الفقرات الأساسية)
            paragraphs = soup.find_all('p')
            for p in paragraphs:
                txt = p.get_text(strip=True)
                if len(txt) > 30 and "حقوق النشر" not in txt and "جميع الحقوق" not in txt:
                    full_text += txt + "\n\n"
                if len(full_text) > 850: # إبقاء المساحة متبقية للعنوان والرابط ضمن سقف 1024 حرف
                    break
            if len(full_text) > 900:
                full_text = full_text[:895] + "..."
                
    except Exception as e:
        logging.error(f"Error extracting details from {url}: {e}")
    
    if not pub_date or len(pub_date.strip()) < 3:
        pub_date = "لم يتم تحديد التاريخ"

    return img_url, full_text.strip(), pub_date.strip()

def send_to_telegram(title, full_text, link, media_url, pub_date="", source=""):
    decoded_link = unquote(link)
    hashtags = get_source_tags(source)
    
    # القالب الرسمي المعتمد
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
        is_pdf = media_url and media_url.lower().endswith('.pdf')
        
        if is_pdf:
            res = session.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument", 
                               json={"chat_id": TELEGRAM_CHANNEL_ID, "document": media_url, "caption": caption}, timeout=15)
        elif media_url:
            # محاولة إرسال الصورة الأصلية مع النص
            res = session.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto", 
                               json={"chat_id": TELEGRAM_CHANNEL_ID, "photo": media_url, "caption": caption}, timeout=15)
            # لو فشل رابط الصورة لأي سبب فني، يتم إرسال الخبر كنص مع المعاينة لضمان عدم ضياع النشر
            if res.status_code != 200:
                res = session.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                                   json={"chat_id": TELEGRAM_CHANNEL_ID, "text": caption, "disable_web_page_preview": False}, timeout=15)
        else:
            res = session.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                               json={"chat_id": TELEGRAM_CHANNEL_ID, "text": caption, "disable_web_page_preview": False}, timeout=15)

        if res and res.status_code == 200:
            logging.info(f"✅ تم الإرسال بنجاح مع الصورة والنص الكامل: {title[:30]}")
            sent = True
        return sent
    except Exception as e:
        logging.error(f"❌ خطأ في الإرسال: {e}")
        return False

def save_to_db(news_url, title, source_name, media_url=None, skip_extract=False):
    if not title or len(title) < 10: return False
    
    if not is_tourism_related(title):
        return False

    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        cur.execute("SELECT id FROM news_db_v13 WHERE news_url = %s", (news_url,))
        if not cur.fetchone():
            fetched_media, fetched_text, fetched_date = None, "", ""
            if not skip_extract:
                fetched_media, fetched_text, fetched_date = extract_article_details(news_url)
            
            final_media = fetched_media if fetched_media else media_url
            final_date = fetched_date if fetched_date else "لم يتم تحديد التاريخ"
            
            cur.execute(
                "INSERT INTO news_db_v13 (news_url, title, full_text, media_url, source, pub_date, status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (news_url, title, fetched_text, final_media, source_name, final_date, 'pending')
            )
            conn.commit()
            return True
        cur.close()
        conn.close()
    except Exception:
        pass
    return False

def fetch_source_news(source_info):
    name = source_info["name"]
    url = source_info["url"]
    stype = source_info["type"]
    session = get_robust_session()
    
    try:
        resp = session.get(url, headers=HEADERS, timeout=15, verify=False)
        if resp.encoding is None or resp.encoding.lower() == 'iso-8859-1':
            resp.encoding = resp.apparent_encoding
            
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            count = 0
            
            if stype == 'mots':
                for l in soup.find_all('a', href=True):
                    href = l['href']
                    if '.pdf' in href.lower() and count < 2:
                        pdf_link = urljoin(url, href)
                        title = l.get_text(strip=True) or "وثيقة رسمية من وزارة السياحة"
                        if save_to_db(pdf_link, title, name, media_url=pdf_link, skip_extract=True): count += 1
            elif stype == 'sana':
                for item in soup.find_all('h3', class_='story-title'):
                    if count >= 2: break
                    a_tag = item.find('a', href=True)
                    if a_tag:
                        title = a_tag.get_text(strip=True)
                        link = a_tag['href']
                        if save_to_db(link, title, name): count += 1
            else:
                for item in soup.find_all('a', href=True):
                    if count >= 2: break
                    title = item.get_text(strip=True)
                    link = item['href']
                    if len(title) > 25 and link and link != '/' and not link.startswith('#'):
                        full_link = urljoin(url, link)
                        if save_to_db(full_link, title, name): count += 1
                            
    except Exception as e:
        logging.error(f"⚠️ خطأ في جلب مصدر {name}: {str(e)[:50]}")

def process_pending_news():
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        cur.execute("SELECT id, news_url, title, full_text, media_url, pub_date, source FROM news_db_v13 WHERE status = 'pending' ORDER BY id ASC LIMIT 1")
        row = cur.fetchone()
        if row:
            nid, nlink, ntitle, ntext, nmedia, ndate, nsource = row
            if send_to_telegram(ntitle, ntext, nlink, nmedia, ndate, nsource):
                cur.execute("UPDATE news_db_v13 SET status = 'sent' WHERE id = %s", (nid,))
                conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error processing pending: {e}")

def background_worker():
    global current_source_index
    
    logging.info("🚀 بدء التنفيذ الفوري لمعاينة أول خبر بالصورة والنص الكامل...")
    active = SOURCES[current_source_index]
    fetch_source_news(active)
    process_pending_news()
    current_source_index = (current_source_index + 1) % len(SOURCES)

    while True:
        try:
            active = SOURCES[current_source_index]
            logging.info(f"🔎 جاري فحص المصدر: {active['name']} ...")
            fetch_source_news(active)

            current_source_index = (current_source_index + 1) % len(SOURCES)

            process_pending_news()
            
        except Exception as e:
            logging.error(f"Worker error: {e}")
        
        logging.info("⏳ الانتظار لمدة 30 دقيقة للفحص القادم...")
        time.sleep(1800)

def start_bot():
    init_db()
    threading.Thread(target=background_worker, daemon=True).start()

start_bot()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
