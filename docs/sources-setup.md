# Подключение источников сигналов спроса (бесплатные)

Инструкция для пайплайна v2 (`docs/demand-first-spec.md`). Всё ниже — бесплатные тиры и
публичные API. Платные варианты упомянуты только там, где бесплатного нет, и не требуются
для работы.

Пометки достоверности: **[✓]** проверено живым вызовом в сентябре 2026; **[~]** по официальной
документации, вызов не делался; **[?]** порядок мог измениться — сверить с официальной страницей
при подключении.

## Сколько ждать: прочитать до начала

Сначала подключайте то, что работает сегодня. Ожидания ниже — не наши сроки, а Google и Microsoft.

| Источник | Своих действий | Ожидание решения третьей стороны | Что даёт пайплайну |
|---|---|---|---|
| Ubersuggest MCP | уже подключён | нет; 3 отчёта в день, сброс по UTC | объём и CPC для 3 кластеров в день |
| Upwork MCP, Freelancer, маркетплейсы, автокомплиты, HN | ничего или `UPWORK_ORG_UID` | нет | деньги, платный софт, достижимость |
| Brave Search (SERP) | ключ за 5 минут | нет | достижимость выдачи |
| DeepSeek | пополнить баланс | нет | кластеризация, проверка агентами, нарратив |
| Telegram | бот и chat_id, 10 минут | нет | отчёт и уведомления |
| Bing Webmaster | 15–20 минут, нужен свой домен | нет; DNS-верификация может тянуться до суток, meta-тег мгновенно | объём и CPC для всех кластеров за проход |
| Google Ads Keyword Planner | 30–60 минут | **есть**: brand verification и одобрение Basic — Google обещает минуты, но проверка OAuth-приложения может занять **до 7 дней и больше** [?]; новый аккаунт Google Ads тоже иногда уходит «на проверку» | объём, CPC, помесячный тренд для всех кластеров |

Вывод: Google Ads — лучший источник, но подключается «на будущее». Чтобы получить цифры сегодня,
нужен Bing или дневная квота Ubersuggest.

---

## 0. Куда класть ключи

Скрипты читают переменные окружения, а при их отсутствии — файлы `.env.local`, затем `.env` в корне
репозитория (`scripts/env.py`). `.env.local` в git не попадает.

Формат:

```
DEEPSEEK_API_KEY=...
BRAVE_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

**Для облачных сессий Claude Code** переменные задаются один раз в настройках окружения
(claude.ai/code → Environments → ваша среда → Environment variables), а список разрешённых доменов —
в сетевой политике **Custom** там же. Полный список доменов — в разделе 12. Поле Setup script
оставить пустым: зависимости ставит SessionStart-хук (`scripts/install_pkgs.sh`).

Там же задаётся `NICHE_MINER_DATA` — путь к приватному репозиторию данных (профиль, прогоны,
бэклог), см. [`workspace.md`](workspace.md).

Проверка всего сразу: `python3 scripts/demand.py doctor` — покажет по каждому источнику
`available / missing key / quota left`. До появления `demand.py` — `python3 scripts/preflight.py`
и `python3 scripts/search.py providers`.

---

## 1. Уже подключено (проверить, что живо)

| Источник | Переменная | Проверка | Статус |
|---|---|---|---|
| DeepSeek | `DEEPSEEK_API_KEY` | `python3 scripts/llm.py ping --provider deepseek --model deepseek-chat` | [✓] работает |
| Brave Search | `BRAVE_API_KEY` | `python3 scripts/search.py providers` | [✓] работает |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | `python3 scripts/telegram.py check` | [✓] ключи есть |
| Ubersuggest MCP | нет (OAuth в сессии) | `auth_status` из сессии Claude Code | [✓] tier free |
| Upwork MCP | нет (OAuth в сессии) | `upwork__get_account` из сессии | [✓] подключён |
| Contra MCP | нет (OAuth в сессии) | `auth_status` из сессии | [✓] подключён |

Brave: бесплатный план — около 2 000 запросов в месяц и 1 запрос в секунду [~]; в пайплайне
уже выдерживается интервал 1,05 с. Этого хватает на проверку выдачи для ~600 кластеров в
месяц по 2–3 фразы.

---

## 2. Autocomplete (спрос, формулировки) — без ключей

Ничего настраивать не нужно. Эндпоинты, которые отвечают без авторизации [✓]:

| Источник | Вызов |
|---|---|
| Google | `https://suggestqueries.google.com/complete/search?client=firefox&hl=en&gl=us&q=<фраза>` |
| YouTube | тот же URL с `&ds=yt` |
| Amazon | `https://completion.amazon.com/api/2017/suggestions?mid=ATVPDKIKX0DER&alias=aps&prefix=<фраза>` |
| Shopify App Store | `https://apps.shopify.com/search/autocomplete?q=<фраза>` |

