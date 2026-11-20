# تحویل فاز چهار — GitHub و هماهنگی کار

تاریخ: ۲۰۲۶-۱۱-۱۸. ابزارهای مدیریت ایشو، executor پایدار و projection کار پیاده شده‌اند. آزمون‌ها از GitHub آزمایشی، HTTP MockTransport و MCP محلی استفاده می‌کنند. ورود حساب، اتصال GitHub و Antigravity واقعی همچنان در فاز شش هستند.

## اجرا و بررسی

از ریشه checkout:

```bash
source .venv/bin/activate
uv sync --locked --group dev
uv run python -m peyda_pm_bot --phase-four --database /tmp/peyda-github-demo.db
uv run ruff check .
uv run ruff format --check .
uv run mypy src/peyda_pm_bot
uv run pytest
```

برای دموی اولیه، دیتابیس تازه و جدا از فازهای دیگر استفاده کنید. دمو برنامه‌ریزی‌شده است: پرونده می‌سازد، کار مشابه را می‌خواند، ایجاد ایشو را ثبت می‌کند، پاسخ موفق گم‌شده را با marker پیدا می‌کند و بدون ایجاد ایشوی دوم وضعیت کار را به in_progress می‌برد. یک issue و دو mutation آزمایشی حاصل می‌شود؛ اعلان‌ها فقط به FakeTransport تحویل می‌شوند.

`Application` اکنون `github` و `executor` اختیاری دارد. در اجراهای دارای GitHub، از `runtime.runner.run(..., agent_worker=app.worker, action_executor=app.executor)` در context برنامه استفاده شود. executable همچنان فقط دمو آفلاین دارد.

## محدوده ابزارها

MCP گیت‌هاب در پردازش اصلی برنامه با Streamable HTTP روی `127.0.0.1` و پورت آزاد اجرا می‌شود؛ credential در همان پردازش می‌ماند. ایجنت فقط آدرس سرور و grant نوبت را می‌گیرد. ابزارهای حافظه stdio باقی مانده‌اند و از همان grant استفاده می‌کنند.

| ابزار | کارکرد |
|---|---|
| `github_read` | issues، issue، comments، labels، assignees، members، pulls، pull، pull_paths، tree و file |
| `github_prepare` | آماده‌سازی نسخه جدید پیش‌نویس بدون mutation خارجی |
| `github_submit` | درخواست اجرای دقیقاً همان revision پس از موفقیت نوبت |
| `github_action` | مشاهده وضعیت یا لغو عملیات قابل لغو |
| `work_status` | projection کارها با مسئول، وابستگی، موعد، اولویت، case links و زمان مشاهده |

grant از قرارداد مشترک `authorize_turn` کنترل می‌شود. جدول تاریخی `memory_turns` برای حفظ داده و FKها نام قبلی را نگه داشته است؛ `SqlTurns` تنها پیاده‌سازی begin/authorize است و مسیر قدیمی حافظه به آن تفویض می‌کند. grant، نشست یا سامانه مجوز موازی ساخته نشده است.

عملیات مجاز:

- create_issue: عنوان، بدنه، assignees، labels و وضعیت اولیه باز. برای ساخت closed/done ابتدا ایشوی باز بسازید و در عملیات بعدی ببندید.
- update_issue: فیلدهای مشخص متن، مسئول، labels و state؛ `work_status` نگاشت هماهنگ label/status را اعمال می‌کند.
- add_comment: افزودن کامنت به ایشوی مشخص.
- edit_comment: اصلاح کامنت با author برابر حساب GitHub تنظیم‌شده؛ مقصد باید همان issue باشد. اگر از PAT شخصی استفاده شود، این معیار هویت همان حساب است؛ تشخیص جداگانه کامنت دستی و کامنت نوشته‌شده با همان حساب ادعا نمی‌شود.

حذف دائمی، merge، review، push، تغییر فایل، تنظیمات مخزن و ساخت label جدید ابزار ندارند. labels باید از قبل موجود باشند؛ labels پیش‌فرض را هنگام تنظیم واقعی فاز شش فراهم کنید. شماره متعلق به PR حتی در endpoint مشترک Issues برای نوشتن رد می‌شود.

