#!/usr/bin/env python3
"""Deterministic Russian report skeleton. Codex writes the narrative; Python guards the machine part."""
from __future__ import annotations

import re
from pathlib import Path

from scripts.artifacts import atomic_text
from scripts.validate import read_data

BEGIN = "<!-- machine:begin -->"
END = "<!-- machine:end -->"
CLAIM_TOKEN = re.compile(r"claim:([A-Za-z0-9][A-Za-z0-9._:-]*)")
ATTEMPT_TOKEN = re.compile(r"attempt:([^\s`)\]]+)")
NARRATIVE = """# Решение по исследованию {run_id}

## Что получили

_Заполните: одним абзацем — есть ли достойный следующий кандидат, насколько завершено исследование и куда направить следующий бюджет._

## Состояние исследования

_Заполните: завершён ли исследовательский план; отдельно перечислите технические сбои и честные результаты «данных нет»._

## Лучшие кандидаты

{cards}

## Что делать дальше

_Заполните: что осталось неизвестным, какие выводы отложены и какие проверки разрешены._

{machine}
"""

STATUS_RU = {"qualified": "коммерчески подтверждён", "deferred": "отложен", "ineligible": "не проходит условия",
             "rejected": "отклонён", "duplicate": "дубликат", "pass": "подтверждено", "fail": "не подтверждено",
             "unknown": "неизвестно", "high": "высокий", "medium": "средний", "low": "низкий",
             "blocked": "заблокирован", "review_required": "нужен разбор противоречий"}


def _read_optional(path):
    return read_data(path) if path.exists() else {}


def _priority(folder, candidate_id, claims, config):
    score = _read_optional(folder / "candidates" / candidate_id / "score.json")
    if score.get("investigation_priority"):
        return score["investigation_priority"]
    from scripts.score import investigation_priority_from_claims
    return investigation_priority_from_claims(candidate_id, claims, config.get("scoring", config))


def _best_candidates(folder, manifest, claims, config):
    priority_order = {"high": 0, "medium": 1, "review_required": 2, "low": 3, "unknown": 4, "blocked": 5}
    decision_order = {"qualified": 0, "deferred": 1, "rejected": 2, "duplicate": 3}
    decisions = {decision["candidate_id"]: decision for decision in manifest["decisions"]}
    depth = {candidate_id: sum(
        assignment["candidate_id"] == candidate_id and
        next(attempt for attempt in assignment["attempts"] if attempt["number"] == assignment["current_attempt"])["status"] == "accepted"
        for assignment in manifest["assignments"])
        for candidate_id in manifest["candidate_hashes"]}
    return sorted(manifest["candidate_hashes"], key=lambda cid: (
        decision_order.get(decisions.get(cid, {}).get("status"), 4),
        -depth[cid],
        priority_order.get(_priority(folder, cid, claims, config).get("tier"), 4),
        cid))[:5]


def _narrative_cards(folder, manifest, claims, config):
    lines = []
    fields = ["Как выглядит бизнес", "Покупатель и момент запуска", "Почему интересен", "Существующие расходы", "Боль и частота", "Конкуренция",
              "Дистрибуция", "Реализуемость", "Главная неизвестная", "Приоритет исследования",
              "Коммерческий статус", "Самая дешёвая решающая проверка"]
    for candidate_id in _best_candidates(folder, manifest, claims, config):
        lines.extend([f"### {candidate_id}", ""])
        for label in fields:
            lines.extend([f"**{label}.** Заполните: краткий вывод по принятым материалам; укажите claim:ID для фактов или явно «неизвестно».", ""])
    return "\n".join(lines) if lines else "Принятых кандидатов нет. Рекомендации отсутствуют."