Правила: пауза 1–2 секунды между вызовами, при 429 — пауза 30 с и повтор один раз. Подсказки
персонализированы по региону (`gl`), поэтому регион фиксируется в конфиге и попадает в
sidecar источника. Чисел эти эндпоинты не дают — только формулировки.

Ubersuggest `google_suggestions` (через MCP-мост) разворачивает ~60 модификаторов на ключ и
**не расходует дневную квоту** free-аккаунта [✓]. Используется как пятый источник разворота.

---

## 3. Ubersuggest MCP (цифры для финалистов)

**Что даёт:** объём, CPC, конкуренцию рекламодателей, помесячный ряд; анализ выдачи с DA доменов.
**Уже подключён** к сессии Claude Code через коннектор. Аккаунт `free` [✓].

**Лимиты free** [✓ по ответам сервера и help-центру]: **3 отчёта в день** — общая квота с
веб-приложением; `keyword_suggestions`, `match_keywords`, `keyword_overview` считаются отчётами.
В `serp_analysis` отдельный счётчик «10 обновлений метрик в месяц», кэшированные ответы
бесплатны. `google_suggestions` и `location_suggest` квоту не тратят.

**Как расходовать 3 отчёта:** только на стадии «цифры», только на кластеры с лучшим отсевом, и
только `keyword_suggestions` по родительской фразе — один такой отчёт возвращает до ~250
ключей с объёмом и CPC [✓]. `keyword_overview` по одному ключу — расточительно.

**Доступ из скриптов:** публичного REST нет. Работает только через MCP-мост (спецификация, §7):
скрипт пишет `bridge/requests.jsonl`, оркестратор исполняет, `bridge-ingest` архивирует.

**Учёт квоты:** `backlog/quota.json` — сколько отчётов пайплайн запланировал сегодня. Квота общая с
веб-приложением, поэтому ledger может отставать от реальности: если инструмент ответил
«You have reached the daily reports limit», `bridge --ingest` помечает день исчерпанным
(`"exhausted": true`), а запросы получают статус `deferred_quota` и уходят на следующий день.
Сброс квоты — по календарному дню UTC.

**Платно (не требуется):** Individual — 150 отчётов в день, ~$29/мес или ~$290 разово [~].

---

## 4. Google Ads API → Keyword Planner (первоисточник объёмов, бесплатно)

**Что даёт:** `avg_monthly_searches` с помесячной разбивкой за 12 месяцев, ставки
`low/high_top_of_page_bid` (это и есть CPC), `competition` рекламодателей. Это данные, которые
Ubersuggest, Keywords Everywhere и DataForSEO перепродают.

**Сколько стоит [✓ по документации Google]:** сам API бесплатен («There is no charge for using the
Google Ads API»), биллинг Cloud-проекта включать не нужно, аккаунт Google Ads работает без
привязанной карты. **Но:** точные объёмы Google отдаёт только аккаунтам с активной рекламой; без
трат `avg_monthly_searches` приходит корзинами (10, 100, 1K–10K, 10K–100K) [~ по отзывам,
официального порога Google не публикует]. Ставки `low/high_top_of_page_bid` отдаются в любом
случае, а именно они нужны гейту self_serve. Если корзины мешают, точные объёмы бесплатно даёт
Bing Webmaster (раздел 5); платить за рекламу — крайний вариант.

