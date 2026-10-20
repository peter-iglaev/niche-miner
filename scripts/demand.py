#!/usr/bin/env python3
"""Пайплайн v2 «от спроса» — одна точка входа для всех стадий.

    python3 scripts/demand.py doctor                       # что доступно, какие квоты остались
    python3 scripts/demand.py create --run <id> --themes a,b [--mode self_serve] --objective "..." --authorization "..."
    python3 scripts/demand.py expand|cluster|screen|measure|gate|review --run <id>
    python3 scripts/demand.py bridge --run <id> [--ingest]   # список MCP-вызовов для оркестратора / приём ответов
    python3 scripts/demand.py decide --run <id> --auto | --cluster <cid> --status <s> --reason "..."
    python3 scripts/demand.py report|narrative|pdf-review|finalize|deliver --run <id>
    python3 scripts/demand.py status --run <id>
    python3 scripts/demand.py auto --run <id>              # гнать стадии подряд, пока не понадобится мост
    python3 scripts/demand.py dashboard                    # собрать docs/demand-dashboard.html

Каждая стадия — своя папка runs/<id>/<n>-<stage>/ с summary.json; стадии можно перезапускать.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.llm import LLMError  # noqa: E402
from scripts.pipeline import bridge, cluster, expand, gate, measure, report, review, screen  # noqa: E402
from scripts.pipeline.config import load_config, select_themes  # noqa: E402
from scripts.report_pdf import report_stem  # noqa: E402
from scripts.pipeline.run import (create_run, load_config as frozen_config, load_manifest, notify, run_dir,  # noqa: E402
                                  stage_status, STAGE_RU)

STAGES = {"expand": expand, "cluster": cluster, "screen": screen, "measure": measure, "gate": gate, "review": review}


def _out(payload, ok=True):
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0 if ok else 1


def cmd_doctor(args):
    from scripts import llm, search, telegram
    from scripts.env import load_env
    from scripts.pipeline.connectors import bing, google_ads
    env = load_env(ROOT)
    config = load_config()
    left, used, day = measure.quota_left(config, ROOT)
    llm_state = llm.credentials("deepseek", root=ROOT)
    if llm_state.get("available"):
        # Один крошечный вызов: ловит пустой баланс (402) и мёртвый ключ до старта прогона.
        try:
            llm.complete([{"role": "user", "content": "ping"}], provider="deepseek", model=config["models"]["cluster"]["model"],
                         max_tokens=1, root=ROOT, timeout=30)
            llm_state["ping"] = "ok"
        except LLMError as error:
            llm_state["ping"] = f"FAILED ({error.kind}): {str(error)[:160]}"
            llm_state["available"] = False
    out = {"config": {"mode": config["mode"], "themes": [t["id"] for t in config["themes"]], "cpc_band": config["cpc"].get(config["mode"])},
           "llm": llm_state,
           "search": search.capabilities(ROOT), "telegram": telegram.credentials(ROOT),
           "google_ads": google_ads.availability(ROOT), "bing": bing.availability(ROOT),
           "upwork_bridge": {"org_uid_present": bool(env.get("UPWORK_ORG_UID", "").strip()),
                             "note": "MCP инструмент mcp__Upwork__upwork__find_jobs исполняет оркестратор; без UPWORK_ORG_UID мост сначала попросит list_accounts"},
           "ubersuggest_bridge": {"daily_reports_left_today": left, "used_today": used, "date": day,
                                  "note": "google_suggestions квоту не тратит; keyword_suggestions/overview — по одному отчёту"},
           "free_sources": ["google/youtube/amazon autocomplete", "shopify autocomplete (может отдавать 429)", "freelancer.com", "apify store", "wordpress.org",
                            "atlassian marketplace", "vscode marketplace", "obsidian stats", "smithery", "hn algolia", "brave serp"]}
    return _out(out)


def cmd_create(args):
    config = load_config()
    themes = select_themes(config, (args.themes or "").split(",") if args.themes else None)
    mode = args.mode or config["mode"]
    if mode not in ("self_serve", "services", "any"):
        raise ValueError("mode must be self_serve, services or any")
    manifest = create_run(ROOT, args.run, config, themes, mode=mode, objective=args.objective or f"Поиск ниш от спроса по темам {', '.join(t['id'] for t in themes)}",
                          authorization=args.authorization or "Разрешение пользователя на исследовательский прогон по публичным источникам; контакты с покупателями, покупки и кампании не разрешены.")
    return _out({"run_id": manifest["run_id"], "mode": mode, "themes": manifest["themes"], "run_dir": str(run_dir(ROOT, args.run))})


def _stage(args, name):
    folder = run_dir(ROOT, args.run)
    config = frozen_config(folder)
    themes = config.get("themes") or []
    module = STAGES[name]
    if name == "cluster":
        summary = module.run(folder, config, themes, root=ROOT, method=getattr(args, "method", None) or "llm")
    elif name == "expand":
        summary = module.run(folder, config, themes, root=ROOT)
    elif name == "review":
        summary = module.run(folder, config, root=ROOT, only=(args.cluster.split(",") if getattr(args, "cluster", None) else None))
    elif name in ("screen", "measure"):
        summary = module.run(folder, config, root=ROOT, refresh=bool(getattr(args, "refresh", False)))
    else:
        summary = module.run(folder, config, root=ROOT)
    text = module.summary_text(summary)
    notice = notify(folder, config, name, text)
    pending = bridge.pending(folder)
    payload = {"stage": name, "status": stage_status(folder, name), "summary": summary, "telegram": notice.get("telegram_status", "skipped")}
    if pending:
        payload["bridge_pending"] = bridge.instructions(folder)["count"]
        payload["next"] = "выполни `python3 scripts/demand.py bridge --run ...` инструменты MCP, затем `bridge --ingest` и повтори стадию"
    return _out(payload)


def cmd_bridge(args):
    folder = run_dir(ROOT, args.run)
    if args.ingest:
        return _out(bridge.ingest(folder, root=ROOT))
    info = bridge.instructions(folder)
    print(info["text"] or "мост пуст: исполнять нечего")
    return _out({"pending": info["count"], "requests": [{k: r[k] for k in ("id", "mcp_tool", "args", "response_path", "purpose")} for r in info["requests"]]})


def cmd_decide(args):
    folder = run_dir(ROOT, args.run)
    config = frozen_config(folder)
    if args.auto:
        return _out(review.decide_auto(folder, config, root=ROOT))
    if not (args.cluster and args.status and args.reason):
        raise ValueError("decide needs --auto or --cluster, --status and --reason")
    return _out(review.decide(folder, config, args.cluster, args.status, args.reason, author=args.author or "root", root=ROOT))


def cmd_report(args):
    folder = run_dir(ROOT, args.run)
    config = frozen_config(folder)
    path = report.build(folder, config)
    meta = report.render(folder, author=args.author or "root")
    notice = notify(folder, config, "report", f"PDF собран, страниц {meta['page_count']}")
    return _out({"report": str(path), "pdf": str(folder / f"{report_stem(folder)}.pdf"), "pages": meta["page_count"],
                 "narrative_required": report.PLACEHOLDER in path.read_text(encoding="utf-8"), "telegram": notice.get("telegram_status", "skipped")})


def cmd_narrative(args):
    folder = run_dir(ROOT, args.run)
    config = frozen_config(folder)
    out = report.draft_narrative(folder, config, root=ROOT)
    meta = report.render(folder, author=args.author or "narrative(model draft)")
    return _out({**out, "pages": meta["page_count"], "note": f"черновик модели; прочитай {report_stem(folder)}.md и поправь перед pdf-review"})


def cmd_pdf_review(args):
    folder = run_dir(ROOT, args.run)
    from scripts.pipeline.run import record_event
    receipt = record_pdf_review_wrapper(folder, args.author or "root", args.reason or "", pages=getattr(args, "pages", None))
    record_event(folder, "pdf-reviewed", receipt)
    return _out(receipt)


def record_pdf_review_wrapper(folder, author, note, *, pages=None):
    from scripts.report_pdf import record_pdf_review
    return record_pdf_review(folder, author, note, pages=pages)


def cmd_finalize(args):
    folder = run_dir(ROOT, args.run)
    return _out(report.finalize(folder))


def cmd_deliver(args):
    folder = run_dir(ROOT, args.run)
    config = frozen_config(folder)
    out = report.deliver(folder, config, root=ROOT, caption=args.caption)
    return _out(out, ok=out.get("telegram_status", "sent") == "sent" or out.get("skipped", False))


def cmd_status(args):
    folder = run_dir(ROOT, args.run)
    manifest = load_manifest(folder)
    stages = {k: {"status": v["status"], **{kk: vv for kk, vv in (v.get("summary") or {}).items() if not isinstance(vv, (dict, list))}}
              for k, v in (manifest.get("stages") or {}).items()}
    pending = bridge.pending(folder)
    return _out({"run_id": manifest["run_id"], "status": manifest["status"], "mode": manifest["mode"], "themes": manifest["themes"],
                 "stages": stages, "decisions": manifest.get("decisions"), "bridge_pending": len(pending),
                 "next_stage": next((s for s in STAGES if s not in (manifest.get("stages") or {})), "report")})


def cmd_auto(args):
    """Гнать стадии подряд; остановиться, когда нужен мост или стадия завершилась с блокером."""
    folder = run_dir(ROOT, args.run)
    config = frozen_config(folder)
    themes = config.get("themes") or []
    done = []
    for name, module in STAGES.items():
        status = stage_status(folder, name)
        if status == "done":
            done.append(name); continue
        if name == "cluster":
            summary = module.run(folder, config, themes, root=ROOT, method=args.method or "llm")
        elif name == "expand":
            summary = module.run(folder, config, themes, root=ROOT)
        else:
            summary = module.run(folder, config, root=ROOT)
        notify(folder, config, name, module.summary_text(summary))
        done.append(name)
        if stage_status(folder, name) != "done":
            info = bridge.instructions(folder)
            print(info["text"])
            return _out({"stopped_at": name, "status": stage_status(folder, name), "bridge_pending": info["count"],
                         "next": "исполни MCP-вызовы моста, `bridge --ingest`, затем снова `auto`"})
    return _out({"done": done, "next": "decide --auto → report → narrative → pdf-review → finalize → deliver"})


def cmd_dashboard(args):
    from scripts.pipeline.viewer import build
    return _out(build(ROOT))


def cmd_backlog(args):
    from scripts.pipeline import backlog
    config = load_config()
    records = backlog.load(config, ROOT)
    rows = sorted(records.values(), key=lambda r: (r.get("status"), r.get("key")))
    if args.status:
        rows = [r for r in rows if r.get("status") == args.status]
    return _out({"count": len(rows), "clusters": [{k: r.get(k) for k in ("backlog_id", "key", "theme", "status", "last_seen_run", "last_numbers", "decision")} for r in rows]})


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["doctor", "create", *STAGES, "bridge", "decide", "report", "narrative", "pdf-review",
                                            "finalize", "deliver", "status", "auto", "dashboard", "backlog"])
    parser.add_argument("--run")
    parser.add_argument("--themes")
    parser.add_argument("--mode")
    parser.add_argument("--objective")
    parser.add_argument("--authorization")
    parser.add_argument("--ingest", action="store_true")
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--cluster")
    parser.add_argument("--status")
    parser.add_argument("--reason")
    parser.add_argument("--author")
    parser.add_argument("--caption")
    parser.add_argument("--pages", help="pdf-review: какие страницы просмотрены, например 1-17 или 1,4,9-11; по умолчанию все")
    parser.add_argument("--method", choices=["llm", "heuristic"], help="cluster: способ кластеризации (по умолчанию llm с фолбэком)")
    parser.add_argument("--refresh", action="store_true", help="screen/measure: собрать заново, не переиспользуя прошлый результат и числа из бэклога")
    args = parser.parse_args()
    handlers = {"doctor": cmd_doctor, "create": cmd_create, "bridge": cmd_bridge, "decide": cmd_decide, "report": cmd_report,
                "narrative": cmd_narrative, "pdf-review": cmd_pdf_review, "finalize": cmd_finalize, "deliver": cmd_deliver,
                "status": cmd_status, "auto": cmd_auto, "dashboard": cmd_dashboard, "backlog": cmd_backlog}
    try:
        if args.command in STAGES:
            if not args.run:
                raise ValueError("--run is required")
            return _stage(args, args.command)
        if args.command not in ("doctor", "dashboard", "backlog") and not args.run:
            raise ValueError("--run is required")
        return handlers[args.command](args)
    except LLMError as error:
        payload = {"ok": False, "error": f"model provider failed ({error.kind}): {error}",
                   "hint": "Пополните баланс DeepSeek или смените provider/model в config/demand.yaml → models; стадии без модели можно продолжать."}
        if args.run:
            try:
                folder = run_dir(ROOT, args.run)
                from scripts.pipeline.run import record_event
                record_event(folder, f"blocked:{args.command}", {"error": str(error)[:300]})
                notify(folder, frozen_config(folder), "review" if args.command in ("review", "narrative") else args.command,
                       f"БЛОКЕР: модель недоступна ({error.kind}: {str(error)[:120]}). Стадия {args.command} остановлена.")
            except (ValueError, OSError):
                pass
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1
    except (ValueError, OSError, KeyError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