def _cell(value):
    text = "-" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def build_machine_block(folder, manifest, config, claims, coverage=None):
    """Compact decision facts, while full evidence stays in canonical artifacts."""
    folder = Path(folder)
    from scripts.validate import execution_health
    health = execution_health(manifest, folder)
    health_ru = {"healthy": "технически целостен", "running": "есть активная работа", "blocked": "не завершён из-за технических сбоев"}
    lines = [BEGIN, "", "## Проверяемая сводка", "", f"- Прогон: `{manifest['run_id']}`",
             f"- Принято кандидатов: {len(manifest['candidate_hashes'])}; решений: {len(manifest['decisions'])}.",
             f"- Утверждений: {len(claims)}. Полные наблюдения и источники: [реестр доказательств](claims.jsonl).",
             f"- Состояние исполнения: **{health_ru[health['state']]}**; принятых заданий: {health['accepted_assignments']}; технических блокеров: {len(health['blocking_assignments'])}.",
             "- Приоритет исследования распределяет следующий бюджет; коммерческий статус определяет готовность к эксперименту.", ""]
    if health["blocking_assignments"]:
        lines.extend(["### Технические блокеры", "",
                      "Эти пункты означают незавершённую работу, а не отсутствие рынка или оплаты.", ""])
        for blocker in health["blocking_assignments"]:
            lines.append(f"- `{blocker['assignment_id']}`: {blocker['status']}; {blocker['reason'] or 'причина не записана'}")
        lines.append("")
    search_failures = {k: v for k, v in health["technical_search_failures"].items() if v}
    if search_failures or health["no_data_queries"]:
        lines.extend(["### Результаты получения данных", "",
                      "- Технические ошибки поиска: " + (", ".join(f"{k}={v}" for k, v in sorted(search_failures.items())) or "нет") + ".",
                      f"- Корректно выполненные запросы без данных: {health['no_data_queries']}. Это исследовательский результат, а не технический сбой.", ""])
    if manifest.get("quota_override"):
        lines.append(f"- Исключение из портфельных квот: {manifest['quota_override']['reason']}")
    decisions = {d["candidate_id"]: d for d in manifest["decisions"]}
    lines.extend(["", "| Кандидат | Приоритет исследования | Уверенность в расходах | Прямота транзакции | Коммерческий статус |",
                  "|---|---|---|---|---|"])
    shortlisted = _best_candidates(folder, manifest, claims, config)
    for cid in shortlisted:
        score = _read_optional(folder / "candidates" / cid / "score.json")
        priority = _priority(folder, cid, claims, config)
        commercial = score.get("commercial_qualification", {}).get(
            "status", decisions.get(cid, {}).get("status", "deferred"))
        if commercial == "qualified" and decisions.get(cid, {}).get("status") != "qualified":
            commercial = "deferred"
        lines.append(f"| {_cell(cid)} | {STATUS_RU.get(priority.get('tier'), 'неизвестно')} | "
                     f"{_cell(score.get('payment_confidence', priority.get('payment_confidence')))} / 5 | "
                     f"{_cell(score.get('transaction_evidence_strength', priority.get('transaction_evidence_strength')))} / 3 | "
                     f"{STATUS_RU.get(commercial, commercial)} |")
    if not shortlisted:
        lines.append("| Нет принятых кандидатов | неизвестно | - | - | отложен |")
    lines.extend(["", "Шкалы порядковые, не вероятности. Публичная цена сама по себе не подтверждает платное использование.",
                  "Ниже приведены ссылки для проверки; полный реестр кандидатов и решений находится в [манифесте](manifest.json).", ""])
    for cid in shortlisted:
        selected_claims = [c["id"] for c in claims if c["candidate_id"] == cid][:3]
        cited = ", ".join("claim:" + claim_id for claim_id in selected_claims)
        lines.append(f"- `{cid}`: [досье](candidates/{cid}/hypothesis.json). Примеры принятых наблюдений: {cited or 'нет'}")
        decision = decisions.get(cid)
        if decision:
            lines.append(f"  Решение: {STATUS_RU.get(decision['status'], decision['status'])}.")
        score = _read_optional(folder / "candidates" / cid / "score.json")
        if score:
            lines.append(f"  Индекс задачи: {_cell(score.get('problem_index'))}; индекс бизнеса: {_cell(score.get('business_index'))}. "
                         f"Коммерческое ранжирование: {_cell(score.get('ranking_score'))}. Соответствие основателю оценивается отдельно.")
    lines.extend(["", "### Портфельный аудит", ""])
    if isinstance(coverage, dict):
        if coverage.get("mode") == "not_assessed" or manifest["scope"] == "targeted":
            lines.append("Для целевой проверки портфельные квоты не оцениваются.")
        else:
            status = STATUS_RU.get(coverage.get("status"), coverage.get("status", "неизвестно"))
            lines.append(f"Статус: {status}; кандидатов в знаменателе: {coverage.get('denominator', 0)}.")
            for flag, entry in sorted(coverage.get("quotas", {}).items()):
                lines.append(f"- `{flag}`: {entry.get('observed')} / порог {entry.get('threshold')}; {entry.get('status')}.")
    else:
        lines.append("Аудит не рассчитан.")
    familiarity = (coverage or {}).get("founder_familiarity", {})
    if familiarity:
        counts = familiarity.get("counts", {})
        lines.extend(["", f"Знакомство основателя с отраслями: {familiarity.get('denominator', 0)} кандидатов.",
                      f"- Подтверждённо знакомы: {counts.get('confirmed_familiar', 0)}.",
                      f"- Подтверждённо незнакомы: {counts.get('confirmed_unfamiliar', 0)}.",
                      f"- Неизвестно: {counts.get('unknown', 0)}.",
                      f"- Вне подтверждённой знакомой области: {counts.get('outside_confirmed_familiarity', 0)}.",
                      "Последняя категория подходит для расширения поиска, но не доказывает незнакомство с отраслью."])
    lines.extend(["", END])
    return "\n".join(lines)


