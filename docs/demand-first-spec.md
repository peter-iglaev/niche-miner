# Спецификация: пайплайн v2 «от спроса» (demand-first)

Статус: проект спецификации, 2026-09-16. Заменяет способ порождения кандидатов в v1 и меняет
смысл проверки; control plane, происхождение данных, скептик, судья, отчёт и Telegram сохраняются.

Читать вместе с `docs/sources-setup.md` (как подключить источники).

---

## 1. Зачем менять

Семь прогонов v1, около ста кандидатов, ноль квалифицированных. Это свойство конструкции, а не
невезение:

- **Идеи придумывала модель из головы.** Всё, что модель знает, уже кем-то описано и, как правило,
  уже кем-то сделано. Скептик каждый раз находил бесплатный OSS или готовый продукт.
- **Инструмент не соответствовал планке.** Планка — «наблюдаемая оплата за exact job», инструмент —
  веб-поиск. Веб-поиск возвращает страницы вендоров; следов оплаты в них нет по построению.
  Уровень доказательств 2 из 5 у всех двадцати кандидатов последнего прогона — это потолок
  инструмента, а не оценка идей.
- **Направление было перевёрнуто.** Шли от идеи к деньгам; называлось «money-first».
- **Ничего не накапливалось.** Каждый прогон стартовал с нуля.

v2 начинает с того, что **уже наблюдается**: что люди печатают в поиск, за что платят
исполнителям, что покупают в маркетплейсах, кто занимает выдачу. Идея становится следствием
данных, а не их причиной.

## 2. Принципы (они же критерии приёмки любого изменения)

1. **Каждый сигнал наблюдаемый.** Число из API с сохранённым сырым ответом, а не вывод модели.
   Модель кластеризует, интерпретирует и проверяет — но не измеряет.
2. **Бесплатно по умолчанию.** Все источники — бесплатные тиры и публичные API. Платные — только
   как явно включаемая опция в конфиге, выключены по умолчанию.
3. **DeepSeek везде, где есть токены.** Кластеризация, интерпретация, скептик, судья — DeepSeek.
   Claude (оркестратор в облачной сессии) только запускает команды, исполняет MCP-мост и пишет
   нарратив отчёта.
4. **Понятность важнее гибкости.** Семь стадий с русскими именами, у каждой один вход, один выход
   и одна папка. Любую стадию можно перезапустить отдельно и посмотреть её файлы глазами.
5. **Ноль кандидатов — валидный результат.** Пороги в конфиге, а не в голове; под план не
   смягчаются.
6. **Всё накапливается.** Каждый увиденный кластер живёт в бэклоге между прогонами.
7. **Провенанс как в v1.** Сырые ответы источников архивируются byte-for-byte, утверждения ссылаются
   на них, манифест хранит хэши, попытки неизменяемы.

## 3. Что сохраняется, что заменяется

| Компонент v1 | Судьба в v2 |
|---|---|
| `scripts/app.py` — манифест, задания, попытки, приёмка, решения, отчёт, финализация | **Сохраняется.** Добавляются новые роли и стадии. |
| `scripts/worker.py` — исполнитель на DeepSeek с инструментами поиска | **Сохраняется** для ролей `interpret`, `skeptic`, `judge`. Получает новые инструменты чтения архива сигналов. |
| `scripts/search.py`, `scripts/sources.py` — Brave, TED, Wayback, HN | **Сохраняются**, расширяются новыми коннекторами. |
| `scripts/hypotheses.py` — staging атомов, сочинённых моделью | **Заменяется** на `scripts/demand.py` (стадии 1–5). Формат `hypotheses.jsonl` уходит; вместо него `clusters.jsonl`. |
| Роль `discovery` — «поищи и процитируй» | **Заменяется** ролью `interpret`: по уже собранным сигналам кластера сформулировать задачу, форму продукта и ценовую точку, со ссылками на сырые ответы. |
| Роли `money`, `distribution`, `feasibility`, `demand`, `competition` | **Сворачиваются.** Их вопросы теперь закрываются сигналами на стадиях 3–4. Остаётся одна роль `feasibility` по запросу (когда судья просит). |
| Роли `skeptic`, `judge` | **Сохраняются**, меняется предмет: проверка чисел и интерпретации, а не поиск incumbent'ов (incumbent'ы уже в выдаче). |
| `config/scoring.yaml` — уровни оплаты 0–5 | **Заменяется** карточкой сигналов и числовыми гейтами в `config/demand.yaml`. |
| `config/portfolio.yaml` — квоты по 12 отраслям | **Не применяется** в v2 (охват задаётся темами в конфиге). Файл остаётся для v1-прогонов. |
| `scripts/report.py`, `scripts/report_pdf.py` | **Сохраняются**, добавляется шаблон «карточка кластера». |
| `scripts/telegram.py` | **Сохраняется**, добавляется команда `notify` для промежуточных сообщений. |
| `scripts/pipeline_viewer.py` | **Сохраняется**, добавляется вкладка кластеров. |
| `.claude/commands/research.md` | **Переписывается** под семь стадий v2. |

