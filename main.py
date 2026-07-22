import os
import time
import threading
import asyncio
import requests
import psycopg2
import urllib3
from bs4 import BeautifulSoup
from datetime import datetime
from telegram import Bot
from flask import Flask

# إخفاء تحذيرات شهادة الأمان (SSL) الخاصة بموقع الوزارة
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==========================================
# 1. إعدادات البيئة (Environment Variables)
# ==========================================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@YOUR_CHANNEL")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@host/dbname")
PORT = int(os.environ.get("PORT", 5000))
TARGET_URL = "https://mots.gov.sy/"

bot = Bot(token=TOKEN)
app = Flask(__name__)

# ==========================================
# 2. الكلمات المفتاحية للفلترة الذكية
# ==========================================
# إذا وجد البوت إحدى هذه الكلمات، سيعتبره "قراراً هاماً" وينسقه كأرشيف
IMPORTANT_KEYWORDS = [
    "ترخيص", "مكاتب", "سفر", "مفاضلة", "قانون", 
    "مرسوم", "تعميم", "قرار", "تعليمات", "المعهد"
]

# ==========================================
# 3. إدارة قاعدة البيانات (PostgreSQL)
# ==========================================
def init_db():
    """إنشاء الجدول إذا لم يكن موجوداً لحفظ روابط الأخبار المرسلة"""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS sent_tourism_news (
            url TEXT PRIMARY KEY
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()

def is_news_sent(url):
    """التحقق مما إذا كان الخبر قد تم إرساله مسبقاً"""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM sent_tourism_news WHERE url = %s", (url,))
    result = cur.fetchone()
    cur.close()
    conn.close()
    return result is not None

def mark_news_as_sent(url):
    """تسجيل رابط الخبر في قاعدة البيانات لمنع تكراره"""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("INSERT INTO sent_tourism_news (url) VALUES (%s) ON CONFLICT (url) DO NOTHING", (url,))
    conn.commit()
    cur.close()
    conn.close()

# ==========================================
# 4. التنسيق والإرسال إلى تيليجرام (Async)
# ==========================================
async def send_to_telegram(title, link, date_str, image_url):
    # الفلترة الذكية: فحص العنوان لمعرفة أهمية الخبر
    is_important = any(keyword in title for keyword in IMPORTANT_KEYWORDS)
    
    if is_important:
        header = "📜 <b>أرشيف التشريعات والقرارات السياحية</b>\n🏛️ <b>(هام لمكاتب السياحة والسفر والطلاب)</b>\n\n"
        tags = "#قوانين_السياحة #قرارات_رسمية #وزارة_السياحة"
    else:
        header = "🏛️ <b>وزارة السياحة السورية - تحديث جديد</b>\n\n"
        tags = "#أخبار_السياحة #سوريا"

    caption = (
        f"{header}"
        f"📌 <b>العنوان:</b> {title}\n"
        f"📅 <b>التاريخ:</b> {date_str}\n\n"
        f"🔗 <a href='{link}'>لقراءة التفاصيل والخبر كاملاً من الموقع الرسمي</a>\n\n"
        f"{tags}"
    )

    try:
        if image_url:
            await bot.send_photo(chat_id=CHANNEL_ID, photo=image_url, caption=caption, parse_mode="HTML")
        else:
            await bot.send_message(chat_id=CHANNEL_ID, text=caption, parse_mode="HTML", disable_web_page_preview=False)
        print(f"تم بنجاح إرسال: {title}")
    except Exception as e:
        print(f"خطأ أثناء الإرسال لتيليجرام: {e}")

# ==========================================
# 5. محرك سحب الأخبار (Web Scraper)
# ==========================================
def check_website_updates():
    print("جاري فحص موقع وزارة السياحة بحثاً عن مستجدات...")
    try:
        response = requests.get(TARGET_URL, verify=False, timeout=20)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # استهداف حاويات الأخبار الشائعة أو الروابط كبديل شامل
            news_containers = soup.find_all(['div', 'article'], class_=['news-item', 'post', 'card', 'item'])
            if not news_containers:
                news_containers = soup.find_all('a', href=True)

            for item in news_containers:
                if item.name == 'a':
                    link = item['href']
                    title = item.get_text(strip=True)
                    img_tag = item.find('img')
                    date_tag = None
                else:
                    a_tag = item.find('a')
                    if not a_tag: continue
                    link = a_tag.get('href', '')
                    title = a_tag.get_text(strip=True) or item.get_text(strip=True)
                    img_tag = item.find('img')
                    date_tag = item.find(class_=['date', 'time', 'post-date'])
                
                # تصفية الروابط العشوائية والقصيرة جداً
                if not link or len(title) < 15 or link.startswith('#') or 'javascript' in link:
                    continue
                
                # معالجة الروابط لتكون كاملة
                if link.startswith('/'):
                    link = "https://mots.gov.sy" + link
                elif not link.startswith('http'):
                    continue 

                date_str = date_tag.get_text(strip=True) if date_tag else datetime.now().strftime('%Y-%m-%d')
                
                # معالجة الصور
                image_url = None
                if img_tag and img_tag.get('src'):
                    img_src = img_tag['src']
                    image_url = f"https://mots.gov.sy{img_src}" if img_src.startswith('/') else img_src

                # فحص قاعدة البيانات وإرسال الجديد فقط
                if not is_news_sent(link):
                    asyncio.run(send_to_telegram(title, link, date_str, image_url))
                    mark_news_as_sent(link)
                    time.sleep(3) # فاصل زمني لتجنب حظر التيليجرام

        else:
            print(f"فشل الاتصال بموقع الوزارة. كود: {response.status_code}")
    except Exception as e:
        print(f"حدث خطأ أثناء فحص الموقع: {e}")

# ==========================================
# 6. حلقة التكرار والخادم المؤقت (Keep-Alive)
# ==========================================
def worker_loop():
    init_db()
    while True:
        check_website_updates()
        time.sleep(600) # إعادة الفحص كل 10 دقائق

@app.route("/")
def home():
    return "Syrian Tourism Bot is running beautifully and monitoring mots.gov.sy!"

def run_flask():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    # تشغيل خادم الويب في مسار خلفي
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # تشغيل مراقب الأخبار
    worker_loop()
