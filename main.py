import os
import requests

# جلب الإعدادات من متغيرات البيئة
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")

def send_test_pdf_to_telegram():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        print("Telegram credentials are missing!")
        return

    # بيانات تجريبية تحاكي تعاميم أو ملفات وزارة السياحة
    title = "تعميم تجريبي حول شروط ترخيص المنشآت السياحية"
    source_label = "وزارة السياحة السورية (التعاميم والأخبار)"
    source_tag = "#وزارة_السياحة"
    formatted_date = "2026/07/24"
    safe_text = "يحتوي هذا الملف على تفاصيل ومعايير التراخيص الجديدة والقرارات التنظيمية الصادرة عن وزارة السياحة السورية لتطوير الخدمات الفندقية."
    
    # رابط PDF تجريبي مباشر (أو رابط حقيقي من موقع الوزارة)
    media_url = "https://mots.gov.sy/uploads/sample_test.pdf" # يمكنك استبداله برابط PDF حقيقي من موقع الوزارة للمعاينة الدقيقة
    link = "https://mots.gov.sy/"

    # تنسيق شكل المنشور الذي طلبته تماماً
    caption = (
        f"عنوان الملف/المنشور: {title}\n"
        f"المصدر: {source_label}\n"
        f"تاريخ النشر: {formatted_date}\n\n"
        f"محتوى المنشور:\n{safe_text}\n\n"
        f"الرابط الرسمي:\n{link}\n\n"
        f"#السياحة_السورية {source_tag} #سوريا"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "document": media_url,
        "caption": caption
    }

    try:
        response = requests.post(url, json=payload, timeout=20)
        if response.status_code == 200:
            print("Test PDF sent successfully!")
        else:
            print(f"Failed to send PDF, Telegram response: {response.text}")
            # إذا لم يجد تليجرام الملف برابط الـ PDF التجريبي الوهمي، يمكنك تجربة إرساله كنص تجريبي للتأكد من الشكل
            print("Trying fallback text format...")
            text_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            requests.post(text_url, json={"chat_id": TELEGRAM_CHANNEL_ID, "text": caption, "disable_web_page_preview": True})
    except Exception as e:
        print(f"Error: {e}")

# تشغيل دالة المعاينة التجريبية فوراً
if __name__ == "__main__":
    send_test_pdf_to_telegram()