**Схема доступа с 10.09.2026 [✓ по developers.google.com]:** developer token больше не выдаётся и
игнорируется сервером; уровень доступа привязан к **Google Cloud-проекту**, из которого выпущены
OAuth-credentials. Manager-аккаунт (MCC) не нужен. Заявки — на странице
`console.cloud.google.com/google/ads-apis/overview`. Для уровня **Basic** обязательна brand
verification проекта, решение автоматическое, за минуты. `KeywordPlanIdeaService` (то, что нужно
пайплайну) на уровне Explorer закрыт — нужен Basic.

**Шаги (30–60 минут, всё бесплатно):**

1. **Аккаунт Google Ads.** https://ads.google.com → создать аккаунт **без кампании** (в мастере
   «пропустить создание кампании» / expert mode). Записать Customer ID вида `123-456-7890`.
   Платёжные данные не нужны, пока не запускается реклама.
2. **Google Cloud-проект.** https://console.cloud.google.com → новый проект → APIs & Services →
   Library → **Google Ads API** → Enable. Биллинг проекта включать не нужно.
3. **OAuth consent screen (это и есть brand verification).** APIs & Services → OAuth consent
   screen: User type **External**; на вкладке Branding заполнить имя приложения, support email,
   логотип и authorized domain (любой свой домен); сохранить; на странице OAuth Overview нажать
   **Get started** — проверка идёт несколько минут; после успеха — **Publish branding** и перевести
   Publishing status в **In production**. Без «In production» refresh token тестового пользователя
   живёт 7 дней и пайплайн начнёт падать через неделю.
4. **OAuth-клиент.** APIs & Services → Credentials → Create credentials → OAuth client ID → тип
   **Desktop app** → скачать JSON (в нём `client_id` и `client_secret`).
5. **Уровень доступа Basic.** `console.cloud.google.com/google/ads-apis/overview` → раздел
   **Upgrade access level** → Basic. Заполнить анкету (использование: keyword research для
   собственного проекта, без управления чужими аккаунтами). Ответ — обычно в течение часа.
6. **Refresh token.** Один раз, на своей машине с браузером:
   ```
   pip install google-ads
   python3 scripts/google_ads_oauth.py ~/Downloads/client_secret_*.json
   ```
   Скрипт сам определит тип клиента и поднимет локальный сервер на подходящем адресе, откроет
   браузер, спросит доступ к scope `https://www.googleapis.com/auth/adwords` и напечатает готовые
   строки для `.env.local`. На экране «Google hasn't verified this app» — Advanced → Go to app.

   Два места, где это ломается, и что делать:
   - **`redirect_uri_mismatch`.** Клиент создан как Web application, а не Desktop app. У web-клиента
     redirect URI должен совпадать буква в букву. Либо создать новый клиент типа Desktop app, либо
     добавить в Authorized redirect URIs строку `http://localhost:8080/` — скрипт подхватит её сам.
   - **`pip` и `python` — разные интерпретаторы** (venv без pip, pyenv, uv). Ставить и запускать
     нужно одним: `python3 -m pip install google-ads`, затем тем же `python3`. Проверка:
     `python3 -c "import sys; print(sys.executable)"` и `pip -V` должны показать один путь.
7. **Переменные** в `.env.local` (файл в .gitignore, в репозиторий не попадает):
   ```
   GOOGLE_ADS_CLIENT_ID=...
   GOOGLE_ADS_CLIENT_SECRET=...
   GOOGLE_ADS_REFRESH_TOKEN=...
   GOOGLE_ADS_CUSTOMER_ID=1234567890          # без дефисов
   GOOGLE_ADS_LOGIN_CUSTOMER_ID=              # пусто; заполнять только если есть manager-аккаунт
   GOOGLE_ADS_DEVELOPER_TOKEN=                # пусто; старая схема, сервер игнорирует
   ```
   В облачной сессии Claude Code те же четыре ключа задаются в настройках окружения
   (Environment → variables) — коннектор читает и `.env.local`, и переменные процесса.