## 4. Семь стадий

Каждая стадия — отдельная команда `python3 scripts/demand.py <стадия> --run <id>`, одна папка
`runs/<id>/<номер>-<стадия>/`, файл `summary.json` с итогами и `telegram`-уведомление по завершении.
Стадии 1, 3, 4, 5 — детерминированные скрипты без модели. Стадии 2 и 6 — DeepSeek через
`worker.py`. Стадия 7 — control plane v1.

```
темы из конфига
   │
   ▼
[1] разворот ──► сотни фраз            (autocomplete, без модели, бесплатно)
   │
   ▼
[2] кластеры ──► десятки задач         (DeepSeek-chat группирует фразы)
   │
   ▼
[3] отсев ─────► бинарные проверки     (Upwork, маркетплейсы, Brave; без модели)
   │
   ▼
[4] цифры ─────► объём, CPC, тренд     (Google Ads KP / Bing / Ubersuggest-мост)
   │
   ▼
[5] гейты ─────► прошёл / не прошёл    (пороги из конфига, режим CPC)
   │
   ▼
[6] проверка ──► interpret → skeptic → judge   (DeepSeek, чистый контекст)
   │
   ▼
[7] решение ───► decide → report → PDF → Telegram
```

### Стадия 1 — Разворот (`expand`)

**Вход:** `config/demand.yaml` → `themes[].seeds` плюс seed-фразы из запроса пользователя.
**Что делает:** для каждого seed — веер подсказок из бесплатных autocomplete-эндпоинтов и три
оператора порождения узких ниш:

| Оператор | Шаблон | Что ловит |
|---|---|---|
| Пары «из A в B» | `{A} to {B} migration`, `{A} to {B}`, `migrate from {A} to {B}` | Узкие ниши на стыке двух платформ; список платформ — из конфига темы |
| Смена продукта | `{X} alternative`, `{X} vs`, `{X} pricing`, `{X} replacement` | Люди, которые уже платят за X |
| Форма инструмента | `{задача} calculator / checker / validator / generator / converter / template / audit / linter` | Запрос буквально описывает продукт |

Плюс общие модификаторы: `free`, `api`, `for {вертикаль}`, `open source`, `self hosted`, `tool`,
`software`, `saas`.

**Источники:** Google, YouTube, Amazon, Shopify App Store autocomplete; Ubersuggest
`google_suggestions` через MCP-мост (не тратит дневную квоту). Пауза 1–2 с между вызовами.
**Выход:** `1-expand/phrases.jsonl` — фраза, откуда пришла (seed, оператор, источник), сырой ответ
в `sources/`. Дедупликация по нормализованной фразе.
**Стоимость:** ноль; модели нет.

### Стадия 2 — Кластеры (`cluster`)

