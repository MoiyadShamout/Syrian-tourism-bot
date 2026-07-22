from flask import Flask
import requests
from bs4 import BeautifulSoup
import sqlite3
import os
import threading
import time

# تعريف app يجب أن يكون في البداية وقبل أي استخدام لـ @app.route
app = Flask(__name__)

# إعداد قاعدة البيانات
def init_db():
    conn = sqlite3.connect('bot_database.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sent_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def send_telegram_message(text):
    bot_token = os.environ.get('8603025224:AAGcXyQw8MeTtUShx0e1uBg4AdKm1q7272w')
    channel_id = os.environ.get('-1004481182341')
    if bot_token and channel_id:
        telegram_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        try:
            requests.post(telegram_url, json={
                'chat_id': channel_id,
                'text': text,
                'parse_mode': 'Markdown'
            }, timeout=10)
        except Exception as e:
            print(f"Telegram error: {e}")

@app.route('/')
def home():
    test_message = "📢 **رسالة اختبار تفعيل البوت:**\n\nيعمل البوت بنجاح ومستعد لجلب ونشر الأخبار المهمة!"
    send_telegram_message(test_message)
    return "Syrian Tourism & SANA Bot: Direct test message sent to Telegram!"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
