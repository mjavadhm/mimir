# مرجع GitHub فاز چهار

تاریخ مرجع API: ۲۰۲۶-۱۱-۱۸؛ بازنگری محدوده: ۲۰۲۶-۱۱-۲۱. این سند طراحی مقصد adapter فاز چهار است؛ در درخت فعلی adapter گیت‌هاب پیاده نشده و اتصال واقعی حساب تأیید نشده است. قراردادها پیش از پیاده‌سازی دوباره با مستندات رسمی و dependencyهای انتخاب‌شده تطبیق داده می‌شوند.

## نسخه و credential

REST header طرح مرجع `X-GitHub-Api-Version: 2026-03-10` است؛ پیش از پیاده‌سازی با [مرجع رسمی نسخه‌ها](https://docs.github.com/en/rest/about-the-rest-api/api-versions) تطبیق داده شود. HTTP client پیشنهادی از `Accept: application/vnd.github+json`، timeout سی ثانیه و توکن اختصاصی adapter در پردازش اصلی استفاده می‌کند؛ redirect خودکار غیرفعال خواهد بود.

در وابستگی‌های فعلی پروژه، کتابخانه‌های HTTP/ACP/MCP موردنیاز این adapter هنوز انتخاب و قفل نشده‌اند. نسخه‌های یک محیط آزمایشی مدرک dependency درخت فعلی نیستند. انتخاب آن‌ها هنگام تحویل مربوط انجام می‌شود؛ aiogram فعلی **3.32.0** است.

روش credential پیشنهادی ابزار بات fine-grained PAT برای یک مخزن است؛ پیش از نخستین mutation، هویت حساب و binding مخزن تطبیق داده می‌شوند. رفتار endpoint هویت و سطح Issues write، PR read و Contents read با نوع token انتخاب‌شده در فاز چهار بررسی شود. GitHub App installation authentication هنوز طراحی/پیاده نشده است.

این سند فقط ابزارهای افزودهٔ گیت‌هاب بات را محدود می‌کند. shell، git، `gh` و احراز هویت معمول پروفایل Antigravity باقی می‌مانند؛ credential اختصاصی adapter به آن‌ها تزریق نمی‌شود. عملیات native زیر تضمین dedup، تراکنش یا reconciliation این adapter نیستند. ایجنت نباید نتیجهٔ مجهول را با رفتن به مسیر دیگر دوباره اجرا کند؛ نخست وضعیت واقعی را بررسی می‌کند.

prepare و submit ابزار بات جدا هستند. submit تا پایان موفق همان نوبت staged است و پاسخ آن موفقیت ساخت ایشو نیست. نتیجهٔ executor یک رویداد پایدار در همان نشست می‌سازد؛ ایجنت در نوبت بعد می‌تواند لینک واقعی را با ابزار تلگرام بفرستد. ابزار تلگرام برخلاف submit گیت‌هاب در میانهٔ نوبت اجرا می‌شود و شکست آن، عملیات گیت‌هاب را تکرار نمی‌کند.

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

مبنای نرخ و رفتار REST: [راهنمای رسمی best practices](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api).

در طراحی ابزار گیت‌هاب، credential در پردازش اصلی می‌ماند. transport افزوده باید علاوه بر binding پروژه و نوبت، احراز هویت داشته باشد؛ loopback به‌تنهایی کافی نیست. HTTP یا stdio و اعلان capability با نسخهٔ واقعی SDK/CLI در فاز دو تأیید می‌شوند. استثنای نسخه از نوشتهٔ قدیمی یا mock موفق به اجرای فعلی تعمیم داده نمی‌شود و fallback با انتقال PAT به ایجنت نداریم. تست API و حساب واقعی گیت‌هاب در فاز چهار انجام می‌شود.