**Вход:** `phrases.jsonl`.
**Что делает:** DeepSeek-chat группирует фразы в задачи покупателя. Один кластер = одна
работа, которую человек хочет сделать, с каноническим именем, списком фраз, предполагаемой
формой продукта (`tool | template | service | content`) и классом намерения
(`commercial | tool_shaped | informational | navigational`). Кластер меньше
`cluster.min_phrases` (по умолчанию 5) отбрасывается — одна фраза не бизнес.
**Дедуп против бэклога:** каждый кластер сверяется с `backlog/clusters.jsonl`; совпавший
получает `backlog_id` и статус `refresh`, а не `new`.
**Расщепление.** После слияния каждый кластер от `2 × min_phrases` фраз проходит проверку на
склейку: один вызов модели с `temperature=0` спрашивает, одна ли это работа, и при необходимости
делит кластер по предмету и покупателю. Делить по стадии пути нельзя: «что такое X», «X tool»
и «X pricing» — одна работа. Ответ больше чем в `cluster.split_max_groups` групп, потерявший
больше половины фраз или оборванный, отбрасывается целиком — кластер остаётся как был.
Подкластеры наследуют intent и product_shape родителя, если модель их не переопределила.

**Выход:** `2-cluster/clusters.jsonl`.
**Стоимость:** один вызов DeepSeek-chat на ~200 фраз; ориентир 20–50k токенов на прогон.

### Стадия 3 — Отсев (`screen`)

**Вход:** `clusters.jsonl`.
**Что делает:** три бинарные проверки, каждая — наблюдение из источника, а не мнение:

| Проверка | Источник | Что фиксируется | Проходит, если |
|---|---|---|---|
| Кто-то платит человеку | Upwork (MCP-мост, фильтр по `title`); Freelancer.com API как дублёр | число заказов за 90 дней, бюджеты, `proposals_tier`, `client.total_spent` | ≥ `screen.upwork.min_jobs` заказов с бюджетом ≥ `screen.upwork.min_budget` **или** проверка отключена |
| Кто-то платит за софт | Apify Store, WordPress.org, Atlassian, VS Code, Obsidian, Chrome WS | платные листинги, установки, дата обновления | есть ≥1 платный листинг с пользователями **или** есть только бесплатные и заброшенные (гэп) |
| Можно ли туда попасть | Brave Search (топ-10 по 2–3 фразам кластера) | домены, доля форумов/Reddit/YouTube, число брендовых SaaS | брендовых SaaS в топ-5 ≤ `screen.serp.max_brands_top5` |

Пороги в конфиге. Проверка `feasibility_hint` — техническая: нужен ли для продукта доступ к
закрытым системам покупателя (по форме продукта) — только флаг, не гейт.
**Выход:** `3-screen/screened.jsonl` с полями `signals.upwork`, `signals.marketplace`,
`signals.serp`, каждое со ссылкой на `source_id`.
**Стоимость:** ноль (бесплатные тиры); Upwork — через мост.

### Стадия 4 — Цифры (`measure`)

**Вход:** кластеры, прошедшие отсев.
**Что делает:** объём, CPC, конкуренция рекламодателей и 12-месячный тренд по фразам кластера.
Источник выбирается по конфигу и доступности, в порядке приоритета:

1. **Google Ads API Keyword Planner** — первоисточник, бесплатно, 15 000 операций/день на Basic.
   Если настроен — используется для всех.
2. **Bing Webmaster Tools API** — точные объёмы Bing (3–8 % рынка, пропорции те же). Используется
   как второй голос или как основной, если Google Ads не настроен.
3. **Ubersuggest через MCP-мост** — 3 отчёта в день на free. Тратится только на кластеры с
   лучшим отсевом; один отчёт `keyword_suggestions` по родительской фразе даёт до ~100–250 ключей
   с объёмом и CPC. Бюджет дня хранится в `backlog/quota.json`; ответ «daily reports limit» от
   инструмента помечает день исчерпанным, а запрос — `deferred_quota` до следующего дня.

Кластер получает: `volume_sum` (сумма средних по фразам за всё окно), `volume_recent` (средний
месячный объём кластера за последний год — именно по нему судит гейт), `volume_head`,
`cpc_median` (середина ставок top-of-page), `cpc_low_median` и `cpc_high_median` (20-й и 80-й
перцентили), `cpc_max`, `competition_median`, `volume_top_share`, `trend_yoy` (год к году,
нужны 24 месяца), `trend_slope` (наклон, только для карточки), `months`, `source`.