def _split(text):
    start, end = text.find(BEGIN), text.find(END)
    if start < 0 or end < 0 or end < start:
        return None, None
    return text[:start], text[end + len(END):]


def write_report(folder, manifest, config, claims, coverage=None):
    folder = Path(folder)
    path = folder / "report.md"
    machine = build_machine_block(folder, manifest, config, claims, coverage)
    if path.exists():
        head, tail = _split(path.read_text(encoding="utf-8"))
        if head is None: raise ValueError("report.md exists without machine markers; refusing to overwrite")
        atomic_text(path, head + machine + tail)
    else:
        atomic_text(path, NARRATIVE.format(run_id=manifest["run_id"], machine=machine, cards=_narrative_cards(folder, manifest, claims, config)))
    return path


def validate_report(run_dir, manifest, claims, config, coverage=None):
    run_dir = Path(run_dir)
    path = run_dir / "report.md"
    if not path.exists(): return ["report.md is required before finalize"]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        return [f"cannot read report.md: {error}"]
    head, tail = _split(text)
    if head is None: return ["report.md must contain the machine markers"]
    errors = []
    current_block = build_machine_block(run_dir, manifest, config, claims, coverage)
    stored = text[text.find(BEGIN):text.find(END) + len(END)]
    if stored.strip() != current_block.strip():
        errors.append("report.md machine section is stale; run the report command again")
    claim_ids = {c["id"] for c in claims}
    for token in sorted(set(CLAIM_TOKEN.findall(text))):
        if token not in claim_ids: errors.append(f"report.md cites unknown claim {token}")
    known_attempts = set()
    for assignment in manifest["assignments"]:
        for attempt in assignment["attempts"]:
            for key in ("input_path", "result_path"):
                if attempt["status"] == "accepted" and attempt.get(key): known_attempts.add(attempt[key].lstrip("./"))
    for token in sorted(set(ATTEMPT_TOKEN.findall(text))):
        if token.lstrip("./") not in known_attempts: errors.append(f"report.md references unknown attempt {token}")
    if "Заполните" in head + tail:
        errors.append("report.md narrative contains unfilled sections")
    narrative = [line for line in (head + tail).splitlines()
                 if line.strip() and not line.lstrip().startswith("#") and "Заполните" not in line]
    if not any(len(line.strip()) >= 20 for line in narrative):
        errors.append("report.md needs a written narrative outside the machine section")
    return errors
