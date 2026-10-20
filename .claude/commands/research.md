---
description: Запустить прогон v2 «от спроса»: разворот → кластеры → отсев → цифры → гейты → проверка → отчёт в Telegram
---

Запусти прогон пайплайна v2 по запросу пользователя: $ARGUMENTS

Ты оркестратор. Стадии 1–5 — детерминированные скрипты, стадии 2 и 6 — DeepSeek через воркеров.
Сам ты только запускаешь команды, исполняешь MCP-мост, читаешь черновик нарратива и смотришь PDF.
Все команды — `python3 -B scripts/demand.py <команда> --run <run-id>`. Спецификация: `docs/demand-first-spec.md`.

## Порядок

1. **Готовность.** `python3 -B scripts/demand.py doctor`. Если нет `DEEPSEEK_API_KEY` или `BRAVE_API_KEY` —
   остановись и скажи, какой переменной не хватает. Остальные источники необязательны: `doctor` покажет,
   что доступно и сколько отчётов Ubersuggest осталось на сегодня.
2. **Прогон.** Разбери запрос: темы (`--themes` по id из `config/demand.yaml`; без них — все темы), режим
   (`--mode self_serve|services|any`; по умолчанию из конфига), цель. Run id — `demand-<тема>-<дата>`.
   ```
   python3 -B scripts/demand.py create --run <id> --themes <a,b> [--mode ...] --objective "..." --authorization "<дословно из запроса>"
   ```
3. **Стадии подряд.** `python3 -B scripts/demand.py auto --run <id>` гонит expand → cluster → screen → measure →
   gate → review и после каждой стадии шлёт строку в Telegram. Остановится, когда нужен мост.
4. **MCP-мост.** Когда `auto` печатает список запросов (или `bridge --run <id>`), исполни каждый **ровно с теми
   аргументами**, что в `bridge/requests.jsonl`, инструментом из поля `mcp_tool`
   (`mcp__Ubersuggest__google_suggestions`, `mcp__Ubersuggest__keyword_suggestions`, `mcp__Upwork__upwork__find_jobs`),
   и сохрани ответ инструмента как есть (JSON) в файл из `response_path` инструментом Write. Ничего не
   редактируй и не пересказывай. Если у Upwork-запроса нет `org_uid`, сначала вызови
   `mcp__Upwork__upwork__list_accounts` и подставь `org_uid` во все Upwork-запросы. Ошибку инструмента тоже
   сохрани как JSON `{"error": "..."}` — это честный результат. Затем:
   ```
   python3 -B scripts/demand.py bridge --run <id> --ingest
   python3 -B scripts/demand.py auto --run <id>
   ```
   Квота Ubersuggest на free — 3 отчёта в день; стадия «цифры» сама планирует не больше оставшегося,
   остальное помечает `deferred_quota` — это не ошибка. Если инструмент вернул «daily reports limit»
   (квоту потратили вне пайплайна), сохрани ошибку как `{"error": "..."}`: `bridge --ingest` пометит день
   исчерпанным в `backlog/quota.json`, вернёт запрос в очередь на завтра и не будет считать это провалом.
   Повторный `measure` на том же прогоне завтра возьмёт отложенные запросы первыми.
   `screen --refresh` пересобирает сигналы всех кластеров (после смены парсера или конфига);
   без флага стадия переиспользует готовые вердикты и досчитывает только ожидающих мост.
5. **Решения и отчёт.**
   ```
   python3 -B scripts/demand.py decide --run <id> --auto        # решения по вердиктам судьи
   python3 -B scripts/demand.py report --run <id>               # карточки + PDF-черновик
   python3 -B scripts/demand.py narrative --run <id>            # черновик двух секций от DeepSeek
   ```
   Прочитай `runs/<id>/report.md`: секции «Что получили» и «Что делать дальше» — черновик модели.
   Поправь, если он противоречит карточкам или выдаёт оффер за оплату; факты сверх карточек не добавляй.
   Если правил — снова `report`. Потом просмотри **каждую** PNG в `runs/<id>/report-preview/` и:
   ```
   python3 -B scripts/demand.py pdf-review --run <id> --author root --reason "<что проверено>"
   python3 -B scripts/demand.py finalize --run <id>
   python3 -B scripts/demand.py deliver --run <id>              # PDF в Telegram
   ```
6. **Публикация.** `python3 -B scripts/demand.py dashboard`, коммит артефактов прогона и бэклога, пуш.
   Если задан `NICHE_MINER_DATA`, `runs/`, `backlog/` и дашборды — симлинки в приватный репозиторий
   данных (`docs/workspace.md`): коммить и пушь их там (`git -C "$NICHE_MINER_DATA" …`), а в
   репозиторий кода — только правки кода. Pre-commit хук отклонит коммит с приватными путями
   или идентификаторами владельца; не обходи его через `--no-verify`.

## Правила

- Ноль кандидатов `build_test` — нормальный результат. Пороги в `config/demand.yaml`, под план не смягчаются.
- Неизвестное остаётся неизвестным: `not_collected`, `pending_bridge`, `unknown` — не ноль и не «спроса нет».
- Свои оценки кандидатов не пиши: интерпретирует, проверяет и решает DeepSeek; ты правишь только нарратив.
- Технический сбой стадии — перезапусти ту же стадию; стадии идемпотентны и переиспользуют собранное.
- Докладывай коротко и по-русски: что запущено, что ждёт моста, что принято.