8. **Проверка.** Библиотека `google-ads` стоит в `requirements.txt`, облачная сессия ставит её
   сама хуком SessionStart. Запусти `python3 -B scripts/demand.py doctor` — в блоке `google_ads`
   должно быть `"available": true`. Реальный вызов сделает стадия `measure`: она берёт Google Ads
   первым провайдером, и Ubersuggest-мост больше не нужен.

**Где хранить ключи для облачных прогонов:** claude.ai/code → Environments → нужная среда →
Environment variables. Контейнер сессии временный, `.env.local` в нём не переживёт перезапуск.
Переменные среды подхватываются при старте новой сессии. В сетевой политике среды должны быть
`googleads.googleapis.com` и `oauth2.googleapis.com` (раздел 12).

**Квота:** Basic — 15 000 операций в день [✓]. Хватает на тысячи фраз в день.
**Ловушки:** Keyword Planner склеивает близкие варианты фраз и округляет; узкие запросы часто
дают 10 или 0 — это не «нет спроса», а группировка. Поэтому в стадии «цифры» считается сумма по
кластеру, а не число по одной фразе. Ошибка `DEVELOPER_TOKEN_NOT_APPROVED`/`PERMISSION_DENIED`
при уровне Test или Explorer означает, что заявка на Basic ещё не одобрена.

---

## 5. Bing Webmaster Tools → Keyword Research (точные объёмы Bing, бесплатно)

**Что даёт:** точные (не корзины) объёмы поиска Bing, связанные ключи. Bing — 3–8 % рынка, но
пропорции между фразами те же, а на длинном хвосте данные не склеиваются, как в Google.
Хороший второй голос; основной — если Google Ads не настроен.

**Шаги:**

1. https://www.bing.com/webmasters → войти Microsoft-аккаунтом.
2. **Добавить и верифицировать любой свой сайт** (нужен один домен; верификация — XML-файл,
   meta-тег или DNS-запись). Без верифицированного сайта API-ключ не выдаётся [~].
3. Settings → **API access** → сгенерировать API key.
4. Переменные:
   ```
   BING_WEBMASTER_API_KEY=...
   BING_WEBMASTER_SITE_URL=https://ваш-домен.tld
   ```
5. Проверка: `python3 -B scripts/demand.py doctor` (блок `bing`: `"available": true`). Стадия `measure` вызывает
   `GetKeyword` / `GetRelatedKeywords` через REST (`https://ssl.bing.com/webmaster/api.svc/json/`)
   [~]. Legacy SOAP/POX отключён с 31.08.2026 — использовать только JSON/REST.

**Квота:** официально не публикуется; практически — сотни вызовов в день без проблем [?].
Документация: https://learn.microsoft.com/en-us/bingwebmaster/

---

## 6. Upwork MCP (кто-то платит человеку за эту задачу)

**Что даёт [✓]:** `find_jobs` возвращает заказы с бюджетом (fixed) или ставкой (hourly),
`proposals_tier` (число откликов: «Fewer than 5» … «50+»), по клиенту — `total_spent`,
`total_posted_jobs`, рейтинг; навыки, длительность. Это самый прямой наблюдаемый сигнал
готовности платить и повторяемости.

**Подключение:** коннектор Upwork в claude.ai (Settings → Connectors → Upwork, OAuth 2.1).
Уже подключён к этой сессии. Нужен аккаунт Upwork (фрилансерский профиль подходит).

**Переменная:** `UPWORK_ORG_UID=<org_uid из list_accounts>` — идентификатор аккаунта (не секрет), чтобы мост
подставлял его в запросы сам. Узнать: вызвать `mcp__Upwork__upwork__list_accounts` в сессии.