Помесячный ряд запрашивается на `measure.months` месяцев (по умолчанию 24): на одном годе
сезонность неотличима от тренда, и ряд, начинающийся с осеннего пика, всегда выглядит падающим.
**Выход:** `4-measure/measured.jsonl`.
**Стоимость:** ноль при настроенном Google Ads/Bing; иначе ограничение 3 кластера в день.

### Стадия 5 — Гейты (`gate`)

**Вход:** `measured.jsonl`, `config/demand.yaml → mode`.
**Что делает:** числовые пороги, зависящие от режима:

| Режим | CPC-диапазон | Смысл | Дополнительно |
|---|---|---|---|
| `self_serve` (по умолчанию) | `cpc.self_serve = [2, 40]` | инструмент за $9–49/мес, покупатель приходит сам | обязателен `marketplace`, отсечка услуг по формулировкам |
| `services` | `cpc.services = [12, 60]` | услуга или дорогой продукт, нужны разговоры | обязателен `upwork` |
| `any` | без ограничения | разведка | гейты информационные |

Общие гейты: `volume_recent ≥ min_volume_sum` (средний месячный объём кластера за последний
год, по умолчанию 120), `intent ∈ {commercial, tool_shaped}`, `trend_yoy ≥ фон + min_trend_yoy_vs_baseline`.
**Выход:** `5-gate/gated.jsonl` с `gate.passed`, `gate.failed_rules[]`, `gate.flags[]`.
Пороги, по которым вынесены вердикты, печатаются в машинной сводке отчёта вместе с фоном
поиска и именем калибровки: без них карточку нельзя перепроверить позже. Кластер,
не прошедший гейты, **не идёт дальше**, но остаётся в бэклоге с причиной.

Пометки (`flags`) никого не отсекают и едут дальше в карточке: `cpc_high` (ставка на уровне
услуги — проверить покупателя), `crowded_serp` (топ-5 занят брендами), `one_phrase` (объём
кластера держит одна фраза), `trend_unknown` (ряд короче двух лет), `baseline_missing`.

**Каждый отказывающий порог проверен на контрольной выборке** — `config/calibration.yaml`,
запуск `python3 -B scripts/calibrate.py --run calib-<дата>`. Правило, отбраковывающее заведомо
живые self-serve ниши, порогом быть не может: см. [калибровку](calibration.md).

### Стадия 6 — Проверка (`review`)

Три роли на DeepSeek через существующий `worker.py`, для каждого кластера, прошедшего гейты
(не больше `review.max_clusters`, по умолчанию 6):

- **`interpret`** (deepseek-chat, серьёзная стадия): по замороженному набору сигналов и
  фраз сформулировать задачу покупателя, форму продукта, ценовую точку (из CPC-диапазона),
  единицу оплаты, самый дешёвый решающий тест. Каждое утверждение ссылается на `source_id`.
  Может сделать до 4 поисковых запросов, чтобы назвать конкретных incumbent'ов из выдачи.
- **`skeptic`** (deepseek-v4-pro, чистый контекст): проверяет **числа и интерпретацию**, а не
  ищет конкурентов: брендовый трафик в объёме (`{brand} pricing` — не наш спрос), сезонность,
  CPC, накрученный одним рекламодателем, артефакты autocomplete, склейка вариантов в Keyword
  Planner, несоответствие фраз и задачи, ложный `tool_shaped`.
- **`judge`** (deepseek-v4-pro, чистый контекст): выносит `assessment` v2 — `decision_context`
  (`build_test | investigate | drop`), карточку сигналов с оценками 0–4, критические неизвестные.
  `build_test` возможен только при всех гейтах и без неустранимых возражений скептика.

Независимость как в v1: разные worker_id, скептик и судья не переиспользуют контекст.
**Стоимость:** ориентир 100–400k токенов DeepSeek на кластер; шесть кластеров — до ~2M
токенов DeepSeek, около $1–2 по текущим ценам.

