# تست زنده

نتیجهٔ زنده از pytest جداست. توکن، شناسهٔ افراد و متن پیام اینجا نوشته نمی‌شود.

## فاز یک — ۲۰۲۶-۱۱-۲۱

آفلاین، روی همین درخت:

- `uv run ruff check .` سبز
- `uv run ruff format --check .` سبز
- `uv run mypy src/peyda_pm_bot` سبز
- `uv run pytest` سبز، ۳۸ تست

زنده، با `.runtime/phase-one.db`:

اولین اجرا صف خالی بود و با کد ۲ برگشت: `accepted=0`، `checkpoint=0`. بعد یک پیام معمولی در تاپیک `pm` فرستاده شد و همان فرمان دوباره اجرا شد.

نتیجهٔ دوم: یک update جدید با `admission=accepted`، `accepted=1`، `ignored=0`، `replay_duplicates=yes`، `plain_message_seen=yes`. checkpoint یک واحد بعد از همان update است، یعنی ACK بعد از ذخیره رفته. بازپخش همان update رکورد تازه نساخته. متن پیام و توکن در این سند نیست.

`~/.local/bin/agy-cli` موجود است. `agy acp` اجرا نشد. `/usr/local/bin/agy` عوض نشد. پیام معمولی دیده شد، پس Privacy Mode این پیام را از بات پنهان نکرده است.
