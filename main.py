import os
import sqlite3
import threading
import time
import requests
from bs4 import BeautifulSoup
from flask import Flask
from telegram import Bot

# إعداد خادم Flask للحفاظ على تشغيل التطبيق (مناسب لمنصات الاستضافة مثل Render)
app = Flask(__name__)


@app.route("/")
def home():
  return "Syrian Tourism & SANA Bot is running successfully!"


def run_flask():
  port = int(os.environ.get("PORT", 5000))
  app.run(host="0.0.0.0", port=port)


# إعدادات بوت تيليجرام (يتم جلبها من متغيرات البيئة أو وضعهما مباشرة)
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@YOUR_CHANNEL")
bot = Bot(token=TOKEN)

# إعداد قاعدة بيانات SQLite لتخزين روابط الأخبار المرسلة سابقاً لتجنب التكرار
DB_NAME = "news.db"


def init_db():
  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()
  cursor.execute(
      """CREATE TABLE IF NOT EXISTS sent_news (
                    url TEXT PRIMARY KEY
                )"""
  )
  conn.commit()
  conn.close()


def is_news_sent(url):
  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()
  cursor.execute("SELECT 1 FROM sent_news WHERE url = ?", (url,))
  result = cursor.fetchone()
  conn.close()
  return result is not None


def mark_news_as_sent(url):
  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()
  cursor.execute("INSERT OR IGNORE INTO sent_news (url) VALUES (?)", (url,))
  conn.commit()
  conn.close()


# وظيفة سحب الأخبار من موقع سانا ووزارة السياحة
def scrape_and_send():
  # روابط المواقع المستهدفة (يمكنك تعديلها بناءً على الروابط الدقيقة للمصادر)
  sources = [
      {
          "name": "وكالة سانا (SANA)",
          "url": "http://sana.sy/",  # أو رابط القسم السياحي المخصص
          "parser": "sana",
      },
      {
          "name": "وزارة السياحة السورية",
          "url": "http://www.syrourism.sy/",  # رابط موقع الوزارة
          "parser": "tourism",
      },
  ]

  headers = {
      "User-Agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
      )
  }

  for source in sources:
    try:
      response = requests.get(
          source["url"], headers=headers, timeout=15
      )
      if response.status_code == 200:
        soup = BeautifulSoup(response.text, "html.parser")

        # تخصيص آلية البحث حسب هيكلية كل موقع (يمكن تعديل الوسوم بما يتوافق مع النصوص المدخلة سابقاً)
        # مثال افتراضي لجلب العناوين والروابط:
        articles = soup.find_all("a", href=True)

        for article in articles[:10]:  # فحص أحدث الروابط
          title = article.get_text(strip=True)
          link = article["href"]

          # تصفية الروابط للتأكد من أنها تخص الأخبار وليست روابط عامة
          if len(title) > 20 and link.startswith("http"):
            if not is_news_sent(link):
              message = (
                  f"📢 **خبر جديد من {source['name']}**\n\n"
                  f"📌 **{title}**\n\n"
                  f"🔗 [رابط الخبر]({link})"
              )

              # إرسال الرسالة إلى قناة تيليجرام
              # ملاحظة: يتم تشغيلها بشكل متوافق مع الدوال غير المتزامنة أو بالطريقة المباشرة
              bot.send_message(
                  chat_id=CHANNEL_ID, text=message, parse_mode="Markdown"
              )

              mark_news_as_sent(link)
              time.sleep(2)  # فاصل زمني لتجنب حظر الطلبات
    except Exception as e:
      print(f"Error scraping {source['name']}: {e}")


def worker_loop():
  init_db()
  while True:
    scrape_and_send()
    time.sleep(1800)  # الفحص كل نصف ساعة


if __name__ == "__main__":
  # تشغيل خادم Flask في خيط منفصل (Background Thread)
  flask_thread = threading.Thread(target=run_flask)
  flask_thread.daemon = True
  flask_thread.start()

  # بدء حلقة فحص الأخبار وتحديثها
  worker_loop()