**Как использовать в пайплайне:** только через MCP-мост, фильтр по `title` (не `query` — он
делает семантическое расширение и шумит), `job_type: fixed`, `budget_min` из конфига,
`sort: recency`, окно 90 дней. Скрипт считает число заказов и медиану бюджета.

**Ограничения:** прямой GraphQL API — по заявке, «personal use», низкая доля одобрений [~];
скрейпинг запрещён ToS. Через MCP — умеренные объёмы (десятки вызовов в прогон), это исследование,
не массовая выгрузка.

**Дублёр без ключа [✓]:** Freelancer.com —
`GET https://www.freelancer.com/api/projects/0.1/projects/active/?query=<фраза>&limit=50&job_details=true&compact=true`
отдаёт бюджеты и `bid_stats` без авторизации. Шумнее и дешевле по бюджетам; используется как
второй голос. Переменных не нужно.

**Contra MCP** — уже подключён; `list_job_feed`, `list_services` дают цены productized-услуг.
Маленький рынок, дополнительный источник.

---

## 7. Маркетплейсы (платят ли за софт, сколько конкурентов) — без ключей

Все эндпоинты отвечают без авторизации [✓]:

| Маркетплейс | Вызов | Поля |
|---|---|---|
| Apify Store | `GET https://api.apify.com/v2/store?search=<фраза>&limit=50` | `stats.totalUsers`, `totalUsers30Days`, `totalRuns`, `pricingInfos` |
| WordPress.org | `GET https://api.wordpress.org/plugins/info/1.2/?action=query_plugins&request[search]=<фраза>&request[per_page]=50` | `active_installs`, `downloaded`, `rating`, `num_ratings`, `last_updated` |
| Atlassian | `GET https://marketplace.atlassian.com/rest/2/addons?text=<фраза>&hosting=cloud` | `distribution.totalInstalls`, `reviews.count`, цена |
| VS Code | `POST https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery` (JSON-тело с `filters`) | `statistics.install` — эндпоинт недокументирован |
| Obsidian | `GET https://raw.githubusercontent.com/obsidianmd/obsidian-releases/HEAD/community-plugin-stats.json` | загрузки по версиям, все плагины одним файлом |
| Chrome Web Store | HTML страницы расширения, `grep -oE '[0-9,]+ users'` | число пользователей; поиск даёт ~10 результатов без пагинации |
| Shopify App Store | `https://apps.shopify.com/search?q=<фраза>` | рейтинг и число отзывов; установок не публикует |
| Smithery (MCP-реестр) | `GET https://registry.smithery.ai/servers?q=<фраза>` | `useCount`, `verified` |

`APIFY_TOKEN` не нужен для чтения Store; если появится — можно поднять лимиты [~].
Ловушка везде: установки — сигнал **удовлетворённого** спроса. Гэп = много подсказок в
autocomplete + мало, старые или низкорейтинговые результаты в маркетплейсе.

---

## 8. Hacker News Algolia (голос практиков) — без ключа

`GET https://hn.algolia.com/api/v1/search?query=<фраза>&tags=comment&hitsPerPage=100&numericFilters=created_at_i><unix>`
[✓]. ~10 000 запросов в час на IP, до 1 000 результатов на запрос. Шаблоны запросов:
`"is there a tool" <тема>`, `"I would pay for" <тема>`, `"Ask HN: what do you use for"`,
`"Show HN" alternative to <X>`. Уже используется в `scripts/sources.py`.

---

## 9. GitHub (незакрытые фичи в популярном OSS)

`GET https://api.github.com/search/issues?q=repo:<owner>/<repo> is:issue is:open "feature request"&sort=reactions-%2B1&order=desc`.
Без токена — 10 запросов в минуту и часто 403 из датацентра [✓ наблюдалось в прогонах]; с токеном —
30 в минуту.

