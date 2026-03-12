# Dasturchilar Choyxonasi Bot (Core Bot)

Bu loyiha Telegram guruhni ro'yxatdan o'tmagan userlardan himoya qiladi, FSM orqali ro'yxatdan o'tkazadi va admin reject mexanizmini bajaradi.

## Texnologiyalar

- Python + aiogram 3.x (async)
- PostgreSQL (asyncpg pool)
- Long polling (hozircha)

## Funktsiyalar

- Group moderation (`active` user yozadi, `rejected` yoki topilmagan user bloklanadi)
- Registration FSM:
  - Language
  - Phone (contact user_id check)
  - Name (validatsiya)
  - Age (14-70)
  - Field (variant + custom)
  - Experience
  - Purpose (spam/link check)
  - Confirmation
- Admin notification + reject callback
- `/set_active <telegram_id>` admin komandasi
- 10 daqiqa inactivity bo'lsa FSM reset
- 30 soniya group warning flood control
- Polling singleton himoyasi (ikkinchi instance PostgreSQL lock sabab ishga tushmaydi)
- Log rotation va event loglar

## O'rnatish

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

`.env.example` ni `.env` ga ko'chirib qiymatlarni to'ldiring:

- `BOT_TOKEN`
- `DATABASE_URL`
- `ADMIN_IDS`
- `BOT_USERNAME`

## Ishga tushirish

```bash
.venv\Scripts\python.exe main.py
```

## Arxitektura

- `app/config.py` - env sozlamalar
- `app/db.py` - pool va schema init
- `app/repositories/users.py` - DB so'rovlar (parameterized)
- `app/handlers/registration.py` - FSM va admin notification
- `app/handlers/group.py` - guruh nazorati
- `app/handlers/admin.py` - reject callback va status boshqaruvi
- `app/middlewares/fsm_timeout.py` - inactivity timeout
- `app/utils/*` - logging, matnlar, validatsiya, retry/delete helperlar

## Eslatma

- Webhook kerak bo'lsa keyingi bosqichda `runner`ga webhook rejimini qo'shamiz.
- Web admin panel keyingi bosqichda alohida modul sifatida qo'shiladi.
