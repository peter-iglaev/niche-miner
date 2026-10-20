"""Стадия 7 — отчёт: карточки кластеров из данных, нарратив (черновик DeepSeek + правка оркестратора), PDF, доставка."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.artifacts import atomic_text, file_hash, write_json
from scripts.pipeline.llmjson import ask_json
from scripts.pipeline.run import STAGE_RU, load_manifest, now, read_stage_jsonl, record_event, save_manifest, finish_stage
from scripts.report_pdf import render_report_pdf, report_stem, validate_report_pdf, record_pdf_review

BEGIN, END = "<!-- machine:begin -->", "<!-- machine:end -->"
PLACEHOLDER = "_Заполните_"
NUMBERS_STATUS = {"deferred_quota": "отложено: дневная квота Ubersuggest исчерпана, запрос в очереди на завтра",
                  "pending_bridge": "ждёт ответа моста Ubersuggest", "not_collected": "провайдер чисел не подключён"}
DECISION_RU = {"build_test": "строить проверочную страницу", "investigate": "исследовать дальше", "dropped": "закрыт",
               "drop": "закрыт", "duplicate": "дубликат", None: "решения нет"}
GATE_RU = {"passed": "пройдены", "failed": "не пройдены", "unknown": "без чисел"}


def _objections(items):
    """Возражения судьи читает человек, поэтому не repr словаря, а «замечание — что с ним».

    Судья отдаёт их либо строками, либо парами {finding, resolution}; вторая форма попадала
    в PDF как питоновский литерал вместе с кавычками и фигурными скобками.
    """
    out = []
    for item in items:
        if isinstance(item, dict):
            finding = str(item.get("finding") or item.get("text") or "").strip()
            resolution = str(item.get("resolution") or "").strip()
            out.append(f"{finding} — {resolution}" if finding and resolution and resolution.lower() != "none" else finding or resolution)
        else:
            out.append(str(item).strip())
    return "; ".join(o for o in out if o) or "нет."


def _fmt(value, unit=""):
    if value is None:
        return "неизвестно"
    if isinstance(value, float):
        value = round(value, 2)
    return f"{value}{unit}"


def _cell(value):
    return "-" if value is None else str(value).replace("|", "\\|").replace("\n", " ")


def card(cluster, decision):
    numbers = cluster.get("numbers") or {}
    sig = cluster.get("signals") or {}
    money, soft, reach = sig.get("money") or {}, sig.get("software") or {}, sig.get("reachable") or {}
    up, fl = money.get("upwork") or {}, money.get("freelancer") or {}
    it = cluster.get("interpret") or {}
    sk = cluster.get("skeptic") or {}
    jd = cluster.get("judge") or {}
    price = it.get("price_point") or {}
    lines = [f"### {cluster.get('name')} (`{cluster['cluster_id']}`)", ""]
    lines.append(f"**Задача.** {it.get('job') or cluster.get('job') or 'неизвестно'}")
    lines.append(f"**Покупатель и триггер.** {it.get('buyer') or 'неизвестно'}; триггер: {it.get('trigger') or 'неизвестно'}.")
    lines.append(f"**Форма продукта и единица оплаты.** {it.get('product_shape') or cluster.get('product_shape')}; {it.get('payment_unit') or 'неизвестно'}. "
                 f"Ценовая точка: {_fmt(price.get('low'))}–{_fmt(price.get('high'))} {price.get('unit') or ''} ({price.get('basis') or 'основание не указано'}).")
    top = ", ".join(f"«{p['phrase']}» {p['volume']}" for p in (numbers.get("top_phrases") or [])[:3])
    share = numbers.get("volume_top_share")
    concentration = f" Доля крупнейшей фразы в объёме: {round(share * 100)}%." if isinstance(share, (int, float)) else ""
    yoy = numbers.get("trend_yoy")
    trend = (f"год к году {round(yoy * 100):+d}%" if isinstance(yoy, (int, float))
             else f"наклон {_fmt(numbers.get('trend_slope'))} (ряд короче двух лет, сезонность не вычтена)")
    lines.append(f"**Спрос.** Фраз в кластере: {len(cluster.get('phrases') or [])}; объём кластера: {_fmt(numbers.get('volume_recent') or numbers.get('volume_sum'), '/мес')} "
                 f"({numbers.get('provider') or NUMBERS_STATUS.get(numbers.get('status'), 'провайдер не задан')}); головная фраза «{cluster.get('head_phrase')}»: {_fmt(numbers.get('volume_head'), '/мес')}; "
                 f"тренд: {trend}.{concentration} {('Топ: ' + top + '.') if top else ''}")
    lines.append(f"**Деньги.** CPC медиана {_fmt(numbers.get('cpc_median'), ' USD')} "
                 f"(ставки top-of-page от {_fmt(numbers.get('cpc_low_median'), ' USD')} до {_fmt(numbers.get('cpc_high_median'), ' USD')}), "
                 f"максимум {_fmt(numbers.get('cpc_max'), ' USD')}. "
                 f"Upwork: {_fmt(up.get('jobs'))} заказов за окно, медианный бюджет {_fmt(up.get('median_budget'), ' USD')}"
                 f"{' (мост не исполнен)' if up.get('status') == 'pending_bridge' else ''}. "
                 f"Freelancer: {_fmt(fl.get('jobs'))} заказов, максимум {_fmt(fl.get('max_budget'), ' USD')}. "
                 f"Платные листинги с пользователями: {_fmt(soft.get('paid_with_users'))} из {_fmt(soft.get('listings'))}; гэп в маркетплейсах: {'да' if soft.get('gap') else 'нет' if soft.get('status') == 'collected' else 'неизвестно'}.")
    serp_rows = reach.get("phrases") or []
    serp_text = "; ".join(f"«{r['phrase']}»: " + ", ".join(f"{t['domain']} ({t['kind']})" for t in r['top'][:5]) for r in serp_rows[:1])
    lines.append(f"**Проходимость.** Брендов в топ-5: {_fmt(reach.get('brands_top5_max'))}; форумы/видео в топе: {'да' if reach.get('forums_present') else 'нет'}. {serp_text}")
    incumbents = ", ".join(f"{i.get('name')}" + (f" ({i.get('note')})" if i.get('note') else "") for i in (it.get("incumbents") or [])[:6]) or "не названы"
    lines.append(f"**Конкуренты в выдаче.** {incumbents}.")
    if it.get("differentiator_hypothesis"):
        lines.append(f"**Гипотеза отличия.** {it['differentiator_hypothesis']}")
    unresolved = jd.get("unresolved_objections") or [f["text"] for f in (sk.get("findings") or []) if f.get("severity") == "fatal"]
    lines.append("**Возражения скептика без ответа.** " + (_objections(unresolved) if unresolved else "нет."))
    card_ = jd.get("scorecard") or {}
    scores = ", ".join(f"{k} {(v.get('value') if isinstance(v, dict) else v)}" for k, v in card_.items()) or "нет"
    gate_flags = (cluster.get("gate") or {}).get("flags") or []
    lines.append(f"**Гейты и решение.** Гейты {GATE_RU.get((cluster.get('gate') or {}).get('status'), 'неизвестно')}"
                 f"{(': ' + ', '.join((cluster.get('gate') or {}).get('failed_rules') or [])) if (cluster.get('gate') or {}).get('failed_rules') else ''}"
                 f"{('; пометки: ' + '; '.join(gate_flags)) if gate_flags else ''}. "
                 f"Судья: {DECISION_RU.get(jd.get('decision_context'))}; карточка: {scores}. Решение: {DECISION_RU.get((decision or {}).get('status'))}.")
    if jd.get("rationale"):
        lines.append(f"**Обоснование судьи.** {jd['rationale']}")
    lines.append(f"**Самая дешёвая решающая проверка.** {it.get('cheapest_test') or 'не сформулирована'}")
    sources_n = len({s for key in ("source_ids",) for s in (soft.get(key) or []) + (reach.get(key) or [])} | set(numbers.get("source_ids") or []))
    lines.append(f"**Источники.** Архивировано {sources_n}+ ответов источников; утверждений interpret/skeptic: {len(it.get('claims') or [])}/{len(sk.get('claims') or [])}.")
    lines.append("")
    return "\n".join(lines)


def _gate_line(config, stages):
    """Пороги, по которым выносились вердикты, прямо в отчёте.

    Без них карточку нельзя перепроверить: гейт тренда судит относительно фона поиска, а фон
    снимается калибровкой и со временем меняется. Строка привязывает вердикты к числам.
    """
    gate = config.get("gate") or {}
    band = (config.get("cpc") or {}).get(config.get("mode")) or []
    fon = ((stages.get("gate") or {}).get("summary") or {}).get("baseline") or {}
    level = fon.get("trend_yoy_median")
    trend = f"год к году не ниже {gate.get('min_trend_yoy_vs_baseline')} от фона"
    trend += f" {level} (калибровка {fon.get('run')})" if level is not None else " (фон не снят)"
    return (f"объём от {gate.get('min_volume_sum')}/мес, CPC {band[0] if band else '?'}–{band[1] if len(band) > 1 else '?'} USD, "
            f"{trend}, интент {', '.join(gate.get('allowed_intents') or [])}.")


def build(folder, config):
    folder = Path(folder)
    manifest = load_manifest(folder)
    reviewed = read_stage_jsonl(folder, "review", "reviewed.jsonl")
    gated = read_stage_jsonl(folder, "gate", "gated.jsonl")
    decisions = manifest.get("decisions") or {}
    stages = manifest.get("stages") or {}
    lines = [f"# Отчёт по прогону {manifest['run_id']}", "",
             f"Режим: {manifest.get('mode')}; темы: {', '.join(manifest.get('themes') or [])}; создан {manifest.get('created_at', '')[:10]}.", "",
             "## Что получили", "", PLACEHOLDER + ": одним абзацем — есть ли кандидат для проверочной страницы, что показали цифры и деньги, куда направить следующий шаг.", "",
             "## Как шёл прогон", ""]
    for stage, entry in stages.items():
        summary = entry.get("summary") or {}
        lines.append(f"- **{STAGE_RU.get(stage, stage)}** ({entry.get('status')}): " + ", ".join(f"{k}={_cell(v)}" for k, v in summary.items() if not isinstance(v, (dict, list)))[:400])
    lines += ["", "## Кандидаты", ""]
    if reviewed:
        order = {"build_test": 0, "investigate": 1, "drop": 2}
        for cluster in sorted(reviewed, key=lambda c: order.get((c.get("judge") or {}).get("decision_context"), 3)):
            lines.append(card(cluster, decisions.get(cluster["cluster_id"])))
    else:
        lines.append("До стадии проверки ни один кластер не дошёл.")
    lines += ["## Что делать дальше", "", PLACEHOLDER + ": что осталось неизвестным, какие кластеры отложены и почему, какие проверки стоят ноль денег.", "",
              BEGIN, "", "## Проверяемая сводка", "",
              f"- Прогон `{manifest['run_id']}`, режим {manifest.get('mode')}, хэш конфига `{manifest.get('config_hash', '')[:16]}`.",
              # «Кластеров после гейтов» называло число дошедших до гейтов, а не прошедших их:
              # в прогоне creator-marketing-2026-09-22 это читалось как «52 прошли» при 25.
              f"- Кластеров дошло до гейтов: {len(gated)}; прошли гейты: {sum(1 for c in gated if (c.get('gate') or {}).get('passed'))}; "
              f"разобрано: {len(reviewed)}; решений: {len(decisions)}.",
              f"- Пороги гейтов: {_gate_line(config, stages)}",
              f"- Архив источников: {len(list((folder / 'sources').glob('*.meta.json')))} записей в `sources/`.", "",
              "| Кластер | Тема | Объём/мес | CPC | Upwork | Бренды топ-5 | Гейты | Судья | Решение |", "|---|---|---|---|---|---|---|---|---|"]
    by_id = {r["cluster_id"]: r for r in reviewed}
    for c in gated:
        # Биржу спрашивают на стадии проверки, поэтому её ответ лежит в разобранной карточке,
        # а не в gated.jsonl. Без этого измеренный ноль заказов выглядел бы в таблице прочерком,
        # то есть «не спрашивали», — а это ровно противоположный вывод.
        seen = by_id.get(c["cluster_id"]) or c
        n, sig = c.get("numbers") or {}, seen.get("signals") or c.get("signals") or {}
        up = (sig.get("money") or {}).get("upwork") or {}
        jd = (by_id.get(c["cluster_id"]) or {}).get("judge") or {}
        lines.append(f"| {_cell(c.get('name'))} | {_cell(c.get('theme'))} | {_cell(n.get('volume_recent') or n.get('volume_sum'))} | {_cell(n.get('cpc_median'))} | {_cell(up.get('jobs'))} | "
                     f"{_cell(((sig.get('reachable') or {}).get('brands_top5_max')))} | {GATE_RU.get((c.get('gate') or {}).get('status'), '-')} | "
                     f"{DECISION_RU.get(jd.get('decision_context')) if jd else '-'} | {DECISION_RU.get((decisions.get(c['cluster_id']) or {}).get('status'))} |")
    lines += ["", "Шкалы порядковые, не вероятности. CPC — ставка рекламодателя, не оплата покупателя; объёмы Keyword Planner склеивают варианты.", END, ""]
    text = "\n".join(lines)
    path = folder / f"{report_stem(folder)}.md"
    if path.exists():
        old = path.read_text(encoding="utf-8")
        text = _keep_narrative(old, text)
    atomic_text(path, text)
    return path


def _keep_narrative(old, new):
    """При пересборке сохраняем уже написанные секции «Что получили» и «Что делать дальше»."""
    def section(text, title):
        start = text.find(f"## {title}")
        if start < 0:
            return None
        rest = text[start + len(title) + 3:]
        end = min([i for i in (rest.find("\n## "), rest.find(BEGIN)) if i >= 0] or [len(rest)])
        body = rest[:end].strip()
        return None if not body or PLACEHOLDER in body else body
    for title in ("Что получили", "Что делать дальше"):
        body = section(old, title)
        if body:
            start = new.find(f"## {title}")
            rest = new[start + len(title) + 3:]
            end = min([i for i in (rest.find("\n## "), rest.find(BEGIN)) if i >= 0] or [len(rest)])
            new = new[:start + len(title) + 3] + "\n\n" + body + "\n\n" + rest[end:].lstrip("\n")
    return new


NARRATIVE_SYSTEM = """Ты пишешь две секции русского отчёта об исследовании ниш по готовым карточкам кластеров. Не добавляй фактов,
которых нет в карточках; числа бери из них дословно; неизвестное называй неизвестным. Тон — деловой, без воды.
Верни JSON: {"summary": "1 абзац для секции «Что получили»", "next": "2–4 абзаца для секции «Что делать дальше»"}."""


def draft_narrative(folder, config, *, root):
    """Черновик нарратива от DeepSeek по карточкам; оркестратор обязан прочитать и при необходимости поправить."""
    folder = Path(folder)
    text = (folder / f"{report_stem(folder)}.md").read_text(encoding="utf-8")
    start, end = text.find("## Кандидаты"), text.find("## Что делать дальше")
    cards = text[start:end] if start >= 0 and end > start else text
    stages = "\n".join(l for l in text.splitlines() if l.startswith("- **"))
    messages = [{"role": "system", "content": NARRATIVE_SYSTEM},
                {"role": "user", "content": f"Ход прогона:\n{stages}\n\nКарточки:\n{cards[:24000]}"}]
    parsed, source_id, usage = ask_json(folder.name, "narrative", messages, model_choice=(config.get("models") or {}).get("narrative"), root=root, max_tokens=4000)
    summary, nxt = str(parsed.get("summary") or "").strip(), str(parsed.get("next") or "").strip()
    if not summary or not nxt:
        raise ValueError("narrative draft is incomplete")
    for title, body in (("Что получили", summary), ("Что делать дальше", nxt)):
        s = text.find(f"## {title}")
        rest = text[s + len(title) + 3:]
        e = min([i for i in (rest.find("\n## "), rest.find(BEGIN)) if i >= 0] or [len(rest)])
        text = text[:s + len(title) + 3] + "\n\n" + body + "\n\n" + rest[e:].lstrip("\n")
    atomic_text(folder / f"{report_stem(folder)}.md", text)
    record_event(folder, "narrative-drafted", {"source_id": source_id, "tokens": usage.get("total_tokens")})
    return {"source_id": source_id, "tokens": usage.get("total_tokens"), "summary_chars": len(summary), "next_chars": len(nxt)}


def narrative_digest(text):
    """Хэш и объём человеческой части отчёта — всего, что вне машинных маркеров.

    Нарратив правится руками, и до сих пор от правки не оставалось машинного следа: в
    `events.jsonl` было видно, что отчёт пересобран, но не то, что текст переписан и кем.
    Хэш ловит любое изменение, включая исправление одной цифры.
    """
    outside = text.split(BEGIN)[0] + (text.split(END)[1] if END in text else "")
    normalized = "\n".join(line.rstrip() for line in outside.strip().splitlines())
    return {"sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(), "chars": len(normalized),
            "has_placeholder": PLACEHOLDER in normalized}


def render(folder, *, author=None):
    folder = Path(folder)
    stem = report_stem(folder)
    before = (folder / f"{stem}.md").read_text(encoding="utf-8") if (folder / f"{stem}.md").is_file() else ""
    meta = render_report_pdf(folder)
    digest = narrative_digest((folder / f"{stem}.md").read_text(encoding="utf-8"))
    if not before or narrative_digest(before)["sha256"] != digest["sha256"]:
        record_event(folder, "narrative:changed", {**digest, "author": author or "root", "report": stem})
    finish_stage(folder, "report", {"pages": meta["page_count"], "pdf_sha256": meta["pdf_sha256"][:16],
                                    "report": stem, "narrative_sha256": digest["sha256"][:16]})
    return meta


def finalize(folder):
    folder = Path(folder)
    manifest = load_manifest(folder)
    errors = []
    markdown = folder / f"{report_stem(folder)}.md"
    text = markdown.read_text(encoding="utf-8") if markdown.is_file() else ""
    if not text:
        errors.append(f"{markdown.name} is missing; run report first")
    if PLACEHOLDER in text:
        errors.append(f"{markdown.name} still has unfilled narrative placeholders (run narrative, then review the text)")
    reviewed = read_stage_jsonl(folder, "review", "reviewed.jsonl")
    missing = [r["cluster_id"] for r in reviewed if r["cluster_id"] not in (manifest.get("decisions") or {})]
    if missing:
        errors.append("decisions missing for: " + ", ".join(missing))
    errors.extend(validate_report_pdf(folder, require_review=True))
    if errors:
        raise ValueError("; ".join(errors))
    manifest.update(status="complete", finished_at=now(),
                    report={"name": report_stem(folder), "markdown_sha256": file_hash(markdown),
                            "pdf_sha256": file_hash(folder / f"{report_stem(folder)}.pdf")})
    save_manifest(folder, manifest)
    record_event(folder, "finalized", manifest["report"])
    summary = {"run_id": manifest["run_id"], "status": "complete", "reviewed": len(reviewed),
               "decisions": {k: v["status"] for k, v in (manifest.get("decisions") or {}).items()}, **manifest["report"]}
    write_json(folder / "summary.json", summary)
    return summary


def deliver(folder, config, *, root, caption=None):
    from scripts import telegram
    folder = Path(folder)
    manifest = load_manifest(folder)
    if not (config.get("notify") or {}).get("send_pdf", True):
        return {"skipped": True}
    decisions = manifest.get("decisions") or {}
    counts = {}
    for d in decisions.values():
        counts[d["status"]] = counts.get(d["status"], 0) + 1
    caption = caption or (f"[{manifest['run_id']}] отчёт: " + (", ".join(f"{DECISION_RU.get(k, k)} {v}" for k, v in counts.items()) or "кандидатов нет"))
    outcome = telegram.send_document(manifest["run_id"], str(folder / f"{report_stem(folder)}.pdf"), caption=caption[:1000], root=root)
    record_event(folder, "delivered", {"status": outcome.get("telegram_status"), "error": outcome.get("error")})
    return outcome
