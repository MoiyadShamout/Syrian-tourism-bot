@app.route('/')
def home():
    # إرسال رسالة اختبار فورية ومباشرة للتأكد من وصول الإشعارات لقناتك على تيليجرام
    test_message = "📢 **رسالة اختبار تفعيل البوت:**\n\nيعمل البوت بنجاح ومستعد لجلب ونشر الأخبار المهمة!"
    send_telegram_message(test_message)
    
    return "Syrian Tourism & SANA Bot: Direct test message sent to Telegram!"
