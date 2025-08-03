# 🐋 Whale Watcher Bot

یک ربات تلگرام برای نظارت بر وضعیت لانگ و شورت بازار کریپتو با استفاده از API سایت CoinGlass.

## ویژگی‌ها:
- ارسال پیام هر ساعت درباره موقعیت نهنگ‌ها (لانگ یا شورت)
- استفاده از Flask برای اجرا روی سرویس‌هایی مثل Render
- بدون نیاز به پایگاه داده یا فایل اضافی

## راه‌اندازی:

1. کلون پروژه:
   ```bash
   git clone https://github.com/username/whale-watcher-bot
   cd whale-watcher-bot
   ```

2. نصب وابستگی‌ها:
   ```bash
   pip install -r requirements.txt
   ```

3. اجرا:
   ```bash
   python main.py
   ```

## محیط‌های لازم:
در فایل `.env` یا به صورت Environment Variables تعریف شود:
- `BOT_TOKEN` = توکن ربات تلگرام شما
- `COINGLASS_API_KEY` = کلید API سایت CoinGlass

---

## مجوز
استفاده آزاد برای اهداف آموزشی و شخصی.