## پیش‌نویس و ثبت اجرا

نمونه ساخت یک پیش‌نویس:

```json
{
  "turn_token": "current-turn-grant",
  "operation_key": "login-task-1",
  "spec": {
    "kind": "create_issue",
    "source_update_ids": [10],
    "case_ids": ["existing-case-id"],
    "title": "Login flow",
    "body": "Problem: ...\nScope: ...\nAcceptance criteria: ...",
    "assignees": ["alice"],
    "work_status": "ready"
  }
}
```

ابزار id و revision می‌دهد. برای اصلاح پیش‌نویس، action_id و expected_revision فعلی به prepare داده شوند. برای اجرا، `github_submit(turn_token, action_id, revision)` فراخوانی شود. پیش‌نویس draft پس از پایان نوبت هم قابل نگهداری و submit در نوبت بعد است.

- آماده‌سازی و submit هیچ درخواست نوشتن GitHub نمی‌فرستند. worker در همان تراکنش حافظه، نتیجه job، انتشار نسخه‌های action و outbox را commit می‌کند.
- شکست یا لغو نوبت، پیش‌نویس staged را قابل اجرا نمی‌کند. grant بازنشسته یا متعلق به پروژه دیگر ابزار را مجاز نمی‌کند.
- operation_key فقط با همان ورودی قابل retry است. revisionهای کنارگذاشته‌شده دوباره استفاده نمی‌شوند؛ حداکثر ۵۰ نسخه پیش‌نویس در نوبت مجاز است.
- منابع باید نسخه‌های واقعی، تازه و شناخته‌شده پیام باشند. caseها باید در حافظه همان نوبت وجود داشته باشند؛ case ساخته‌شده در همان نوبت نیز قابل پیوند است.
- ready یعنی درخواست معتبر اجرای ثبت‌شده، نه اثبات توافق یا تأیید انسان. قواعد توافق در پرامپت هستند؛ برنامه رأی نمی‌شمارد و پیش‌نیاز approval انسانی ایجاد نکرده است.
- مخزن روی دیتابیس bind می‌شود و در payload action نیز ثبت می‌شود. تغییر repository تنظیم‌شده روی همان دیتابیس رد می‌شود؛ برای پروژه/مخزن دیگر دیتابیس جدا لازم است.

## executor، رقابت و نتیجه نامعلوم

یک executor ترتیبی در کنار دریافت ورودی و همان یک worker ایجنت فعالیت می‌کند. claim اتمیک و قید SQLite مانع دو action executing می‌شوند؛ یک executor اصلی زیر قفل instance بسازید. شبکه داخل تراکنش دیتابیس نیست.

پیش از نوشتن، منبع و revision، لغو، نوع مقصد، labels و assignees بررسی و تصویر تازه GitHub با مبنای پیش‌نویس مقایسه می‌شود. تغییر دستی موجب stale می‌شود؛ خودکار روی آن بازنویسی نمی‌شود. فیلدهای درخواست‌نشده و labels نامرتبط از تصویر مبنا حفظ می‌شوند. GitHub compare-and-swap تضمین‌شده برای این مسیر فرض نشده است؛ رقابت بین آخرین GET و PATCH کاملاً حذف نشده و این محدودیت واقعی API/پیاده‌سازی است.

وضعیت‌ها: draft، ready، executing، succeeded، failed، cancelled، stale و unknown. action_versions سابقه payloadها را نگه می‌دارد و action_attempts هر تلاش، نوع اجرا/تطبیق، زمان و نتیجه آن را ثبت می‌کند.