### Стадия 7 — Решение и отчёт (`decide`, `report`)

`app.py decide` по каждому кластеру: `build_test` (строить проверочную страницу),
`investigate`, `dropped`, `duplicate`. Затем `report` → нарратив оркестратора → `report` → PDF →
`pdf-review` → `finalize` → `telegram.py send`. Всё как в v1.

## 5. Карточка кластера (то, что читает основатель)

В отчёте на один кластер — одна карточка:

- **Задача** одной фразой, **форма продукта**, **ценовая точка** (из CPC), **единица оплаты**.
- **Спрос:** объём кластера/мес, число фраз, три главные фразы с объёмом, тренд стрелкой.
- **Деньги:** CPC медиана/макс; Upwork — N заказов, медианный бюджет; платные листинги — какие.
- **Проходимость:** топ-5 выдачи одной строкой (бренд / форум / видео / блог), DA лучшего
  небрендового результата.
- **Конкуренты в выдаче** по имени.
- **Возражения скептика** — только неустранённые.
- **Решение** и **самый дешёвый тест** (страница/инструмент под кластер; или $5–10/день
  exact-match в Google Ads как опция).
- Ссылки на `source_id` для каждого числа.

Машинная сводка (таблица кластеров, хэши, worker_id) — отдельной секцией, как в v1.

## 6. Конфиг `config/demand.yaml`

Один файл, читается всеми стадиями; замораживается в `runs/<id>/config.json` при создании
прогона. Пример со всеми ключами:

```yaml
schema_version: '2.0'

mode: self_serve            # self_serve | services | any — переключатель бизнес-модели

cpc:                        # диапазоны CPC в USD по режимам
  self_serve: [2, 12]
  services:   [12, 60]

themes:                     # что исследуем; каждый прогон берёт одну или несколько тем
  - id: devops-platform
    seeds: [terraform drift, kubernetes upgrade, argocd, github actions migration, on-call]
    platforms: [jenkins, github actions, gitlab ci, circleci, argocd, flux]   # для пар A→B
    verticals: [fintech, ecommerce, saas]                                       # для "for X"
  - id: ecommerce-tech
    seeds: [shopify migration, product feed, pim, checkout performance]
    platforms: [shopify, woocommerce, magento, bigcommerce, prestashop, wix, squarespace]
    verticals: [fashion, furniture, b2b wholesale]
  - id: ai-agents-mcp
    seeds: [mcp server, llm evaluation, prompt regression, agent tracing, token cost]
    platforms: [openai, anthropic, langchain, litellm]
    verticals: [customer support, legal, sales]

expand:
  sources: [google, youtube, shopify, amazon, ubersuggest_suggestions]
  modifiers: [free, api, tool, software, saas, open source, self hosted, template]
  operators: [pairs, alternatives, tool_shaped]
  pause_seconds: 1.5
  max_phrases_per_seed: 300

cluster:
  model_role: interpret       # роль из model-routing.yaml → deepseek-chat
  min_phrases: 5
  max_clusters: 60

screen:
  upwork:  {enabled: true, min_jobs: 3, min_budget: 200, window_days: 90}
  freelancer: {enabled: true}
  marketplaces: {enabled: true, sources: [apify, wordpress, atlassian, vscode, obsidian, chrome]}
  serp:    {enabled: true, provider: brave, phrases_per_cluster: 2, max_brands_top5: 2}

measure:
  providers: [google_ads, bing, ubersuggest]   # порядок приоритета; недоступные пропускаются
  ubersuggest_daily_reports: 3                 # лимит free; тратим на лучшие по отсеву
  country: US
  language: en

gate:
  min_volume_sum: 120                # средний месячный объём кластера за последний год
  min_trend_yoy_vs_baseline: -0.3    # падение относительно фона поиска, а не относительно нуля
  allowed_intents: [commercial, tool_shaped]
  cpc_flag_above: 20                 # пометка, не отказ
  flag_top_share: 0.9                # пометка, не отказ
  max_services_share: 0.3            # доля фраз, где покупают человекочасы

review:
  max_clusters: 6
  interpret_search_queries: 4
  skeptic_search_queries: 6

notify:
  telegram: true
  stages: [expand, cluster, screen, measure, gate, review, report]   # после каких стадий писать
  send_pdf: true

limits:                     # как в v1 — замораживаются в манифесте
  max_active_agents: 8
  max_attempts_per_assignment: 2
```

