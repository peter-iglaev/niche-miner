"""Один вызов модели, от которой ждут JSON. Сырой ответ архивируется в sources/ прогона."""
from __future__ import annotations

import json
import re

from scripts import sources
from scripts.llm import complete

NOTE = "Model output is an interpretation of archived inputs, never an observation about the market."


class TruncatedReply(ValueError):
    """Ответ модели упёрся в лимит вывода и оборвался на полуслове.

    Это не проблема формата: повторять тот же запрос бессмысленно, надо уменьшать порцию
    входных данных. Вызывающий код ловит это отдельно от обычной ошибки разбора.
    """


def extract_json(text):
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except ValueError:
                continue
    raise ValueError("model reply contains no parsable JSON")


def ask_json(run_id, role, messages, *, model_choice, root=None, temperature=0.2, max_tokens=8000, retries=1, who=None):
    """Вызов + разбор JSON + архив. Возвращает (parsed, source_id, usage). При неудаче — ValueError."""
    root = root or sources.ROOT
    provider, model = model_choice["provider"], model_choice["model"]
    last_error = None
    for attempt in range(retries + 1):
        answer = complete(messages, provider=provider, model=model, reasoning_effort=model_choice.get("reasoning_effort"),
                          temperature=temperature, max_tokens=max_tokens, root=root)
        content = answer["message"].get("content") or ""
        finish_reason = answer.get("finish_reason")
        body = json.dumps({"role": role, "provider": provider, "model": answer.get("model") or model,
                           "messages": messages, "reply": content, "usage": answer.get("usage"),
                           "finish_reason": finish_reason}, ensure_ascii=False).encode("utf-8")
        record = sources.archive(run_id, f"llm:{provider}", f"llm://{provider}/{model}/{role}", query=role, status=200,
                                 content_type="application/json", body=body, limitations=NOTE, root=root, **(who or {}))
        try:
            return extract_json(content), record["source_id"], answer.get("usage") or {}
        except ValueError as error:
            if finish_reason == "length":
                raise TruncatedReply(f"{role}: ответ оборван на лимите вывода "
                                     f"({(answer.get('usage') or {}).get('completion_tokens')} токенов); нужна меньшая порция") from error
            last_error = error
            messages = messages + [{"role": "assistant", "content": content},
                                   {"role": "user", "content": "Ответ не разобрался как JSON. Верни только валидный JSON без пояснений."}]
    raise ValueError(f"{role}: {last_error}")
