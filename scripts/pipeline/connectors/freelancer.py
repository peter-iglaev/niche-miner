"""Freelancer.com: активные проекты с бюджетами без авторизации. Шумный дублёр Upwork."""
from __future__ import annotations

from scripts.pipeline.connectors.http import get_json, quote

NOTE = "Open freelance projects show that someone is paying a person for this work; budgets are asks, not settled payments."


def search(run_id, phrase, *, limit=50, pause_seconds=1.0, root=None, who=None):
    url = ("https://www.freelancer.com/api/projects/0.1/projects/active/"
           f"?query={quote(phrase)}&limit={limit}&job_details=true&compact=true")
    record, parsed = get_json(run_id, "freelancer_projects", url, query=phrase, limitations=NOTE,
                              pause_seconds=pause_seconds, root=root, who=who)
    jobs = []
    for project in ((parsed or {}).get("result") or {}).get("projects") or []:
        budget = project.get("budget") or {}
        currency = project.get("currency") or {}
        rate = currency.get("exchange_rate") or 1.0
        jobs.append({"title": project.get("title"), "type": project.get("type"),
                     "budget_min_usd": round((budget.get("minimum") or 0) * rate, 2) if budget.get("minimum") is not None else None,
                     "budget_max_usd": round((budget.get("maximum") or 0) * rate, 2) if budget.get("maximum") is not None else None,
                     "bids": (project.get("bid_stats") or {}).get("bid_count"),
                     "bid_avg_usd": round(((project.get("bid_stats") or {}).get("bid_avg") or 0) * rate, 2) or None,
                     "submitted": project.get("time_submitted"), "url": f"https://www.freelancer.com/projects/{project.get('seo_url')}"})
    return {"source_id": record["source_id"], "status": record["retrieval_status"], "jobs": jobs,
            "total": ((parsed or {}).get("result") or {}).get("total_count")}