Переключение режима — одна строка `mode:`. Смена тем — блок `themes`. Отключение любого
источника — `enabled: false`, стадия честно запишет `not_collected`, а не ноль.

## 7. MCP-мост (почему он нужен и как устроен)

Ubersuggest и Upwork доступны **только как MCP-серверы внутри сессии Claude Code**; у скриптов на
DeepSeek к ним доступа нет, публичного REST нет. Чтобы не терять провенанс и не тратить токены
Claude на рассуждения, мост сделан механическим:

1. Стадия (`expand`, `screen`, `measure`) пишет `runs/<id>/bridge/requests.jsonl` — список
   вызовов: `{"id": "...", "tool": "ubersuggest.keyword_suggestions", "args": {...}}`.
2. Оркестратор (Claude) исполняет их **в том порядке и с теми аргументами**, без изменений, и
   сохраняет каждый ответ как есть в `runs/<id>/bridge/responses/<id>.json`.
3. `scripts/demand.py bridge-ingest --run <id>` валидирует ответы против запросов, кладёт их в
   `sources/` с sidecar (SHA, длина, время), помечает `source_id` и продолжает стадию.

Команда `/research` показывает оркестратору ровно этот список; квоты (3 отчёта Ubersuggest в день)
считаются скриптом до записи запросов, а не после отказа.

## 8. Бэклог между прогонами

`backlog/clusters.jsonl` — один файл в репозитории, дописывается каждой стадией:

```json
{"backlog_id": "bl-000123", "key": "terraform-drift-detection", "theme": "devops-platform",
 "first_seen_run": "demand-2026-09-20", "last_seen_run": "demand-2026-10-04",
 "status": "gated_out", "last_gate": {"passed": false, "failed_rules": ["min_volume_sum"]},
 "last_numbers": {"volume_sum": 210, "cpc_median": 7.3, "source": "ubersuggest", "at": "2026-09-20"},
 "decision": null, "notes": []}
```

Статусы: `new → screened → measured → gated_out | gated_in → reviewed → build_test | investigate |
dropped | duplicate`. Новый прогон не измеряет заново то, что измерено меньше `backlog.refresh_days`
назад (по умолчанию 60), и показывает в отчёте, что изменилось у старых кластеров.

## 9. Модели и токены

| Где | Модель | Зачем | Ориентир токенов на прогон |
|---|---|---|---|
| Стадия 2 | deepseek-chat | кластеризация фраз | 20–50k |
| Стадия 6, `interpret` | deepseek-chat | интерпретация сигналов | 50–150k на кластер |
| Стадия 6, `skeptic` | deepseek-v4-pro | проверка чисел | 100–300k на кластер |
| Стадия 6, `judge` | deepseek-v4-pro | оценка | 50–150k на кластер |
| Оркестратор | Claude (сессия) | команды, мост, нарратив | только вывод команд и ~3–4k слов нарратива |

Маршрутизация — в `config/model-routing.yaml`, роли `interpret`, `skeptic`, `judge`. Стадии 1, 3,
4, 5 токенов не тратят вообще.

## 10. Запуск из Claude Code cloud

Как раньше: `/research <тема или id темы из конфига> [--mode services] [--themes a,b]`.
Переписанная `.claude/commands/research.md` ведёт оркестратора по стадиям:

1. `preflight` (v1) + `demand.py doctor` — какие источники доступны, какие квоты остались.
2. `app.py create-run --scope demand --config config/demand.yaml`.
3. Стадии 1–5 командами; после каждой — `telegram.py notify` с одной строкой:
   «Разворот: 1 240 фраз из 14 seed. Кластеры: 37, из них 9 уже в бэклоге» и т. п.
