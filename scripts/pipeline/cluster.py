"""Стадия 2 — кластеры: DeepSeek группирует фразы в задачи покупателя.

Кластер = одна работа, которую человек хочет сделать: каноническое имя, формулировка задачи,
форма продукта, класс намерения, головная фраза и список фраз. Модель ничего не измеряет — только
группирует; её сырой ответ архивируется, каждый кластер сверяется с бэклогом.
"""
from __future__ import annotations

import json

import re

from scripts.llm import LLMError
from scripts.pipeline import backlog
from scripts.pipeline.llmjson import TruncatedReply, ask_json
from scripts.pipeline.run import finish_stage, read_stage_jsonl, write_stage_jsonl

SHAPES = ("tool", "template", "service", "content", "unknown")
INTENTS = ("commercial", "tool_shaped", "informational", "navigational")

SYSTEM = """Ты группируешь поисковые фразы в задачи покупателя (jobs to be done) для исследования ниш.
Правила:
- Один кластер = одна конкретная работа, которую человек хочет сделать, а не тема и не категория продукта.
- Головная фраза (head_phrase) — самая общая фраза кластера из списка, по ней потом запросят цифры.
- product_shape: tool (инструмент/SaaS), template (шаблон/чеклист), service (услуга людьми), content (статья/обучение), unknown.
- intent: commercial (ищут что купить: tool/software/pricing/alternative/service), tool_shaped (фраза буквально описывает
  инструмент: calculator/checker/validator/generator/converter/template), informational (как/что/почему), navigational (бренд).
- Не выдумывай фразы: в phrases только фразы из входного списка, дословно.
- Фразы, которые не образуют работу (брендовые, случайные), клади в кластер с name "прочее".
- Не создавай кластер из одной-двух фраз: если работа не набирает хотя бы четыре фразы, отправь их в "прочее".
- Общее слово — не работа. Разные покупатели и разные работы не склеиваются, даже если фразы совпадают
  по слову: «referral» у HR-рекрутинга, у направления пациентов и у партнёрского маркетинга — три разные
  работы; «pim» у товарных каталогов, у Entra Privileged Identity Management и у бренда конфет — три разных
  предмета; «migrate to shopify» у мерчанта и «shopify to woocommerce» у разработчика — две разные работы.
  Если в группе видны два покупателя или два предмета, делай два кластера.
- Кластеров на партию — сколько получилось работ. Не подгоняй под число: лучше двенадцать чистых кластеров
  и большое "прочее", чем двадцать пять, где в каждом по три разные работы.
Отвечай только JSON вида {"clusters":[{"name":"...","job":"...","product_shape":"...","intent":"...","head_phrase":"...","phrases":["..."]}]}.
name — короткое английское имя задачи (3–6 слов), job — одно предложение по-русски."""


# Формы инструмента: если головная фраза буквально называет инструмент, кластер не может быть
# «информационным». Список тот же, что у оператора tool_shaped на стадии разворота.
TOOL_SHAPES = ("tool", "tools", "maker", "generator", "checker", "check", "validator", "converter",
               "calculator", "software", "template", "templates", "monitor", "tracker", "linter",
               "audit", "analyzer", "scanner", "tester", "test", "api", "app", "platform")


def tool_shaped(phrase):
    """Фраза буквально описывает инструмент."""
    words = set((phrase or "").lower().replace("-", " ").split())
    return any(shape in words for shape in TOOL_SHAPES)


def tool_shaped_cluster(head, phrases, *, share=0.25):
    """Интент кластера как минимум tool_shaped, если это видно по фразам, а не по мнению модели.

    Проход расщепления породил подкластеры, где модель ставила informational кластеру с головой
    «youtube thumbnail maker»; гейт интента снимал их за разметку, а не за нишу. Одной головы
    мало: кластер про проверку доставляемости шёл с головой «email deliverability check», а его
    самые объёмные фразы — tester, tools, checker, platform. Поэтому смотрим и на долю фраз.
    """
    if tool_shaped(head):
        return True
    if not phrases:
        return False
    return sum(1 for p in phrases if tool_shaped(p)) / len(phrases) >= share


def _coverage(phrase, phrases):
    """Сколько других фраз кластера содержат все слова этой фразы."""
    bag = set(phrase.lower().split())
    return sum(1 for other in phrases if other != phrase and bag <= set(other.lower().split()))