- POST/PATCH یک marker پایدار `<!-- peyda-action:... -->` در body دارد. این marker جای idempotency تضمین‌شده GitHub نیست.
- timeout یا پاسخ مبهم پس از احتمال نوشتن unknown است. startup claimهای executing را unknown می‌کند.
- unknown فقط خوانده و تطبیق داده می‌شود؛ هیچ resend خودکاری انجام نمی‌دهد. ساخت ایشو با marker و author حساب تنظیم‌شده تطبیق داده می‌شود؛ کامنت نیز author و marker می‌خواهد.
- تطبیق صفحه‌بندی ناقص، نبود marker، چند تطبیق یا حذف/تغییر marker نتیجه قطعی تولید نمی‌کند. حداکثر پنج تلاش تطبیق با backoff انجام و سپس متوقف می‌شود. دستور retry می‌تواند دوباره تطبیق را فعال کند؛ برای unknown مجوز ارسال دوباره نیست.
- retry خودکار فقط برای خطایی است که عدم پذیرش عملیات مشخص است؛ هر چرخه حداکثر سه تلاش دارد. خطای صریح auth/permission/validation بدون retry خودکار failed می‌شود.
- موفقیت، نتیجه خارجی، projection و اعلان outbox یک تراکنش‌اند. شکست ثبت نتیجه پس از GitHub باعث recovery/reconciliation می‌شود، نه اجرای دوباره mutation. شکست sender نیز action را تکرار نمی‌کند.
- لغو پیش از ارسال جلوی اجرا را می‌گیرد. لغو در حین ارسال یا unknown نیازمند تطبیق است و rollback خارجی ادعا نمی‌شود.
- اگر GitHub در پاسخ موفق، assignee یا label درخواستی را کامل اعمال نکند، هشدار Partial result ثبت می‌شود؛ ساخت همان ایشو دوباره اجرا نمی‌شود.

فرمان‌های فنی در تاپیک مجاز برای admin IDs موجود:

- `/action ACTION_ID`: وضعیت و تعداد تلاش.
- `/cancel_action ACTION_ID`: لغو پیش از ارسال یا ثبت نیاز به تطبیق.
- `/retry_action ACTION_ID`: retry failed یا فقط reconciliation برای unknown.

شناسه نامعتبر inbox را مسموم نمی‌کند؛ پاسخ خطای مشخص می‌گیرد. لغو طبیعی از مسیر ایجنت انجام می‌شود؛ فرمان slash مسیر کنترل فوری است.

## کار، کد و تازگی داده

GitHub مرجع وضعیت ایشو/PR است. projection محلی با زمان مشاهده همراه است؛ نتیجه قدیمی نباید تصویر تازه‌تر را بازنویسی کند. خواندن issues و issue و پایان mutation آن را به‌روز می‌کند. polling، یادآوری و گزارش دوره‌ای در فاز پنج هستند.

labels پیش‌فرض `pm:backlog`، `pm:ready`، `pm:in_progress`، `pm:in_review`، `pm:blocked` و `pm:done` هستند. نگاشت باید شش نام غیرخالی و متفاوت داشته باشد. closed در GitHub به done نگاشت می‌شود؛ labelهای متعارض یا done روی issue باز به شکل conflict نمایش داده می‌شوند. برای تغییر هماهنگ workflow از work_status استفاده شود؛ تغییر raw state یا labels ممکن است conflict قابل مشاهده ایجاد کند.

موعد، وابستگی و اولویت metadata محلی هستند، نه فیلد ساختگی GitHub Issue. تغییر title نباید آن‌ها را پاک کند؛ dependencies غایب یعنی بدون تغییر و لیست خالی یعنی پاک‌کردن. due_at/priority خالی، مقدار قبلی را پاک می‌کند. موعد غیرخالی باید timezone داشته باشد. case links در projection ذخیره و از `memory_read` پرونده نیز برگردانده می‌شوند.

نگاشت تلگرام به GitHub login از `member_logins` می‌آید و با resource=members قابل خواندن است. نام نمایشی خودکار به login تبدیل نمی‌شود. ابزار assignees افراد قابل تخصیص را می‌خواند؛ ایجنت باید توافق واقعی مسئول را از گفتگو روشن کند.

خواندن tree/file به یک SHA کامل در هر نوبت pin می‌شود؛ فراخوانی موازی هم snapshot را عوض نمی‌کند. مسیرهای اسرار، traversal، symlink و submodule رد می‌شوند. فایل باید UTF-8 و حداکثر ۱۰۰ هزار بایت باشد؛ با offset/limit تا ۱۰ هزار نویسه صفحه‌بندی می‌شود. tree تا صد مسیر در هر صفحه می‌دهد و truncated بودن درخت اعلام می‌شود. PRها فقط metadata و changed paths می‌دهند؛ diff یا review تحلیل/منتشر نمی‌شود.

