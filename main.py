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
    return {"status": "active", "message": "Syrian Tourism & Ministry Alternating Bot is running smoothly!"}, 200

# جلب الإعدادات من متغيرات البيئة
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")
DATABASE_URL = os.environ.get("DATABASE_URL")

# دالة الاتصال بقاعدة البيانات وإعداد الجداول مع تحديد مصدر الخبر
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
                status TEXT DEFAULT 'pending'
            )
        ''')
        cur.execute("ALTER TABLE posted_news ADD COLUMN IF NOT EXISTS full_text TEXT;")
        cur.execute("ALTER TABLE posted_news ADD COLUMN IF NOT EXISTS media_url TEXT;")
        cur.execute("ALTER TABLE posted_news ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'sana';")
        
        # حذف أي روابط قديمة تحتوي على /en/ لضمان عدم ظهور أي محتوى إنجليزي
        cur.execute("DELETE FROM posted_news WHERE news_url LIKE '%/en/%';")
        
        conn.commit()
        cur.close()
        conn.close()
        logging.info("Database initialized with source-tracking successfully.")
    except Exception as e:
        logging.error(f"Error initializing database: {e}")

# دالة إرسال المقال إلى تليجرام باللغة العربية مع العبارات الموثوقة والحد الأقصى المسموح
def send_to_telegram(title, full_text, link, media_url, source_name="sana", is_urgent=False):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logging.error("Telegram credentials are missing!")
        return False
    
    current_hour = datetime.now().hour
    
    if is_urgent:
        icon = "🚨"
        category_tag = "#عاجل #تعميم_رسمي"
        header = "عاجل | تحديث رسمي جديد"
    elif 9 <= current_hour < 12:
        icon = "☀️"
        category_tag = "#النشاطات_السياحية #قطاع_تعليمي #وزارة_السياحة"
        header = "نشاطات السياحة والقطاع الأكاديمي"
    elif 12 <= current_hour < 16:
        icon = "⚖️"
        category_tag = "#قرارات_رسمية #مكاتب_السفر #قوانين_السفر"
        header = "تحديثات القرارات وقوانين المكاتب"
    elif 16 <= current_hour < 21:
        icon = "🌇"
        category_tag = "#معالم_سياحية #سياحة_سورية #اثار_سوريا #دليل_السفر"
        header = "دليل السياحة السورية | محطة مسائية"
    else:
        icon = "🌙✨"
        category_tag = "#استثمار_سياحي #مشاريع_سورية"
        header = "أفق الاستثمار والمشاريع السياحية"

    # تخصيص الوسم بناءً على المصدر الرسمي
    source_tag = "#وزارة_السياحة_السورية" if source_name == "ministry" else "#وكالة_سانا"

    safe_text = full_text if full_text else "يمكنكم متابعة تفاصيل الخبر رسمياً عبر الرابط أدناه."
    
    caption = (
        f"{icon} {header}\n\n"
        f"📌 {title}\n\n"
        f"{safe_text[:700]}...\n\n"
        f"للمزيد أضغط على الرابط في الأسفل\n"
        f"🔗 {link}\n\n"
        f"{category_tag} {source_tag} #سوريا"
    )

    try:
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

        response = requests.post(url, json=payload)
        if response.status_code == 200:
            logging.info(f"Post from [{source_name}] sent to Telegram successfully.")
            return True
        else:
            logging.error(f"Failed to send to Telegram: {response.text}")
            return False
    except Exception as e:
        logging.error(f"Exception while sending to Telegram: {e}")
        return False

# دالة جلب النص الكامل لمقال سانا
def fetch_sana_article_details(article_url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(article_url, headers=headers, timeout=10)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            content_div = soup.find('div', class_='entry-content') or soup.find('div', class_='post-content') or soup.find('div', class_='single-content')
            
            if content_div:
                paragraphs = content_div.find_all('p')
                full_text = "\n".join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])
                if not full_text:
                    full_text = content_div.get_text(strip=True)
            else:
                full_text = "يمكنكم متابعة تفاصيل الخبر رسمياً عبر الرابط أدناه."

            img_tag = soup.find('img', class_='wp-post-image') or (content_div.find('img') if content_div else None)
            media_url = img_tag.get('src') if img_tag else None
            return full_text, media_url
    except Exception as e:
        logging.error(f"Error fetching Sana article details from {article_url}: {e}")
    return "يمكنكم متابعة تفاصيل الخبر رسمياً عبر الرابط أدناه.", None

# دالة جلب النص الكامل لمقال موقع وزارة السياحة
def fetch_ministry_article_details(article_url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(article_url, headers=headers, timeout=10)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            content_div = soup.find('div', class_='content') or soup.find('article') or soup.find('main')
            
            if content_div:
                paragraphs = content_div.find_all('p')
                full_text = "\n".join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])
                if not full_text:
                    full_text = content_div.get_text(strip=True)
            else:
                full_text = "يمكنكم متابعة تفاصيل الخبر رسمياً عبر الرابط أدناه."

            img_tag = content_div.find('img') if content_div else soup.find('img')
            media_url = img_tag.get('src') if img_tag else None
            if media_url and not media_url.startswith('http'):
                media_url = "https://mots.gov.sy" + media_url
            return full_text, media_url
    except Exception as e:
        logging.error(f"Error fetching Ministry article details from {article_url}: {e}")
    return "يمكنكم متابعة تفاصيل الخبر رسمياً عبر الرابط أدناه.", None

# دالة جلب وتخزين الأخبار من المصدرين بشكل منفصل لضمان التناوب والأولوية
def fetch_and_store_news():
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    cur = conn.cursor()
    headers = {'User-Agent': 'Mozilla/5.0'}

    # 1. جلب أحدث أخبار سانا
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
                        full_text, media_url = fetch_sana_article_details(news_link)
                        cur.execute(
                            "INSERT INTO posted_news (news_url, title, full_text, media_url, source, status) VALUES (%s, %s, %s, %s, %s, %s)",
                            (news_link, news_title, full_text, media_url, 'sana', 'pending')
                        )
                        conn.commit()
    except Exception as e:
        logging.error(f"Error fetching Sana news: {e}")

    # 2. جلب أحدث أخبار موقع وزارة السياحة
    try:
        ministry_url = "https://mots.gov.sy/"
        response = requests.get(ministry_url, headers=headers, timeout=15)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            links = soup.find_all('a', href=True)
            
            for tag in links[:10]:
                news_link = tag['href']
                news_title = tag.get_text(strip=True)
                
                if ('mots.gov.sy' in news_link or news_link.startswith('/')) and len(news_title) > 15:
                    if news_link.startswith('/'):
                        news_link = "https://mots.gov.sy" + news_link
                        
                    cur.execute("SELECT id FROM posted_news WHERE news_url = %s", (news_link,))
                    if not cur.fetchone():
                        full_text, media_url = fetch_ministry_article_details(news_link)
                        cur.execute(
                            "INSERT INTO posted_news (news_url, title, full_text, media_url, source, status) VALUES (%s, %s, %s, %s, %s, %s)",
                            (news_link, news_title, full_text, media_url, 'ministry', 'pending')
                        )
                        conn.commit()
    except Exception as e:
        logging.error(f"Error fetching Ministry news: {e}")

    cur.close()
    conn.close()

# دالة نشر فورية لخبر سانا وتليها فورية لموقع الوزارة لضمان رؤية منشورات فورية للموقعين
def send_immediate_sample_posts():
    try:
        time.sleep(5)
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        
        # نشر أول خبر من سانا (إن وجد معلقاً)
        cur.execute("SELECT id, news_url, title, full_text, media_url, source FROM posted_news WHERE status = 'pending' AND source = 'sana' ORDER BY id ASC LIMIT 1")
        row_sana = cur.fetchone()
        if row_sana:
            news_id, news_link, news_title, full_text, media_url, source_name = row_sana
            if send_to_telegram(news_title, full_text, news_link, media_url, source_name, is_urgent=False):
                cur.execute("UPDATE posted_news SET status = 'sent' WHERE id = %s", (news_id,))
                conn.commit()

        # نشر أول خبر من وزارة السياحة (إن وجد معلقاً) لضمان الحصول على منشور فوري من كلا المصدرين
        cur.execute("SELECT id, news_url, title, full_text, media_url, source FROM posted_news WHERE status = 'pending' AND source = 'ministry' ORDER BY id ASC LIMIT 1")
        row_min = cur.fetchone()
        if row_min:
            news_id, news_link, news_title, full_text, media_url, source_name = row_min
            if send_to_telegram(news_title, full_text, news_link, media_url, source_name, is_urgent=False):
                cur.execute("UPDATE posted_news SET status = 'sent' WHERE id = %s", (news_id,))
                conn.commit()

        cur.close()
        conn.close()
        logging.info("Immediate sample posts for both sources evaluated.")
    except Exception as e:
        logging.error(f"Error sending immediate sample posts: {e}")

# عامل النشر الدوري كل نصف ساعة (1800 ثانية) بالتناوب بين المصدرين مع أولوية الأحدث
def alternating_publisher_worker():
    last_source = 'ministry'  # لبدء التناوب بشكل صحيح
    while True:
        try:
            # الانتظار لمدة 30 دقيقة بين المنشور والآخر
            time.sleep(1800)
            
            conn = psycopg2.connect(DATABASE_URL, sslmode='require')
            cur = conn.cursor()
            
            # التناوب الذكي: إذا كان المنشور السابق من سانا، نبحث الآن عن وزارة السياحة والعكس صحيح، مع أخذ الأحدث
            next_source = 'ministry' if last_source == 'sana' else 'sana'
            
            cur.execute("SELECT id, news_url, title, full_text, media_url, source FROM posted_news WHERE status = 'pending' AND source = %s ORDER BY id ASC LIMIT 1", (next_source,))
            row = cur.fetchone()
            
            # إذا لم يُوجد خبر من المصدر المناوب، نبحث في المصدر الآخر كخيار احتياطي لضمان عدم توقف النشر
            if not row:
                next_source = 'sana' if next_source == 'ministry' else 'ministry'
                cur.execute("SELECT id, news_url, title, full_text, media_url, source FROM posted_news WHERE status = 'pending' AND source = %s ORDER BY id ASC LIMIT 1", (next_source,))
                row = cur.fetchone()
            
            if row:
                news_id, news_link, news_title, full_text, media_url, source_name = row
                if send_to_telegram(news_title, full_text, news_link, media_url, source_name, is_urgent=False):
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
        time.sleep(900)  # تحديث البيانات كل 15 دقيقة للتأكد من جلب أي قرار أو خبر جديد فور صدوره

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