4. Мост: оркестратор исполняет `bridge/requests.jsonl`, запускает `bridge-ingest`.
5. Стадия 6 — `worker.py` пачками до `max_active_agents`.
6. Стадия 7 — решения, отчёт, нарратив, PDF, `pdf-review`, `finalize`, `telegram.py send`.
7. Дашборд, коммит, пуш.

Уведомления в Telegram — короткие, по одной на стадию; финальное — PDF. Всё это через
существующий `scripts/telegram.py`, куда добавляется `notify --run <id> --text "..."`.

## 11. Структура прогона

```
runs/demand-2026-09-20/
├── manifest.json            # как в v1: хэши, задания, решения
├── config.json              # замороженный demand.yaml + limits
├── brief.md
├── 1-expand/   phrases.jsonl, summary.json
├── 2-cluster/  clusters.jsonl, summary.json
├── 3-screen/   screened.jsonl, summary.json
├── 4-measure/  measured.jsonl, summary.json
├── 5-gate/     gated.jsonl, summary.json
├── bridge/     requests.jsonl, responses/
├── sources/    сырые ответы + sidecar (как в v1)
├── work/       задания interpret/skeptic/judge (как в v1)
├── candidates/ <cluster>/signals.json, assessment.json, score.json
├── claims.jsonl
├── report.md, report.pdf, report-preview/
└── events.jsonl
```

Каждая папка стадии читается глазами: `summary.json` говорит, сколько вошло, сколько вышло и
почему отпало.

## 12. План внедрения

| Этап | Что появляется | Файлы |
|---|---|---|
| 1 | Конфиг, схема, стадия «разворот» с бесплатными autocomplete, бэклог | `config/demand.yaml`, `schemas/demand-*.json`, `scripts/demand.py` (expand), `backlog/` |
| 2 | Стадия «кластеры» на DeepSeek, роль `interpret` в маршрутизации | `scripts/demand.py` (cluster), `agents/interpret.md`, `config/model-routing.yaml` |
| 3 | Стадия «отсев»: Brave SERP-состав, коннекторы маркетплейсов, Freelancer | `scripts/demand.py` (screen), `scripts/connectors/*.py` |
| 4 | MCP-мост: запросы/ответы/ingest; Ubersuggest и Upwork через него | `scripts/demand.py` (bridge-ingest), правила в `research.md` |
| 5 | Стадия «цифры»: Google Ads KP, Bing; выбор провайдера по доступности | `scripts/connectors/google_ads.py`, `bing.py` |
| 6 | Стадия «гейты» и режимы CPC | `scripts/demand.py` (gate) |
| 7 | Скептик и судья v2, `assessment` v2, карточка сигналов | `agents/skeptic.md`, `agents/judge.md`, `schemas/assessment.schema.json` |
| 8 | Отчёт с карточками кластеров, `telegram.py notify`, дашборд | `scripts/report.py`, `scripts/telegram.py`, `scripts/pipeline_viewer.py` |
| 9 | Команда `/research` v2, `doctor`, документация | `.claude/commands/research.md`, `docs/runbook.md` |

Каждый этап заканчивается прогоном на одной теме и коммитом. Этап 1 уже даёт видимый результат
(тысяча фраз с происхождением); к этапу 3 появляется первый честный отсев без единого токена.

## 13. Что остаётся открытым

- Google Ads API: с 9 сентября 2026 доступ выдаётся по Cloud-проекту, а не по developer token;
  порядок получения уровня Basic описан в `docs/sources-setup.md`, но проверяется на практике
  при первом подключении.
- Bing требует верифицированный сайт — нужен любой домен основателя.
- Reddit пока не подключаем: с июня 2026 очередь на одобрение 2–4 недели. Место в стадии 3
  (сигнал боли) зарезервировано; HN Algolia закрывает его частично.
- Пороги гейтов калибруются не на глаз, а на контрольной выборке: `docs/calibration.md`.
  Полоса CPC режима `services` контролем пока не проверена — там числа по-прежнему на глаз.
- Определение «брендовый SaaS в выдаче» — эвристика (домен продукта с прайсингом); скептик
  проверяет спорные случаи.