مجموعه‌های REST حداکثر پنج صفحه صدتایی خوانده می‌شوند. complete=false یا response_budget نشانه ناتمام بودن است؛ نبود مورد مشابه از آن نتیجه‌گیری نشود. خروجی خواندن حداکثر ۱۲ هزار نویسه است. تشخیص هم‌پوشانی، مناسب‌بودن شروع و نبود مانع با همان ایجنت و شواهد است، نه تضمین معنایی برنامه.

## تنظیمات و اتصال واقعی بعدی

Settings شامل github_repository، github_bot_login، github_token از نوع SecretStr، github_live_validated، member_logins و status_labels است. برای real adapter باید محدودسازی محیط و اتصال در فاز شش تأیید شوند؛ مقدار live پیش‌فرض خاموش است. هیچ حساب واقعی در این تحویل وصل نشده است.

در فاز شش، برای یک مخزن مشخص، fine-grained PAT با Issues write، Pull requests read و Contents read بررسی شود. HttpGithub ابتدا `/user` را با حساب تنظیم‌شده تطبیق می‌دهد؛ GitHub App installation token در این نسخه مسیر احراز هویت مستقلی ندارد و نباید به‌عنوان پشتیبانی‌شده فرض شود.

فقط پردازش اصلی credential را می‌گیرد. تنظیم MCP ایجنت URL loopback بدون PAT است؛ HTTP MCP باید در handshake اعلام شود و در غیر این صورت adapter خطای مشخص می‌دهد. نیاز واقعی CLI به تنظیماتی مانند process-per-session در فاز شش بررسی شود؛ flag یا مسیر fallback ناامن خودکار اضافه نشده است.

HTTP با timeout سی‌ثانیه‌ای، `X-GitHub-Api-Version: 2026-03-10` و ETag برای GET اجرا می‌شود. درخواست‌ها سری و تغییرها حداقل یک ثانیه فاصله دارند. Retry-After و reset محدودیت نرخ رعایت و deadlineها در SQLite حفظ می‌شوند. read موقت از ابزار خطای قابل اقدام می‌دهد؛ retry نامحدود وجود ندارد. redirect خودکار دنبال نمی‌شود؛ جابه‌جایی مخزن به اصلاح تنظیمات/رسیدگی نیاز دارد.

## شواهد و منابع

پذیرش شامل تست‌های فازهای قبلی و تست‌های action staging، submit نسخه‌دار، scope مخزن، PR و author، marker reconciliation، rollback پس از پاسخ موفق، حفظ labels و metadata، قفل claim، سابقه تلاش، بودجه retry، quota پایدار، پین SHA و MCP loopback است. source/model واقعی برای تشخیص توافق یا هم‌پوشانی ارزیابی نشده است. CI تعریف شده ولی در GitHub اجرا نشده است.

منابع قرارداد: [Issues REST](https://docs.github.com/en/rest/issues/issues)، [Issue comments](https://docs.github.com/en/rest/issues/comments)، [Repository contents](https://docs.github.com/en/rest/repos/contents)، [REST best practices](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)، [API versions](https://docs.github.com/en/rest/about-the-rest-api/api-versions). امضای SDKهای قفل‌شده MCP/ACP نیز از بسته نصب‌شده بررسی شده است. API version انتخاب‌شده در مرجع رسمی پشتیبانی‌شده است؛ هیچ ادعایی درباره موفقیت اتصال live وجود ندارد.


نتیجه بررسی نهایی این تحویل: **۱۳۳ تست آفلاین موفق**، Ruff check و format check موفق و mypy strict بدون خطا روی ۷۲ فایل منبع. `uv sync --locked --offline --group dev` موفق بود. دموی تازه‌دیتابیس، یک ایشو و دو mutation آزمایشی ایجاد کرد و پاسخ گم‌شده ساخت را بدون ایجاد دوم تطبیق داد. تست‌ها طبق محدودیت محیط خارج از sandbox اجرا شدند؛ سرویس‌های خارجی همچنان fake/MockTransport بودند و فقط MCP روی loopback واقعی اجرا شد. [مرجع دقیق API](GITHUB_API_REFERENCE.md).