def central_phrase(phrases):
    """Самая общая фраза кластера: та, чьи слова встречаются в наибольшем числе остальных.

    Раньше при негодной головной фразе от модели подставлялась первая по списку, а список
    приходит отсортированным — то есть первая по алфавиту. Так кластер про расшифровку
    подкастов назвался «apple podcast hide transcript» с нулевым объёмом, и по этой же фразе
    собрались сигналы отсева: маркетплейсы, выдача и биржа отвечали не про ту работу. Судья
    снял из-за этого один кластер целиком.
    """
    if not phrases:
        return None
    return min(phrases, key=lambda p: (-_coverage(p, phrases), len(p.split()), p.lower()))


def better_head(head, phrases, *, min_coverage=5, ratio=5):
    """Головная фраза модели или более общая, если модель выбрала частный хвост.

    Фолбэк спасает только когда модель назвала фразу не из кластера. Но она регулярно
    выбирает и вполне реальный, однако частный хвост: «atlantis terraform drift» при живой
    «terraform drift», «how much tokens cost» при «token cost». По такому хвосту объём
    нулевой, а сигналы отсева собираются именно по нему, и вердикт получается не про ту
    работу. Отличие видно по покрытию: у хвоста слова встречаются в одной-двух фразах
    кластера, у общей — в сотне (1 против 99 и 1 против 277 в прогоне devops-ai-2026-09-22).
    Замена происходит только при разнице в разы, поэтому равные головы остаются как были.
    """
    if not phrases:
        return head
    central = central_phrase(phrases)
    if not central or central == head:
        return head
    spread = _coverage(central, phrases)
    return central if spread >= min_coverage and spread >= ratio * max(_coverage(head, phrases), 1) else head


def _clean(cluster, allowed):
    phrases = []
    seen = set()
    for phrase in cluster.get("phrases") or []:
        if not isinstance(phrase, str):
            continue
        norm = " ".join(phrase.lower().split())
        if norm in allowed and norm not in seen:
            seen.add(norm)
            phrases.append(allowed[norm])
    head = cluster.get("head_phrase") if isinstance(cluster.get("head_phrase"), str) else None
    head_norm = " ".join((head or "").lower().split())
    if head_norm not in {" ".join(p.lower().split()) for p in phrases}:
        head = central_phrase(phrases) or head
    head = better_head(head, phrases)
    intent = cluster.get("intent") if cluster.get("intent") in INTENTS else "informational"
    if intent == "informational" and tool_shaped_cluster(head, phrases):
        intent = "tool_shaped"
    return {"name": str(cluster.get("name") or "").strip()[:80], "job": str(cluster.get("job") or "").strip(),
            "product_shape": cluster.get("product_shape") if cluster.get("product_shape") in SHAPES else "unknown",
            "intent": intent, "head_phrase": head, "phrases": phrases}


