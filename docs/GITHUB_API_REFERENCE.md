# مرجع GitHub فاز چهار

تاریخ بررسی: ۲۰۲۶-۱۱-۱۸. این سند قرارداد پیاده‌سازی و تست آفلاین است، نه تأیید اتصال واقعی حساب.

## مسیر فعال میمیر

در بازسازی میمیر، سرویس Issue می‌سازد و مشخصات Issue و PR را می‌خواند. فاز پنج برای درخواست ریویو از `pulls/{number}`، `pulls/{number}/files` و `commits/{head}/check-runs` استفاده می‌کند. برای کامنت معمولی روی PR، مسیر `issues/{number}/comments` خوانده یا با درخواست صریح تیم نوشته می‌شود. یافته‌ها به‌صورت review رسمی GitHub یا درخواست تغییر ثبت نمی‌شوند.
آداپتر فعال از aiohttp، مهلت ۳۰ ثانیه و هدر نسخهٔ `2022-11-28` استفاده می‌کند.
شروع سرویس مقصد صریح و هویت حساب را بررسی می‌کند. رسید نامعلوم با خواندن حداکثر
پنج صفحهٔ صدتایی و یافتن یک marker یکتا از همان نویسنده تطبیق داده می‌شود.
خروجی جست‌وجوی ناقص یا حذف marker، مجوز ارسال مجدد نیست. بین آخرین بررسی محلی
و دریافت درخواست در GitHub امکان تغییر بیرونی وجود دارد.
جزئیات بخش‌های پایین مربوط به طراحی قبلی‌اند. شواهد مسیر فعال در
[گزارش آزمون](LIVE_TESTING.md) ثبت شده‌اند.
[GitHub Issues](https://docs.github.com/en/rest/issues/issues) و
[GitHub Pull Requests](https://docs.github.com/en/rest/pulls/pulls).

## نسخه و credential در طراحی قبلی

REST header انتخاب‌شده `X-GitHub-Api-Version: 2026-03-10` است؛ در [مرجع رسمی نسخه‌ها](https://docs.github.com/en/rest/about-the-rest-api/api-versions) پشتیبانی می‌شود. HTTP client از `Accept: application/vnd.github+json`، timeout سی ثانیه و توکن تنظیم‌شده در پردازش اصلی استفاده می‌کند. redirect خودکار غیرفعال است.

نسخه‌های خوانده‌شده از محیط و قفل پروژه: httpx **0.28.1**، MCP **1.30.0**، ACP **1.0.0**، uvicorn **0.54.0** و aiogram **3.32.0**. ادعای آخرین نسخه دائمی نیست؛ ارتقا نیازمند بررسی و تست دوباره است.

روش credential این نسخه fine-grained PAT برای یک مخزن است؛ پیش از نخستین mutation، `GET /user` و login تنظیم‌شده تطبیق داده می‌شوند. برای Issues write، PR read و Contents read در فاز شش سطح دسترسی دقیق بررسی شود. پشتیبانی GitHub App installation authentication در این پیاده‌سازی ادعا نشده است.

## نگاشت endpointها

| قابلیت | روش و مسیر زیر `/repos/{owner}/{repo}` | محدودیت |
|---|---|---|
| فهرست/خواندن ایشو | `GET issues`، `GET issues/{number}` | پاسخ Issues می‌تواند PR باشد؛ نوع برای نوشتن بررسی می‌شود |
| ساخت/اصلاح | `POST issues`، `PATCH issues/{number}` | فقط فیلدهای whitelist؛ marker عملیات در body |
| کامنت | `GET/POST issues/{number}/comments` | POST فقط روی issue؛ PR نوشتنی نیست |
| خواندن/اصلاح کامنت | `GET/PATCH issues/comments/{id}` | مقصد issue و author حساب تنظیم‌شده بررسی می‌شوند |
| labels و افراد | `GET labels`، `GET assignees` | نبود label یا login از نتیجه ناقص حدس زده نمی‌شود |
| PR metadata | `GET pulls`، `GET pulls/{number}` | انتشار review یا merge وجود ندارد |
| مسیرهای PR | `GET pulls/{number}/files` | فقط filename و status به ابزار برمی‌گردند؛ patch حذف می‌شود |
| snapshot کد | repository metadata، `GET commits/{default_branch}`، `GET git/trees/{sha}`، `GET git/blobs/{sha}` | SHA کامل ثابت در نوبت، path policy و سقف اندازه؛ بدون تغییر working tree |

منابع رسمی: [Issues](https://docs.github.com/en/rest/issues/issues)، [Issue comments](https://docs.github.com/en/rest/issues/comments)، [Repository contents و ارجاع به recursive tree](https://docs.github.com/en/rest/repos/contents). API محتوای فایل می‌تواند symlink را دنبال کند؛ به همین دلیل پیاده‌سازی قبل از خواندن blob، mode/type مسیر در tree را بررسی می‌کند.

## خطا، نرخ و تطبیق

- connect error/timeout پیش از اتصال، موقت و قابل retry است. read/write transport error پس از احتمال ارسال mutation و HTTP 5xx در نوشتن unknown هستند.
- 403 عادی auth/permission یک شکست قطعی است؛ 403/429 دارای نشانگر محدودیت نرخ با retry-after یا reset deadline رسیدگی می‌شود. deadline و فاصله write در SQLite باقی می‌مانند.
- GET با ETag و If-None-Match cache می‌شود؛ 304 فقط از cache معتبر همان URL استفاده می‌کند. این ETag به معنای CAS برای PATCH نیست.
- حداکثر پنج صفحه صدتایی خوانده می‌شود. complete=false و محدودیت پاسخ، فقدان نتیجه را ثابت نمی‌کنند. هیچ index/search lag به‌عنوان اثبات اجرا نشدن POST استفاده نمی‌شود.
- تطبیق نتیجه unknown فقط خواندن است؛ marker حذف‌شده، پاسخ نامعتبر، چند تطبیق یا اسکن ناتمام خودکار resend ایجاد نمی‌کند.
- موفقیت 2xx با حذف خاموش فیلدهای درخواستی، Partial result گزارش می‌شود؛ issue ایجادشده دوباره ساخته نمی‌شود.

مبنای نرخ و رفتار REST: [راهنمای رسمی best practices](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api). محدودیت رقابت GET/PATCH و نبود exactly-once در [راهنمای فاز چهار](PHASE_FOUR.md) توضیح داده شده است.

MCP گیت‌هاب فقط روی loopback در همان پردازش مالک credential اجرا می‌شود. در ACP، `mcp_capabilities.http` باید اعلام شود؛ در غیر این صورت اتصال خطای مشخص می‌دهد و هیچ fallback با انتقال PAT به ایجنت انجام نمی‌شود. این قرارداد از SDK نصب‌شده بررسی و با peer آزمایشی تست شده؛ قابلیت واقعی CLI در فاز شش بررسی خواهد شد.
