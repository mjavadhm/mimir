# مرجع قابلیت‌های Telegram Bot API و aiogram

تاریخ بررسی: **۲۰۲۶-۱۱-۱۷**. این سند مرجع مستند برای کدنویسی است؛ وجود یک قابلیت، دستور ساخت آن یا تأیید کارکردش در گروه `pm` نیست. قابلیت‌های مناسب می‌توانند در محدوده بات مدیریت استفاده شوند؛ ریویو PR همچنان مرحله دوم محصول است.

## ۱. نسخه‌ها و کیفیت شواهد

| مرجع | نتیجه بررسی |
|---|---|
| [Telegram Bot API changelog](https://core.telegram.org/bots/api-changelog) | آخرین نسخه مشاهده‌شده در همین گفتگو **10.4، مورخ ۲۰۲۶-۱۱-۰۹** بود. دریافت مجدد مستقیم این صفحه هنگام تهیه این سند با خطای شبکه مواجه شد؛ این بخش تأیید دوباره زنده محسوب نمی‌شود. |
| [aiogram changelog](https://docs.aiogram.dev/en/latest/changelog.html) | در این نوبت دریافت شد: **3.32.0، مورخ ۲۰۲۶-۱۱-۱۵**، با اعلام پشتیبانی Bot API **10.4**. |
| [aiogram در PyPI](https://pypi.org/project/aiogram/3.32.0/) و [metadata رسمی](https://pypi.org/pypi/aiogram/json) | آخرین نسخه منتشرشده مشاهده‌شده **3.32.0**؛ زمان انتشار `2026-11-15T16:42:04.755650Z`. |
| بسته منتشرشده aiogram 3.32.0 | wheel رسمی دانلود و با SHA-256 metadata تطبیق داده شد؛ متدها، مدل‌ها و توضیحات مرتبط از کد همان بسته بررسی شدند. در آن بررسی اولیه بسته نصب یا اجرا نشد؛ نصب و تست آفلاین بعداً در فاز یک انجام شد. |

هش wheel بررسی‌شده: `5cd85dc111f83c303b5716e1740d2add39bf35fbf5292e821528b27b150d6dc0`.

لینک‌های aiogram در جدول‌ها به `/latest/` اشاره می‌کنند و ممکن است بعداً تغییر کنند. مبنای نام‌ها و پارامترهای این snapshot، **بسته 3.32.0** است. در پیاده‌سازی فاز یک، aiogram 3.32.0 نصب و در uv.lock قفل شد؛ get_updates، send_message و مدل‌های ورودی با تست آفلاین بررسی شدند. دریافت مستندات نسخه‌دار `v3.32.0` هم در این بررسی HTTP 403 داد؛ از آن به‌عنوان مدرک خوانده‌شده استفاده نشده است.

## ۲. تازه‌ترین تغییرات مشاهده‌شده — Bot API 10.4

همه نگاشت‌های زیر در aiogram 3.32.0 وجود دارند. ستون محدودیت بخشی از قرارداد استفاده است.

| قابلیت و مرجع تلگرام | نگاشت دقیق aiogram و لینک | محدودیت و کاربرد برای این پروژه |
|---|---|---|
| [ارسال HTML Widget](https://core.telegram.org/bots/api#sendwidget) | [`SendWidget`](https://docs.aiogram.dev/en/latest/api/methods/send_widget.html)، `bot.send_widget(chat_id=..., html=...)` | فعلاً در production فقط چت خصوصی؛ محتوا از forward محافظت می‌شود و قابل copy نیست. قابلیت گروه `pm` فرض نشود. Widget با Mini App یا HTML معمول پیام یکسان نیست. |
| [ارسال مجدد shared widget](https://core.telegram.org/bots/api#sendsharedwidget) | [`SendSharedWidget`](https://docs.aiogram.dev/en/latest/api/methods/send_shared_widget.html)، `bot.send_shared_widget(chat_id=..., widget_id=...)` | widget باید ساخته همان بات باشد؛ محدودیت فعلی چت خصوصی برقرار است. inline widget در production هنوز قابل استفاده فرض نمی‌شود. |
| [پاسخ به widget query](https://core.telegram.org/bots/api#answerwidgetquery) | [`AnswerWidgetQuery`](https://docs.aiogram.dev/en/latest/api/methods/answer_widget_query.html)، `widget_query_id` و `data`؛ observerهای `router.widget_query` و `router.shared_widget_query` | پاسخ در ۱۵ ثانیه؛ جواب query داده widget را تغییر نمی‌دهد. برای تغییر داده باید متد edit مربوط فراخوانی شود. |
| [ویرایش داده widget پیام](https://core.telegram.org/bots/api#editmessagewidgetdata) | [`EditMessageWidgetData`](https://docs.aiogram.dev/en/latest/api/methods/edit_message_widget_data.html)، شناسه پیام/inline و `public_data` / `chat_data` | ویرایش public data برای shared widget حداکثر یک بار در ثانیه. |
| [ویرایش داده shared widget](https://core.telegram.org/bots/api#editsharedwidgetdata) | [`EditSharedWidgetData`](https://docs.aiogram.dev/en/latest/api/methods/edit_shared_widget_data.html)، `widget_id`، `public_data`، `cache_time` | حداکثر یک بار در ثانیه؛ عمومی‌بودن داده باید در طراحی payload لحاظ شود. |
| [پاسخ ephemeral به callback](https://core.telegram.org/bots/api#ephemeralmessageparameters) | [`EphemeralMessageParameters`](https://docs.aiogram.dev/en/latest/api/types/ephemeral_message_parameters.html) با `callback_query_id`، `receiver_user_id` و `replace_callback_query_message`؛ پارامتر `ephemeral_message_parameters` روی [`SendMessage`](https://docs.aiogram.dev/en/latest/api/methods/send_message.html) | در پاسخ callback می‌توان `receiver_user_id` را نداد؛ فقط فشاردهنده دکمه پیام را می‌بیند. channel direct messages استثناست. دریافت تضمین‌شده نیست، خصوصاً کاربر آفلاین؛ جای اعلان پایدار تیم نیست. |
| [نقل‌قول rich message](https://core.telegram.org/bots/api#replyparameters) | [`ReplyParameters.quote_rich_texts`](https://docs.aiogram.dev/en/latest/api/types/reply_parameters.html)، [`TextQuote.rich_texts`](https://docs.aiogram.dev/en/latest/api/types/text_quote.html) | با `quote` متنی ساده یکسان نیست؛ ساختار دقیق نقل‌قول باید با متن اصلی و قرارداد روش ارسال سازگار باشد. |
| [اطلاعات TON wallet](https://core.telegram.org/bots/api#tonwalletcard) | [`TonWalletCard`](https://docs.aiogram.dev/en/latest/api/types/ton_wallet_card.html)، `ChatFullInfo.ton_wallet_card` و `Message.ton_wallet_transfer` | قابلیت پروفایل/سرویس؛ کاربرد مستقیمی برای مدیریت پروژه ندارد. |
| [قالب صفحه اشتراک لینک چت](https://core.telegram.org/bots/api#getchatsharethemebackdrops) | [`GetChatShareThemeBackdrops`](https://docs.aiogram.dev/en/latest/api/methods/get_chat_share_theme_backdrops.html)، `ChatFullInfo.share_theme` | اطلاعات theme صفحه share است؛ به معنی امکان تغییر دلخواه رابط تلگرام توسط بات نیست. |
| [خواندن unique gift](https://core.telegram.org/bots/api#getuniquegift) | [`GetUniqueGift`](https://docs.aiogram.dev/en/latest/api/methods/get_unique_gift.html)، `name` | خارج محدوده فعلی محصول. |
| [حذف gift حساب Business](https://core.telegram.org/bots/api#deletebusinessaccountgift) | [`DeleteBusinessAccountGift`](https://docs.aiogram.dev/en/latest/api/methods/delete_business_account_gift.html)، `business_connection_id` و `owned_gift_id` | متد حذف واقعی، نیازمند حق Business مربوط و محدودیت نوع gift؛ در این پروژه استفاده نمی‌شود. |

تغییرات عمومی دیگر 10.4 در changelog: entityهای `ton_address` و `bank_card_number`، نوع `RichTextTonAddress`، پارامترهای `custom_description_parse_mode` و `custom_description_entities` برای `VerifyUser` / `VerifyChat`، و پشتیبانی پیشنهاد نام ثانویه با پسوندهای جدید برای `KeyboardButtonRequestManagedBot.suggested_username`. این موارد مجوز دسترسی یا نقش مدیریتی بات ما ایجاد نمی‌کنند. `AreaPosition` جایگزین `StoryAreaPosition` شده است؛ نوع قدیمی در پارامترهای جدید نباید بی‌بررسی استفاده شود.

لینک تاپیک خصوصی با بات می‌تواند فرم `https://t.me/<bot_username>/<message_thread_id>` داشته باشد و فقط برای همان کاربر باز شود؛ این را با لینک پیام گروه یا تاپیک گروه اشتباه نکنید.

## ۳. تغییرات جدید و مرتبط پیش از 10.4

نسخه معرفی قابلیت از changelog aiogram خوانده شده است؛ وجود نام‌ها در بسته 3.32.0 بررسی شد. قبل از استفاده، محدودیت کامل روش متناظر دوباره خوانده شود.

| قابلیت | معرفی و API در aiogram | تصمیم استفاده و محدودیت |
|---|---|---|
| پیام rich با بلوک، جدول و بخش‌بندی | Bot API 10.1 و توسعه در 10.2/10.3؛ [`SendRichMessage`](https://docs.aiogram.dev/en/latest/api/methods/send_rich_message.html)، [`InputRichMessage`](https://docs.aiogram.dev/en/latest/api/types/input_rich_message.html) با `html` / `markdown` یا `blocks` و `media` | گزینه برای گزارش خواناتر؛ با `send_message(parse_mode=...)` یکی نیست. وجود بلوک رسانه‌ای به حق ارسال همان رسانه نیاز دارد. برای خروجی فارسی `InputRichMessage.is_rtl` موجود است. |
| جدول compact و بلوک‌های rich تکمیلی | Bot API 10.3؛ [`InputRichBlockTable.is_compact`](https://docs.aiogram.dev/en/latest/api/types/input_rich_block_table.html)، `RichMessageButton` و `DisabledButton` | فقط هنگام نیاز واقعی به گزارش ساختاریافته؛ fallback متنی و بررسی رفتار کلاینت در فاز نهایی. |
| پیش‌نمایش streaming متن | معرفی 9.3، تغییر دسترسی 9.5 و گزینه توقف در 10.3؛ [`SendMessageDraft`](https://docs.aiogram.dev/en/latest/api/methods/send_message_draft.html)، `draft_id`، `message_thread_id`، `can_stop`، `keep_on_stop` | `chat_id` در قرارداد این متد، **چت خصوصی** است؛ گروه forum با چت خصوصی یکی نیست. preview موقت ۳۰ ثانیه‌ای است؛ پیام نهایی باید جداگانه با `send_message` پایدار شود. |
| پیش‌نمایش streaming rich | Bot API 10.1، تکمیل 10.3؛ [`SendRichMessageDraft`](https://docs.aiogram.dev/en/latest/api/methods/send_rich_message_draft.html)، `rich_message` و گزینه‌های توقف | چت خصوصی؛ upload مستقیم فایل جدید یا upload با URL در draft پشتیبانی نمی‌شود. نتیجه نهایی با `send_rich_message` ارسال می‌شود. |
| رویداد توقف generation توسط کاربر | Bot API 10.3؛ [`MessageGenerationStopped`](https://docs.aiogram.dev/en/latest/api/types/message_generation_stopped.html)، `router.stopped_message_generation`، `chat` و `draft_id` | رویداد باید به job مرتبط نگاشت شود. توقف draft به‌خودی‌خود عملیات بیرونی انجام‌شده را برنمی‌گرداند؛ `keep_on_stop` هم جای ارسال پیام پایدار نیست. |
| دکمه رنگی و emoji | Bot API 9.4؛ [`InlineKeyboardButton`](https://docs.aiogram.dev/en/latest/api/types/inline_keyboard_button.html)، `style` و `icon_custom_emoji_id` | سبک‌های تصریح‌شده در مدل بررسی‌شده: `danger`، `success`، `primary`. emoji شرط Premium مالک یا وضعیت خاص نام خریداری‌شده دارد؛ برای همه بات‌ها آزاد فرض نشود. |
| دکمه غیرفعال و نمایش reply همراه keyboard | Bot API 10.3؛ `InlineKeyboardButton.disabled` و `InlineKeyboardMarkup.force_reply` | `disabled` نوع `DisabledButton` است، نه `bool`: `disabled=DisabledButton()` شکل مدل بررسی‌شده است. غیرفعال‌بودن ظاهری، کنترل تأیید یا نقش در executor را جایگزین نمی‌کند. |
| پیام ephemeral و edit/delete آن | Bot API 10.2/10.3، توسعه 10.4؛ [`EditEphemeralMessageText`](https://docs.aiogram.dev/en/latest/api/methods/edit_ephemeral_message_text.html) و [`DeleteEphemeralMessage`](https://docs.aiogram.dev/en/latest/api/methods/delete_ephemeral_message.html) | `message_id` معمولی می‌تواند صفر باشد؛ هویت `ephemeral_message_id` را حفظ کنید. برای تأیید دائمی یا لینک شواهد از آن استفاده نشود. پارامترهای قدیمی `receiver_user_id` و `callback_query_id` روی روش ارسال deprecated شده‌اند؛ از مدل `ephemeral_message_parameters` استفاده شود. |
| تاپیک خصوصی و گروه forum | private topics در 9.3/9.4؛ [`CreateForumTopic`](https://docs.aiogram.dev/en/latest/api/methods/create_forum_topic.html)، `Message.message_thread_id` و `is_topic_message` | ساخت تاپیک در supergroup نیازمند admin و `can_manage_topics` است. `pm` یک تاپیک گروهی است. داشتن `message_thread_id` روی یک متد، ثابت نمی‌کند متد همه انواع چت را می‌پذیرد. |
| tag اعضای گروه | Bot API 9.5؛ [`SetChatMemberTag`](https://docs.aiogram.dev/en/latest/api/methods/set_chat_member_tag.html)، `chat_id`، `user_id`، `tag`؛ `Message.sender_tag` | برای عضو عادی گروه/supergroup و نیازمند admin با `can_manage_tags`؛ tag نمایشی منبع نقش داخلی مدیر/تأییدکننده نیست. |
| چک‌لیست بومی | [`SendChecklist`](https://docs.aiogram.dev/en/latest/api/methods/send_checklist.html)، [`EditMessageChecklist`](https://docs.aiogram.dev/en/latest/api/methods/edit_message_checklist.html) | به نمایندگی از حساب Business متصل و نیازمند `business_connection_id` است. بات معمولی گروه را دارای این قابلیت فرض نکنید؛ تسک‌ها می‌توانند با پیام و inline keyboard نمایش داده شوند. |
| پاسخ guest بدون عضویت معمول | Bot API 10.0؛ [`AnswerGuestQuery`](https://docs.aiogram.dev/en/latest/api/methods/answer_guest_query.html)، `router.guest_message` و `Message.guest_query_id` | مدل ورودی متفاوت است؛ دسترسی به پرونده یا گروه مجاز از guest query نتیجه نمی‌شود. در مرحله اول نیازی به فعال‌کردن این مسیر نیست. |

در 10.0 همچنین قابلیت‌های Poll رسانه‌دار، Live Photo و Managed Bots اضافه شده‌اند؛ وجودشان در SDK دلیل اضافه‌کردنشان به این بات نیست. به‌ترتیب [SendPoll](https://docs.aiogram.dev/en/latest/api/methods/send_poll.html)، [SendLivePhoto](https://docs.aiogram.dev/en/latest/api/methods/send_live_photo.html) و [GetManagedBotAccessSettings](https://docs.aiogram.dev/en/latest/api/methods/get_managed_bot_access_settings.html) مرجع‌اند.

## ۴. نکته‌های مخصوص aiogram 3.32.0

- حداقل Python در این نسخه **3.11** است؛ حداقل پیشنهادی پروژه 3.12 همچنان سازگار است. نسخه دقیق محیط و dependencyها باید با lockfile تأیید شود.
- `Dispatcher.start_polling` و `run_polling` پارامتر `tasks_shutdown_timeout` دارند. این گزینه برای shutdown است و تضمین durable ACK ایجاد نمی‌کند؛ تصمیم inbox پایدار پروژه برقرار است.
- `Bot.usernames()` برای نام‌های ثانویه و اصلاح تشخیص command مربوط اضافه شده است. تطبیق mention با یک username ثابت ممکن است کافی نباشد.
- پیام ارسالی از `sender_chat` یا مدیر ناشناس نباید به یک انسان دارای مجوز داخلی نسبت داده شود. `fsm_key_on_sender_chat` یک تنظیم opt-in برای کلید FSM است، نه احراز هویت کاربر.
- اصلاحات parsing و escaping rich text/HTML/Markdown در این نسخه دلیل دیگری برای تطبیق نسخه کد و مستندات است؛ متن و URLهای کاربر را با قالب‌بندی دستی ناامن ترکیب نکنید.

## ۵. روند استفاده بدون حدس‌زدن API

1. کاربرد را مشخص کنید: آیا قابلیت به مدیریت تسک/تصمیم کمک می‌کند و در همین مرحله محصول است؟
2. نسخه `aiogram` در `uv.lock` و محیط واقعی را بخوانید. اگر هنوز نصب نشده، فقط از «نسخه مرجع» صحبت کنید.
3. changelog رسمی Telegram و صفحه متد را برای نوع چت، دسترسی، production/test و محدودیت زمانی/حجمی بررسی کنید.
4. صفحه aiogram و signature/model همان نسخه را بررسی کنید. نام CamelCase تلگرام، نام متد پایتون را به‌تنهایی ثابت نمی‌کند.
5. در صورت نیاز با `inspect.signature` و `Model.model_fields` در محیط توسعه، یا کد بسته رسمی نسخه دقیق، نام و نوع پارامترها را تأیید کنید. این بررسی schema جای آزمون سرور را نمی‌گیرد.
6. payload، نگاشت رویداد، اثر بر policy و fallback را آفلاین تست کنید؛ داده حساس در fixture نگذارید.
7. نسخه/تاریخ/منابع/محدودیت را در این سند به‌روز کنید. قابلیت نامعلوم را نامعلوم ثبت کنید؛ خطا را با اختراع پارامتر یا retry بدون حد حل نکنید.
8. در فاز آخر مرحله محصول، روی سرویس و کلاینت واقعی تست کنید و فقط همان نتیجه مشاهده‌شده را گزارش دهید.

## ۶. پیشنهادهای مناسب گروه `pm`

این‌ها انتخاب‌های طراحی‌اند، نه قابلیت‌های پیاده‌شده:

- دکمه‌های تأیید/لغو/شروع کار با کنترل نقش و نسخه پیش‌نویس در برنامه؛ سبک رنگی می‌تواند کمک بصری کند.
- پاسخ با `reply_parameters` و `message_thread_id` برای حفظ زمینه بحث و تاپیک.
- بررسی Rich Messages برای گزارش‌ها؛ fallback به متن معمول و inline keyboard.
- برای پاسخ تدریجی گروه، پیام پایدار با ویرایش محدودشده و رعایت خطاهای نرخ؛ draft مخصوص private chat به گروه تعمیم داده نشود.
- ephemeral callback فقط برای بازخورد شخصی کوتاه و قابل‌چشم‌پوشی؛ نتیجه اقدام و تصمیم تیم در حافظه و پیام پایدار ثبت شود.
- Widgets، Business Checklist، TON و Managed Bots برای نسخه مدیریتی فعلی ضرورت ندارند؛ SDK آن‌ها را دارد ولی scope و شرایط دسترسی باید جدا احراز شوند.

## تطبیق فاز یک

در ۲۰۲۶-۱۱-۱۷ مستندات رسمی getUpdates و صفحه Bot API دوباره دریافت شدند. offset تنها بعد از ذخیره durable ورودی در درخواست بعدی جلو می‌رود؛ dispatcher polling پیش‌فرض استفاده نمی‌شود. کد نصب‌شده 3.32.0 نشان می‌دهد `Message.edit_date` از نوع `int | None` است؛ adapter آن را با `datetime.fromtimestamp(..., UTC)` تبدیل می‌کند. `get_updates(offset, timeout, limit, allowed_updates)` و `send_message(chat_id, message_thread_id, text, reply_parameters)` با fake session/متدهای async آزموده شدند. هیچ آزمون زنده‌ای انجام نشده است.

## تطبیق فاز دو

کارت وضعیت هر نوبت ابتدا با `send_rich_message` فرستاده می‌شود و بعد با `bot.edit_message_text(chat_id=..., message_id=..., rich_message=InputRichMessage(...))` ویرایش می‌شود. در بستهٔ نصب‌شدهٔ aiogram 3.32.0 پارامتر `rich_message` روی `edit_message_text` وجود دارد و به `editMessageText` نگاشت می‌شود. اگر ویرایش rich رد قطعی شود، همان متن یک بار با `text` ویرایش می‌شود. خطای «message is not modified» موفقیت است. پس از پاسخ نهایی، `bot.delete_message(chat_id, message_id)` همان پیام وضعیت را حذف می‌کند؛ نبودن پیام یا خارج‌بودن از پنجرهٔ حذف، تکرار نمی‌شود. [editMessageText](https://core.telegram.org/bots/api#editmessagetext) · [deleteMessage](https://core.telegram.org/bots/api#deletemessage). پیش‌نویس `sendRichMessageDraft` همچنان مخصوص چت خصوصی است و در تاپیک گروه استفاده نمی‌شود.

از ۲۰۲۶-۱۱-۱۸، پاسخ نهایی ایجنت در تاپیک forum با `bot.send_rich_message` به همراه `message_thread_id`، `reply_parameters` و `InputRichMessage(markdown=..., is_rtl=...)` ارسال می‌شود. امضا و فیلدها در نسخهٔ نصب‌شدهٔ aiogram 3.32.0 بررسی شدند. در رد قطعی درخواست rich، همان محتوا یک بار با `send_message` فرستاده می‌شود. خطای شبکه نتیجهٔ نامعلوم دارد و به ارسال جایگزین منجر نمی‌شود. [روش رسمی sendRichMessage](https://core.telegram.org/bots/api#sendrichmessage) · [InputRichMessage](https://core.telegram.org/bots/api#inputrichmessage).


## تطبیق چند پیام و ری‌اکشن — ۲۰۲۶-۱۱-۱۸

در aiogram نصب‌شده **3.32.0**، امضای `Bot.set_message_reaction` و مدل `ReactionTypeEmoji` از خود بسته بررسی شدند؛ متد و محدودیت‌ها با [Telegram setMessageReaction](https://core.telegram.org/bots/api#setmessagereaction)، [aiogram set_message_reaction](https://docs.aiogram.dev/en/latest/api/methods/set_message_reaction.html) و [changelog رسمی](https://core.telegram.org/bots/api-changelog) تطبیق داده شدند.

نگاشت: `bot.set_message_reaction(chat_id=..., message_id=..., reaction=[ReactionTypeEmoji(emoji=...)], is_big=False)`؛ حذف ری‌اکشن بات با `reaction=[]`. نتیجه موفق `True` است، نه شناسه پیام تازه. در outbox، external_id این عملیات به پیام هدف اشاره دارد. برای پیام بدون ریپلای، `send_message` با `reply_parameters=None` فراخوانی می‌شود؛ ریپلای اختیاری هر پیام مستقل است.

تلگرام فعلاً برای بات یک ری‌اکشن در هر پیام را اعلام می‌کند؛ paid مجاز نیست. بعضی service messageها ری‌اکشن نمی‌پذیرند؛ در media group ممکن است هدف واقعی اولین پیام حذف‌نشده گروه رسانه باشد. محدودیت ایموجی‌های فعال گروه همچنان اعمال می‌شود. ابزار فعلی custom emoji را ارائه نمی‌دهد. هیچ نقش یا دسترسی مدیریتی جدیدی با این قابلیت کسب نمی‌شود؛ بررسی live مجوزها و پشتیبانی گروه در فاز شش باقی می‌ماند.

خطای BadRequest مربوط به ری‌اکشن permanent، خطای شبکه/سرور unknown، و RetryAfter موقت با رعایت انتظار است. موفقیت ساختگی یا جایگزینی خودکار ری‌اکشن نامعتبر با پیام متنی وجود ندارد. تمام تست‌های این قابلیت آفلاین‌اند.

## فاز اول میمیر، ۲۰۲۶-۱۱-۲۲

مسیر دریافت با aiogram قفل‌شدهٔ 3.32.0 و Bot API آزموده شد. امضای نصب‌شدهٔ `Bot.get_updates` شامل `offset`، `limit`، `timeout`، `allowed_updates` و `request_timeout` است. گیرنده `message` و `edited_message` را در گروه و تاپیک تنظیم‌شده می‌پذیرد و offset متعهدشده را در poll بعدی می‌فرستد. تاریخ ارسال از `date` و زمان ویرایش از `edit_date` خوانده می‌شوند. [روش رسمی تلگرام](https://core.telegram.org/bots/api#getupdates)، [تغییرات رسمی](https://core.telegram.org/bots/api-changelog)، [مرجع نسخهٔ aiogram](https://docs.aiogram.dev/en/v3.32.0/api/methods/get_updates.html)، [شواهد اجرای واقعی](LIVE_TESTING.md).

## Mimir phase two, checked 2026-11-22

The installed aiogram 3.32.0 exposes `Bot.send_message` with `message_thread_id` and `reply_parameters`, `Bot.send_document` with `FSInputFile` and `message_thread_id`, and `Bot.set_message_reaction` with `message_id` and an emoji reaction list. Pass an empty list to remove the reaction. Private panel refresh uses `Bot.edit_message_text` on the bot's own private message. Topic sends target the bound forum group and replies refer to a stored message version. Limits and unsupported reactions are reported from the service. See [Telegram methods](https://core.telegram.org/bots/api), [Telegram changelog](https://core.telegram.org/bots/api-changelog), [versioned aiogram methods](https://docs.aiogram.dev/en/v3.32.0/api/methods/index.html) and the [live observations](LIVE_TESTING.md).

در میمیر بازسازی‌شده، ارسال و ریپلای متنی تاپیک از Markdown در
`InputRichMessage` استفاده می‌کند. پنل خصوصی و ویرایش همان کارت از بلوک
پاراگراف برای حفظ متن دقیق CLI استفاده می‌کنند. رد قطعیِ پارس Markdown
با بلوک متنی rich همان مقصد قابل بازیابی است. در ۲۰۲۶-۱۱-۲۲ با aiogram
3.32.0، تلگرام پیام آماده‌بودن مشتری‌یاب را در تاپیک صحیح با دو بلوک rich برگرداند.

در ۲۰۲۶-۱۱-۲۲ کارت وضعیت عمومیِ هر نوبت مشتری‌یاب روی aiogram 3.32.0
با `send_rich_message(message_thread_id=...)` و ویرایش همان پیام با
`edit_message_text(chat_id=..., message_id=..., rich_message=...)` فعال شد.
کارت تعداد پیام‌ها، فعالیت ابزار و پایان نوبت را نشان می‌دهد؛ متن تدریجی
مدل فقط در پنل خصوصی قابل مشاهده است. در Telegram Web برای نوبت سوم، ویرایش
کارت به «فعالیت ۲: جست‌وجو در حافظه» مشاهده شد.

کارت وضعیت مشتری‌یاب اکنون عنوان انگلیسی برجسته و یک سطر فعالیت دارد.
پس از پایان یا توقف پردازش، سرویس با `delete_message(chat_id, message_id)`
شناسهٔ ذخیره‌شدهٔ همان کارت را حذف می‌کند. محدودیت معمول حذف پیام ۴۸ ساعت است.
[Telegram deleteMessage](https://core.telegram.org/bots/api#deletemessage)

### Destination setup (2026-11-28)

With aiogram 3.32.0 the private setup flow uses Bot.get_chat to check a supergroup's is_forum flag, then Bot.send_rich_message with message_thread_id to check permission to post in the chosen topic. The receipt's chat.id and message_thread_id must match before activation. For General the request omits the thread ID and treats an absent receipt thread as 1. The live test exercised a numbered forum topic. Bot.send_chat_action returned true for a missing thread. See [Telegram getChat](https://core.telegram.org/bots/api#getchat), [sendRichMessage](https://core.telegram.org/bots/api#sendrichmessage) and [API changelog](https://core.telegram.org/bots/api-changelog).