1. https://github.com/settings/tokens → Fine-grained token, только public repositories, без
   дополнительных прав.
2. `GITHUB_TOKEN=github_pat_...`

Плюс без ключа: `https://api.npmjs.org/downloads/point/last-month/<пакет>` — масштаб экосистемы [✓].

---

## 10. Триггеры: закупки и регуляторика — без ключей

| Источник | Вызов | Что даёт |
|---|---|---|
| TED (EU-закупки) | `POST https://api.ted.europa.eu/v3/notices/search`, expert-запрос `FT~"<фраза>" AND PD>=20260101` | бюджеты и повторяемость контрактов; уже в `scripts/sources.py` [✓] |
| Federal Register | `GET https://www.federalregister.gov/api/v1/documents.json?conditions[term]=<фраза>` | регуляторные документы США с датами [✓] |
| endoflife.date | `GET https://endoflife.date/api/v1/products/<продукт>` | даты окончания поддержки — волны «migrate / alternative» [✓] |
| SAM.gov (опционально) | ключ бесплатный, 10 запросов в день на личном аккаунте [~] | закупки США; низкий приоритет |

---

## 11. Что сознательно не подключаем сейчас

- **Reddit** — с июня 2026 обязательное одобрение доступа, очередь 2–4 недели, коммерческое
  использование по договору [~]. Место в стадии «отсев» зарезервировано; когда одобрят —
  `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, PRAW, 100 запросов в минуту.
- **G2 / Capterra / AlternativeTo** — только скрейп через анти-бот; нарушает правило
  «без скрейпинга поисковиков и защищённых страниц».
- **Google Trends** — официальный API в закрытой альфе, `pytrends` архивирован; форки
  ловят 429. Тренд берём из помесячного ряда Keyword Planner.
- **Semrush / Ahrefs** — MCP только на платных планах от $129–199 в месяц.
- **Serper.dev** — 2 500 бесплатных SERP-запросов (показывают рекламные блоки Google, чего
  Brave не даёт). Опция: `SERPER_API_KEY=...`, провайдер `serper` в `screen.serp.provider`.
  Не обязателен.
- **Keywords Everywhere** (~$84/год) и **DataForSEO** (депозит $50) — платные замены Google
  Ads API, если настройка Google не удастся. В конфиге как провайдеры `keywords_everywhere`,
  `dataforseo`, выключены.

---

## 12. Домены для сетевой политики облачной среды

В claude.ai/code → Environments → Network: **Custom** → добавить:

```
api.deepseek.com
api.search.brave.com
api.telegram.org
suggestqueries.google.com
completion.amazon.com
apps.shopify.com
hn.algolia.com
api.apify.com
api.wordpress.org
marketplace.atlassian.com
marketplace.visualstudio.com
raw.githubusercontent.com
api.github.com
api.npmjs.org
chromewebstore.google.com
registry.smithery.ai
api.ted.europa.eu
www.federalregister.gov
endoflife.date
archive.org
www.freelancer.com
googleads.googleapis.com
oauth2.googleapis.com
ssl.bing.com
google.serper.dev
```

Последние четыре нужны только при настроенных Google Ads, Bing и Serper. MCP-серверы
(Ubersuggest, Upwork, Contra) идут через коннекторы claude.ai и в этот список не входят.

---

## 13. Порядок подключения (рекомендуемый)

1. Ничего не делать — стадии «разворот», «отсев» (маркетплейсы, Brave, Freelancer, HN) и
   MCP-мост работают на том, что уже есть. Первый прогон возможен сразу; цифры — только через
   3 отчёта Ubersuggest в день.
2. Один вечер — **Google Ads API** (раздел 4). Снимает ограничение в 3 кластера в день.
3. Пятнадцать минут — **Bing Webmaster** (раздел 5), если есть свой домен.
4. Две минуты — **GitHub token** (раздел 9).
5. Когда одобрят — Reddit.

После каждого шага: `python3 scripts/demand.py doctor`.