def cluster_phrases(run_id, phrases, config, *, root, who=None):
    """Один или несколько вызовов модели; при нескольких батчах — общий проход слияния."""
    settings = config.get("cluster") or {}
    choice = (config.get("models") or {}).get("cluster")
    batch = int(settings.get("phrases_per_call") or 350)
    allowed = {" ".join(p.lower().split()): p for p in phrases}
    clusters, source_ids, usage_total = [], [], {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    min_batch = int(settings.get("min_phrases_per_call") or 40)

    def ask_batch(chunk):
        """Кластеры для одной порции. Обрыв по лимиту вывода → делим порцию пополам."""
        messages = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": "Фразы:\n" + json.dumps(chunk, ensure_ascii=False)}]
        try:
            parsed, source_id, usage = ask_json(run_id, "cluster", messages, model_choice=choice, root=root, who=who)
        except TruncatedReply:
            if len(chunk) <= min_batch:
                raise
            half = len(chunk) // 2
            ask_batch(chunk[:half])
            ask_batch(chunk[half:])
            return
        source_ids.append(source_id)
        for key in usage_total:
            usage_total[key] += int(usage.get(key) or 0)
        for raw in (parsed.get("clusters") if isinstance(parsed, dict) else parsed) or []:
            cleaned = _clean(raw, allowed)
            if cleaned["phrases"] and cleaned["name"].lower() not in ("прочее", "other", "misc"):
                cleaned["source_id"] = source_id
                clusters.append(cleaned)

    for start in range(0, len(phrases), batch):
        ask_batch(phrases[start:start + batch])
    if len(clusters) > 1 and len(phrases) > min_batch:
        clusters, merge_source = _merge(run_id, clusters, choice, root, who)
        source_ids.append(merge_source)
    if settings.get("split", True):
        # Слияние сводит одинаковое, расщепление разводит разное; порядок именно такой,
        # иначе склейка, собранная слиянием из двух партий, останется незамеченной.
        #
        # Проходов несколько: один разбирает верхний слой склейки, но подкластер сам может
        # оказаться склейкой. В проверочном прогоне 2026-09-23 после одного прохода скептик
        # всё ещё нашёл шесть работ в «SEO rank tracking tools» и три в «media kit».
        # Цикл останавливается, когда проход ничего не изменил.
        split_min = int(settings.get("min_phrases") or 4)
        sample = int(settings.get("split_sample") or 120)
        groups = int(settings.get("split_max_groups") or 4)
        for _ in range(int(settings.get("split_passes") or 3)):
            expanded = []
            for cluster in clusters:
                expanded.extend(split_cluster(run_id, cluster, choice, root, who, min_phrases=split_min,
                                              sample=sample, max_groups=groups))
            if len(expanded) == len(clusters):
                break
            clusters = expanded
        # Расщепление режет каждый кластер отдельно и потому способно породить две почти
        # одинаковые работы из разных родителей: в прогоне 2026-09-24 «diagnose email
        # deliverability» и «email deliverability diagnostics» жили рядом и делили объём
        # одной ниши пополам. Финальное слияние сводит такие пары обратно.
        if len(clusters) > 1:
            clusters, after_split = _merge(run_id, clusters, choice, root, who)
            source_ids.append(after_split)
    return clusters, source_ids, usage_total


SPLIT_SYSTEM = """Тебе дан один кластер поисковых фраз, который считается одной работой покупателя. Проверь это.

Расщепляй ТОЛЬКО когда в кластере разные ПРЕДМЕТЫ или разные ПОКУПАТЕЛИ:
- «referral»: HR-рефералы сотрудников, направления пациентов между клиниками и партнёрский маркетинг —
  три разных покупателя, три кластера.
- «PIM»: товарные каталоги, Entra Privileged Identity Management, радиочастотная интермодуляция и бренд
  конфет — четыре разных предмета.
- «migrate to shopify»: мерчант уходит на Shopify и разработчик уходит с Shopify — разные покупатели
  и противоположные направления.

НЕ расщепляй, когда предмет и покупатель одни, а различается лишь стадия пути или форма запроса.
Один и тот же человек по одному и тому же делу спрашивает «что такое X», «X best practices», «X tool»,
«X pricing», «X alternatives», «как починить X», «X курс» — это ОДНА работа, и разбивать её на
«определения», «инструменты», «обучение» и «диагностику» нельзя. Так же не расщепляй по платформе,
по языку и по синониму одного и того же предмета.

Если кластер про одну работу — верни его как есть одной группой и coherent: true. Если склеен — раздели
на группы, каждая из которых одна работа. Обычно склеенный кластер даёт две-три группы; если групп
выходит больше четырёх, скорее всего ты режешь по стадии пути, а не по работе — вернись к одной группе.
Фразы, не относящиеся ни к одной работе, клади в группу с name "прочее".
Каждая фраза входит ровно в одну группу, дословно из входного списка, ничего не выдумывай.

intent и product_shape у групп — такие же, как у исходного кластера, если работа та же самая.
Меняй их только когда группа действительно про другое: например, группа целиком про «что такое X»
становится informational, а группа про наём подрядчика — service. Группа, где есть «X tool»,
«X software», «X checker», informational быть не может.

Отвечай только JSON: {"coherent": true|false, "groups":[{"name":"...","job":"...","product_shape":"...",
"intent":"...","head_phrase":"...","phrases":["..."]}]}.
name — короткое английское имя работы (3–6 слов), job — одно предложение по-русски."""


def split_cluster(run_id, cluster, choice, root, who, *, min_phrases, sample, max_groups=4):
    """Расщепить кластер, если в нём несколько работ. Возвращает список кластеров.

    Скептик ловил склейку в четырёх кластерах из восьми разобранных 22 сентября — но ловил
    в самом конце, после того как на кластер потрачены замеры и проверка, и вердикт сводился
    к «цифры посчитаны по смеси». Здесь тот же вопрос задаётся сразу после кластеризации,
    где он стоит один дешёвый вызов и где ответ ещё может изменить, что мы измеряем.

    Ошибка модели не должна стоить кластера: при обрыве, неполном ответе или потере фраз
    исходный кластер возвращается нетронутым.
    """
    phrases = cluster.get("phrases") or []
    if len(phrases) < 2 * min_phrases:
        return [cluster]
    # Выборка с шагом, а не первые N: список приходит отсортированным, и первые 120 фраз
    # из 399 — это один алфавитный угол кластера. Кластер «media kit» так и не расщеплялся,
    # хотя скептик находил в нём три работы: модель просто не видела остальных двух третей.
    step = max(1, (len(phrases) + sample - 1) // sample)
    shown = phrases[::step][:sample]
    messages = [{"role": "system", "content": SPLIT_SYSTEM},
                {"role": "user", "content": json.dumps({"name": cluster.get("name"), "job": cluster.get("job"),
                                                        "phrases": shown}, ensure_ascii=False)}]
    try:
        # temperature=0: расщепление меняет состав кластеров, а значит и то, что мы измеряем.
        # На одном и том же кластере ответ не должен плавать от прогона к прогону.
        parsed, source_id, _ = ask_json(run_id, "cluster-split", messages, model_choice=choice,
                                        temperature=0, root=root, who=who)
    except (TruncatedReply, LLMError):
        return [cluster]
    groups = (parsed.get("groups") if isinstance(parsed, dict) else None) or []
    if parsed.get("coherent") is True or len(groups) < 2:
        return [cluster]
    allowed = {" ".join(p.lower().split()): p for p in phrases}
    out, taken = [], set()
    for group in groups:
        cleaned = _clean(group, allowed)
        if str(cleaned["name"]).strip().lower() in ("прочее", "other", "misc"):
            continue
        fresh = [p for p in cleaned["phrases"] if p not in taken]
        if len(fresh) < min_phrases:
            continue
        taken.update(fresh)
        # Пропущенные моделью поля наследуются от родителя, а не берутся из умолчаний _clean:
        # иначе подкластер тихо становится informational и падает на гейте интента за то,
        # что модель не повторила поле, — вердикт не про нишу, а про форму ответа.
        for field in ("intent", "product_shape"):
            if not group.get(field) or group.get(field) == cluster.get(field):
                cleaned[field] = cluster.get(field, cleaned[field])
        out.append({**cluster, **cleaned, "phrases": fresh,
                    "head_phrase": better_head(cleaned["head_phrase"], fresh),
                    "split_from": cluster.get("name"), "source_id": source_id})
    # Слишком дробное расщепление — признак резки по стадии пути, а не по работе: на проверке
    # чистый кластер «email deliverability» разваливался на семь частей вида «определения»,
    # «инструменты», «обучение». Такой ответ отбрасывается целиком.
    if len(out) > int(max_groups):
        return [cluster]
    # Расщепление, потерявшее больше половины показанных фраз, — не расщепление, а потеря данных.
    if len(out) < 2 or len(taken) < len(shown) // 2:
        return [cluster]
    _assign_rest([p for p in phrases if p not in taken], out)
    for part in out:
        part["head_phrase"] = better_head(part["head_phrase"], part["phrases"])
    return out


def _assign_rest(rest, groups):
    """Фразы вне выборки — в ближайшую по словам группу, а не в мусор.

    Модели показывается выборка (120 фраз из 399), и раньше всё, что в неё не попало, просто
    исчезало: кластер «media kit» после расщепления терял три четверти фраз, и объём считался
    уже не по нему. Родство меряется долей общих слов с фразами группы.
    """
    if not rest or not groups:
        return
    vocab = [set(w for p in g["phrases"] for w in p.lower().split()) for g in groups]
    for phrase in rest:
        words = set(phrase.lower().split())
        scores = [len(words & bag) / max(len(words), 1) for bag in vocab]
        best = max(range(len(groups)), key=lambda i: scores[i])
        if scores[best] > 0:
            groups[best]["phrases"].append(phrase)
            # Словарь не растёт по ходу раздачи: иначе первая же группа набирает случайных слов
            # и начинает притягивать к себе всё подряд. Родство меряется только по тем фразам,
            # которые в группу положила модель.


MERGE_SYSTEM = """Тебе дан список кластеров задач из разных партий фраз. Объедини дубликаты: кластеры про одну и ту же работу
покупателя. Отвечай только JSON: {"groups":[{"name":"...","job":"...","product_shape":"...","intent":"...","members":[индексы]}]}.
Каждый индекс входит ровно в одну группу. Не объединяй разные работы только потому, что они про одну платформу."""


def _merge(run_id, clusters, choice, root, who, *, window=60, min_window=10):
    """Слияние окнами, а не одним вызовом на весь список.

    Прогон creator-marketing-2026-09-22 упал здесь: 141 кластер в одном запросе, и ответ
    обрезало на лимите вывода в 8000 токенов. Основной проход давно умеет делить порцию
    пополам, а слияние — нет, поэтому весь прогон вставал на стадии кластеризации.

    Окно режет список на части, второй проход ловит дубликаты, оказавшиеся в разных частях.
    Проходов не больше трёх: дальше список уже не сокращается, а вызовы стоят.
    """
    source_ids = []
    for _ in range(3):
        if len(clusters) <= window:
            merged, source_id = _merge_window(run_id, clusters, choice, root, who, window=window, min_window=min_window)
            source_ids.append(source_id)
            return merged, source_ids[0]
        merged = []
        for start in range(0, len(clusters), window):
            part, source_id = _merge_window(run_id, clusters[start:start + window], choice, root, who,
                                            window=window, min_window=min_window)
            merged.extend(part)
            source_ids.append(source_id)
        if len(merged) == len(clusters):
            return merged, source_ids[0]
        clusters = merged
    return clusters, source_ids[0]


def _merge_window(run_id, clusters, choice, root, who, *, window, min_window):
    listing = [{"index": i, "name": c["name"], "job": c["job"], "head_phrase": c["head_phrase"], "phrases": len(c["phrases"])}
               for i, c in enumerate(clusters)]
    messages = [{"role": "system", "content": MERGE_SYSTEM},
                {"role": "user", "content": json.dumps(listing, ensure_ascii=False)}]
    try:
        parsed, source_id, _ = ask_json(run_id, "cluster-merge", messages, model_choice=choice, root=root, who=who)
    except TruncatedReply:
        if len(clusters) <= min_window:
            raise
        half = len(clusters) // 2
        left, source_id = _merge_window(run_id, clusters[:half], choice, root, who, window=window, min_window=min_window)
        right, _ = _merge_window(run_id, clusters[half:], choice, root, who, window=window, min_window=min_window)
        return left + right, source_id
    merged, used = [], set()
    for group in (parsed.get("groups") if isinstance(parsed, dict) else []) or []:
        members = [i for i in group.get("members") or [] if isinstance(i, int) and 0 <= i < len(clusters) and i not in used]
        if not members:
            continue
        used.update(members)
        base = dict(clusters[members[0]])
        for i in members[1:]:
            base["phrases"] = base["phrases"] + [p for p in clusters[i]["phrases"] if p not in base["phrases"]]
        base["name"] = str(group.get("name") or base["name"]).strip()[:80]
        base["job"] = str(group.get("job") or base["job"]).strip()
        if group.get("product_shape") in SHAPES:
            base["product_shape"] = group["product_shape"]
        if group.get("intent") in INTENTS:
            base["intent"] = group["intent"]
        merged.append(base)
    for i, cluster in enumerate(clusters):
        if i not in used:
            merged.append(cluster)
    return merged, source_id


MODIFIERS = {"free", "api", "tool", "tools", "software", "saas", "open", "source", "self", "hosted", "template", "templates",
             "checker", "calculator", "validator", "generator", "converter", "audit", "linter", "monitor", "best", "top",
             "service", "services", "app", "apps", "agency", "agencies", "company", "companies", "cost", "price", "pricing",
             "checklist", "guide", "tutorial", "example", "examples", "online", "vs", "alternative", "alternatives",
             "review", "reviews", "near", "me", "uk", "india", "usa", "2024", "2025", "2026", "how", "to", "what", "is", "a",
             "the", "for", "with", "in", "of", "and", "my", "your", "can", "does", "do", "i", "you", "it", "meaning", "definition"}
COMMERCIAL = {"tool", "tools", "software", "saas", "pricing", "price", "cost", "alternative", "alternatives", "service", "services",
              "agency", "app", "apps", "company", "vs", "buy", "plugin", "extension", "platform", "solution"}
TOOL_SHAPED = {"checker", "calculator", "validator", "generator", "converter", "template", "templates", "audit", "linter", "monitor", "tester"}


def heuristic_clusters(records, config):
    """Кластеризация без модели: общая «сердцевина» фразы после снятия модификаторов.

    records — записи стадии разворота (phrase, source, operator) или просто строки. Намерение и головная
    фраза считаются только по фразам из autocomplete: операторные фразы («seed checker») — наши
    заготовки, а не наблюдённый спрос, и голосовать не должны. Помечено method=heuristic.
    """
    rows = [r if isinstance(r, dict) else {"phrase": r, "source": "autocomplete", "operator": "autocomplete"} for r in records]
    groups = {}
    for row in rows:
        words = [w for w in re.findall(r"[a-z0-9+#.]+", row["phrase"].lower())]
        core = [w for w in words if w not in MODIFIERS] or words[:2]
        groups.setdefault(" ".join(core[:4]), []).append(row)
    out = []
    for key, members in groups.items():
        observed = [m for m in members if m.get("source") not in ("operator", "config")] or members
        lowered = [w for m in observed for w in m["phrase"].lower().split()]
        share_tool = sum(w in TOOL_SHAPED for w in lowered) / max(1, len(observed))
        share_comm = sum(w in COMMERCIAL for w in lowered) / max(1, len(observed))
        intent = "tool_shaped" if share_tool >= 0.25 else "commercial" if share_comm >= 0.25 else "informational"
        shape = "tool" if intent in ("commercial", "tool_shaped") else "content"
        head = min(observed, key=lambda m: (len(m["phrase"].split()),
                                            0 if any(w in COMMERCIAL | TOOL_SHAPED for w in m["phrase"].lower().split()) else 1,
                                            len(m["phrase"])))["phrase"]
        out.append({"name": key.title()[:80], "job": f"Задача вокруг «{key}» (эвристическая группировка, без модели)",
                    "product_shape": shape, "intent": intent, "head_phrase": head,
                    "phrases": sorted({m["phrase"] for m in members}), "observed_phrases": len(observed),
                    "source_id": None, "method": "heuristic"})
    return out


def run(folder, config, themes, *, root, method="llm"):
    run_id = folder.name
    settings = config.get("cluster") or {}
    phrases_all = read_stage_jsonl(folder, "expand", "phrases.jsonl")
    if not phrases_all:
        raise ValueError("stage expand produced no phrases; run expand first")
    out, ids, per_theme, source_ids, usage = [], set(), {}, [], {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    llm_error, used_method = None, method
    for theme in themes:
        theme_rows = [p for p in phrases_all if p["theme"] == theme["id"]]
        phrases = [p["phrase"] for p in theme_rows]
        if not phrases:
            continue
        if used_method == "llm":
            try:
                clusters, srcs, used = cluster_phrases(run_id, phrases, config, root=root)
                source_ids.extend(srcs)
                for key in usage:
                    usage[key] += used[key]
            except LLMError as error:
                if not settings.get("fallback_heuristic", True):
                    raise
                llm_error, used_method = f"{error.kind}: {str(error)[:200]}", "heuristic"
                clusters = heuristic_clusters(theme_rows, config)
        else:
            clusters = heuristic_clusters(theme_rows, config)
        kept = 0
        for cluster in clusters:
            if cluster.get("observed_phrases", len(cluster["phrases"])) < int(settings.get("min_phrases") or 4):
                continue
            key = backlog.key_for(cluster["name"])
            cluster_id = "cl-" + key
            suffix = 2
            while cluster_id in ids:
                cluster_id = f"cl-{key}-{suffix}"; suffix += 1
            ids.add(cluster_id)
            record, existed = backlog.touch(config, {"name": cluster["name"], "key": key, "theme": theme["id"]}, run_id, root=root)
            out.append({"cluster_id": cluster_id, "theme": theme["id"], **cluster, "key": key,
                        "backlog_id": record["backlog_id"], "backlog_status": "refresh" if existed else "new",
                        "backlog_previous": record.get("last_numbers") if existed else None})
            kept += 1
        per_theme[theme["id"]] = kept
    out.sort(key=lambda c: (-len(c["phrases"]), c["cluster_id"]))
    out = out[: int(settings.get("max_clusters") or 60)]
    write_stage_jsonl(folder, "cluster", "clusters.jsonl", out)
    summary = {"clusters": len(out), "per_theme": per_theme, "already_in_backlog": sum(c["backlog_status"] == "refresh" for c in out),
               "phrases_clustered": sum(len(c["phrases"]) for c in out), "llm_calls": len(source_ids), "tokens": usage,
               "method": used_method, "llm_error": llm_error}
    finish_stage(folder, "cluster", summary)
    return summary


def summary_text(summary):
    text = (f"{summary['clusters']} кластеров из {summary['phrases_clustered']} фраз; "
            f"уже в бэклоге {summary['already_in_backlog']}; токенов {summary['tokens'].get('total_tokens', 0)}")
    if summary.get("method") == "heuristic":
        text += f"; ВНИМАНИЕ: кластеризация эвристическая без модели ({summary.get('llm_error') or 'метод задан явно'})"
    return text
