"""Дашборд v2: один автономный HTML со всеми прогонами «от спроса» и бэклогом. Сеть и сервер не нужны.

Перегенерация: python3 scripts/demand.py dashboard → docs/demand-dashboard.html
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.artifacts import atomic_text
from scripts.report_pdf import report_stem
from scripts.pipeline.run import STAGE_DIRS, STAGE_RU, read_json, read_jsonl

PAGE = "docs/demand-dashboard.html"


def _slim_cluster(c):
    sig = c.get("signals") or {}
    money, soft, reach = sig.get("money") or {}, sig.get("software") or {}, sig.get("reachable") or {}
    up, fl = money.get("upwork") or {}, money.get("freelancer") or {}
    n = c.get("numbers") or {}
    jd, sk, it = c.get("judge") or {}, c.get("skeptic") or {}, c.get("interpret") or {}
    return {"id": c["cluster_id"], "name": c.get("name"), "theme": c.get("theme"), "job": c.get("job"), "intent": c.get("intent"),
            "shape": c.get("product_shape"), "head": c.get("head_phrase"), "phrases": c.get("phrases") or [],
            "backlog": c.get("backlog_status"),
            "screen": c.get("screen"), "gate": c.get("gate"),
            "upwork": {"status": up.get("status"), "jobs": up.get("jobs"), "median": up.get("median_budget"), "sample": up.get("sample")},
            "freelancer": {"status": fl.get("status"), "jobs": fl.get("jobs"), "max": fl.get("max_budget"), "sample": fl.get("sample")},
            "software": {"status": soft.get("status"), "listings": soft.get("listings"), "paid": soft.get("paid_with_users"), "gap": soft.get("gap"), "top": soft.get("top")},
            "serp": {"status": reach.get("status"), "brands": reach.get("brands_top5_max"), "forums": reach.get("forums_present"), "phrases": reach.get("phrases")},
            "hn": (sig.get("hn") or {}).get("hits"),
            "numbers": {k: n.get(k) for k in ("status", "provider", "volume_sum", "volume_head", "cpc_median", "cpc_max", "competition_median", "trend_slope", "top_phrases", "reason")},
            "interpret": {k: it.get(k) for k in ("job", "buyer", "trigger", "product_shape", "payment_unit", "price_point", "incumbents", "differentiator_hypothesis", "cheapest_test")} if it else None,
            "skeptic": {"findings": sk.get("findings"), "gaps": sk.get("gaps")} if sk else None,
            "judge": {k: jd.get(k) for k in ("decision_context", "scorecard", "rationale", "unresolved_objections", "critical_unknowns")} if jd else None,
            "review": c.get("review")}


def collect(root):
    runs = []
    for folder in sorted((Path(root) / "runs").glob("*"), key=lambda p: p.name, reverse=True):
        manifest = read_json(folder / "manifest.json")
        if not manifest or manifest.get("kind") != "demand":
            continue
        stages = {}
        for stage, sub in STAGE_DIRS.items():
            summary = read_json(folder / sub / "summary.json")
            if summary:
                stages[stage] = summary
        latest = None
        for stage, name in (("review", "reviewed.jsonl"), ("gate", "gated.jsonl"), ("measure", "measured.jsonl"),
                            ("screen", "screened.jsonl"), ("cluster", "clusters.jsonl")):
            rows = read_jsonl(folder / STAGE_DIRS[stage] / name)
            if rows:
                latest = (stage, rows); break
        clusters = []
        if latest:
            stage, rows = latest
            reviewed = {r["cluster_id"]: r for r in read_jsonl(folder / "6-review" / "reviewed.jsonl")}
            for row in rows:
                merged = {**row, **{k: v for k, v in reviewed.get(row["cluster_id"], {}).items() if k in ("interpret", "skeptic", "judge", "review")}}
                clusters.append(_slim_cluster(merged))
        bridge = read_jsonl(folder / "bridge" / "requests.jsonl")
        events = read_jsonl(folder / "events.jsonl")
        runs.append({"run_id": manifest["run_id"], "created_at": manifest.get("created_at"), "status": manifest.get("status"),
                     "mode": manifest.get("mode"), "themes": manifest.get("themes"), "objective": manifest.get("objective"),
                     "stages": stages, "manifest_stages": manifest.get("stages"), "decisions": manifest.get("decisions") or {},
                     "clusters": clusters, "cluster_stage": latest[0] if latest else None,
                     "bridge": [{k: r.get(k) for k in ("id", "tool", "status", "stage", "purpose")} for r in bridge],
                     "events": events[-40:], "sources": len(list((folder / "sources").glob("*.meta.json"))),
                     "report": (folder / f"{report_stem(folder)}.pdf").is_file(), "phrases": len(read_jsonl(folder / "1-expand" / "phrases.jsonl"))})
    backlog = read_jsonl(Path(root) / "backlog" / "clusters.jsonl")
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "runs": runs, "backlog": backlog,
            "stage_ru": STAGE_RU}


TEMPLATE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Demand pipeline</title>
<style>
:root{--bg:#f6f8fa;--card:#fff;--ink:#1f2933;--muted:#6b7a8a;--line:#dfe5ec;--accent:#1f6f8b;--ok:#2e8b57;--bad:#c0392b;--warn:#b7791f;--chip:#eef2f6}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0f1418;--card:#161d24;--ink:#e6edf3;--muted:#93a1b0;--line:#2a343f;--accent:#5cb3d1;--chip:#1f2a35}}
:root[data-theme=dark]{--bg:#0f1418;--card:#161d24;--ink:#e6edf3;--muted:#93a1b0;--line:#2a343f;--accent:#5cb3d1;--chip:#1f2a35}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{padding:16px;border-bottom:1px solid var(--line);display:flex;gap:12px;align-items:center;flex-wrap:wrap}
h1{font-size:18px;margin:0}select,button{font:inherit;padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink)}
main{padding:16px;max-width:1400px;margin:0 auto}.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px}.k{color:var(--muted);font-size:12px}.v{font-size:22px;font-weight:600}
.funnel{display:flex;gap:6px;align-items:flex-end;height:120px;margin:12px 0}.bar{flex:1;background:var(--accent);opacity:.85;border-radius:6px 6px 0 0;position:relative;min-height:4px}
.bar span{position:absolute;bottom:100%;left:0;right:0;text-align:center;font-size:12px;padding-bottom:4px}.bar b{position:absolute;top:100%;left:0;right:0;text-align:center;font-size:11px;color:var(--muted);padding-top:4px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{font-size:12px;color:var(--muted);cursor:pointer;white-space:nowrap}
tr.row{cursor:pointer}tr.row:hover{background:var(--chip)}.chip{display:inline-block;padding:2px 8px;border-radius:999px;background:var(--chip);font-size:12px;margin:1px}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}.muted{color:var(--muted)}
#detail{position:fixed;top:0;right:0;width:min(640px,100%);height:100%;overflow:auto;background:var(--card);border-left:1px solid var(--line);padding:16px;display:none;box-shadow:-8px 0 24px rgba(0,0,0,.15)}
#detail.open{display:block}#detail h2{margin-top:0;font-size:16px}#detail h3{font-size:13px;color:var(--muted);margin:14px 0 4px;text-transform:uppercase;letter-spacing:.04em}
pre{white-space:pre-wrap;background:var(--chip);padding:8px;border-radius:8px;font-size:12px}.tabs{display:flex;gap:8px;margin:12px 0}.tabs button.active{border-color:var(--accent);color:var(--accent)}
ul{margin:4px 0;padding-left:18px}small{color:var(--muted)}
</style></head><body>
<header><h1>Пайплайн «от спроса»</h1><select id="run"></select><span class="k" id="meta"></span>
<span style="flex:1"></span><div class="tabs"><button data-tab="run" class="active">Прогон</button><button data-tab="backlog">Бэклог</button></div></header>
<main><section id="tab-run"><div class="grid" id="stats"></div><div class="funnel" id="funnel"></div>
<div class="card" id="bridge" style="display:none"></div><table id="clusters"><thead><tr>
<th data-k="name">Кластер</th><th data-k="theme">Тема</th><th data-k="intent">Намерение</th><th data-k="phr">Фраз</th><th data-k="vol">Объём/мес</th><th data-k="cpc">CPC</th>
<th data-k="up">Upwork</th><th data-k="fl">Freelancer</th><th data-k="paid">Платные</th><th data-k="brands">Бренды топ-5</th><th data-k="screen">Отсев</th><th data-k="gate">Гейты</th><th data-k="judge">Судья</th><th data-k="dec">Решение</th></tr></thead><tbody></tbody></table>
<div class="card" style="margin-top:12px"><b>События</b><div id="events" class="k"></div></div></section>
<section id="tab-backlog" style="display:none"><table id="backlog"><thead><tr><th>Кластер</th><th>Тема</th><th>Статус</th><th>Последний прогон</th><th>Объём</th><th>CPC</th><th>Решение</th></tr></thead><tbody></tbody></table></section></main>
<aside id="detail"><button onclick="document.getElementById('detail').classList.remove('open')" style="float:right">закрыть</button><div id="detail-body"></div></aside>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent);const RU=D.stage_ru;
const $=s=>document.querySelector(s);const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt=v=>v==null?'<span class="muted">—</span>':(typeof v==='number'?(Math.round(v*100)/100).toLocaleString('ru-RU'):esc(v));
const DEC={build_test:'строить тест',investigate:'исследовать',dropped:'закрыт',drop:'закрыт',duplicate:'дубликат'};
let run=null,sortKey='vol',sortDir=-1;
function init(){const sel=$('#run');D.runs.forEach(r=>{const o=document.createElement('option');o.value=r.run_id;o.textContent=`${r.run_id} · ${r.status}`;sel.appendChild(o)});
sel.onchange=()=>show(sel.value);if(D.runs.length)show(D.runs[0].run_id);else $('#stats').innerHTML='<div class="card">Прогонов v2 ещё нет.</div>';
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{document.querySelectorAll('.tabs button').forEach(x=>x.classList.toggle('active',x===b));$('#tab-run').style.display=b.dataset.tab==='run'?'':'none';$('#tab-backlog').style.display=b.dataset.tab==='backlog'?'':'none'});
document.querySelectorAll('#clusters th').forEach(th=>th.onclick=()=>{const k=th.dataset.k;sortDir=sortKey===k?-sortDir:-1;sortKey=k;renderTable()});renderBacklog()}
function show(id){run=D.runs.find(r=>r.run_id===id);$('#meta').textContent=`${run.mode} · ${run.themes.join(', ')} · источников ${run.sources} · создан ${(run.created_at||'').slice(0,16).replace('T',' ')}`;
const s=run.stages;const counts=[['Фраз',run.phrases],['Кластеров',s.cluster?.clusters],['Прошли отсев',s.screen?.passed],['С цифрами',s.measure?.collected],['Прошли гейты',s.gate?.passed],['Проверено',s.review?.reviewed],['Решений',Object.keys(run.decisions).length]];
$('#stats').innerHTML=counts.map(([k,v])=>`<div class="card"><div class="k">${k}</div><div class="v">${v??'—'}</div></div>`).join('');
const max=Math.max(1,...counts.map(c=>c[1]||0));$('#funnel').innerHTML=counts.map(([k,v])=>`<div class="bar" style="height:${Math.max(4,100*(v||0)/max)}%"><span>${v??'—'}</span><b>${k}</b></div>`).join('');
const pend=run.bridge.filter(b=>b.status==='pending');$('#bridge').style.display=pend.length?'':'none';$('#bridge').innerHTML=`<b class="warn">Мост ждёт оркестратора:</b> ${pend.length} вызовов — `+pend.slice(0,6).map(b=>`<span class="chip">${esc(b.tool)} · ${esc(b.purpose)}</span>`).join(' ');
$('#events').innerHTML=run.events.slice().reverse().slice(0,25).map(e=>`<div>${esc((e.at||'').slice(11,19))} · ${esc(e.event)}</div>`).join('');renderTable()}
function val(c,k){const n=c.numbers||{};return {name:c.name,theme:c.theme,intent:c.intent,phr:c.phrases.length,vol:n.volume_sum,cpc:n.cpc_median,up:c.upwork.jobs,fl:c.freelancer.jobs,paid:c.software.paid,brands:c.serp.brands,screen:c.screen?.status,gate:c.gate?.status,judge:c.judge?.decision_context,dec:run.decisions[c.id]?.status}[k]}
function cls(v,good,bad){return v===good?'ok':v===bad?'bad':v==null?'muted':'warn'}
function renderTable(){const rows=run.clusters.slice().sort((a,b)=>{const x=val(a,sortKey),y=val(b,sortKey);if(x==null&&y==null)return 0;if(x==null)return 1;if(y==null)return -1;return (x>y?1:x<y?-1:0)*sortDir});
$('#clusters tbody').innerHTML=rows.map(c=>`<tr class="row" data-id="${esc(c.id)}"><td><b>${esc(c.name)}</b><br><small>${esc(c.head)}</small></td><td>${esc(c.theme)}</td><td>${esc(c.intent)}</td><td>${c.phrases.length}</td><td>${fmt(c.numbers.volume_sum)}</td><td>${fmt(c.numbers.cpc_median)}</td><td>${fmt(c.upwork.jobs)}${c.upwork.status==='pending_bridge'?' <span class="warn">мост</span>':''}</td><td>${fmt(c.freelancer.jobs)}</td><td>${fmt(c.software.paid)}${c.software.gap?' <span class="chip">гэп</span>':''}</td><td>${fmt(c.serp.brands)}</td><td class="${cls(c.screen?.status,'passed','failed')}">${esc(c.screen?.status)}</td><td class="${cls(c.gate?.status,'passed','failed')}">${esc(c.gate?.status)}</td><td class="${cls(c.judge?.decision_context,'build_test','drop')}">${esc(DEC[c.judge?.decision_context]||c.judge?.decision_context||'')}</td><td>${esc(DEC[run.decisions[c.id]?.status]||'')}</td></tr>`).join('');
document.querySelectorAll('#clusters tr.row').forEach(tr=>tr.onclick=()=>detail(tr.dataset.id))}
function list(a,f){return a&&a.length?'<ul>'+a.map(x=>`<li>${f?f(x):esc(typeof x==='string'?x:JSON.stringify(x))}</li>`).join('')+'</ul>':'<span class="muted">—</span>'}
function detail(id){const c=run.clusters.find(x=>x.id===id);const n=c.numbers,it=c.interpret,sk=c.skeptic,jd=c.judge;let h=`<h2>${esc(c.name)} <small>${esc(c.id)}</small></h2><div>${esc(c.job)}</div>`;
h+=`<h3>Фразы (${c.phrases.length})</h3><div>${c.phrases.map(p=>`<span class="chip">${esc(p)}</span>`).join(' ')}</div>`;
h+=`<h3>Цифры</h3><div>${n.status} · ${esc(n.provider||'')} · объём ${fmt(n.volume_sum)} (головная ${fmt(n.volume_head)}) · CPC ${fmt(n.cpc_median)} / макс ${fmt(n.cpc_max)} · конкуренция ${fmt(n.competition_median)} · тренд ${fmt(n.trend_slope)}${n.reason?`<br><small>${esc(n.reason)}</small>`:''}</div>${list(n.top_phrases,p=>`${esc(p.phrase)} — ${fmt(p.volume)}/мес, CPC ${fmt(p.cpc)}`)}`;
h+=`<h3>Деньги</h3><div>Upwork: ${esc(c.upwork.status)} · заказов ${fmt(c.upwork.jobs)} · медиана ${fmt(c.upwork.median)} USD</div>${list(c.upwork.sample,j=>`${esc(j.title)} — ${fmt(j.budget_usd)} USD · ${esc(j.proposals||'')} · клиент потратил ${fmt(j.client_spent)}`)}<div>Freelancer: заказов ${fmt(c.freelancer.jobs)} · макс ${fmt(c.freelancer.max)} USD</div>${list(c.freelancer.sample,j=>`${esc(j.title)} — ${fmt(j.budget_min_usd)}–${fmt(j.budget_max_usd)} USD (${esc(j.type)}, ставок ${fmt(j.bids)})`)}`;
h+=`<h3>Маркетплейсы</h3><div>листингов ${fmt(c.software.listings)} · платных с пользователями ${fmt(c.software.paid)} · гэп ${c.software.gap?'да':'нет'}</div>${list(c.software.top,l=>`<a href="${esc(l.url)}" target="_blank">${esc(l.name)}</a> — ${esc(l.source)} · пользователей ${fmt(l.users)} · ${l.paid===true?'платный':l.paid===false?'бесплатный':'?'} · ${esc(l.updated||'')}`)}`;
h+=`<h3>Выдача</h3><div>брендов в топ-5: ${fmt(c.serp.brands)} · форумы/видео: ${c.serp.forums?'да':'нет'} · HN: ${fmt(c.hn)}</div>`+(c.serp.phrases||[]).map(p=>`<div><b>${esc(p.phrase)}</b>${list(p.top,t=>`${t.rank}. <a href="${esc(t.url)}" target="_blank">${esc(t.domain)}</a> <span class="chip">${esc(t.kind)}</span>`)}</div>`).join('');
h+=`<h3>Отсев и гейты</h3><pre>${esc(JSON.stringify({screen:c.screen,gate:c.gate},null,1))}</pre>`;
if(it)h+=`<h3>Интерпретация</h3><div><b>Задача:</b> ${esc(it.job)}<br><b>Покупатель:</b> ${esc(it.buyer)}<br><b>Триггер:</b> ${esc(it.trigger)}<br><b>Форма/оплата:</b> ${esc(it.product_shape)} · ${esc(it.payment_unit)}<br><b>Цена:</b> ${esc(JSON.stringify(it.price_point))}<br><b>Отличие (гипотеза):</b> ${esc(it.differentiator_hypothesis)}<br><b>Дешёвый тест:</b> ${esc(it.cheapest_test)}</div><b>Конкуренты</b>${list(it.incumbents,i=>`<a href="${esc(i.url)}" target="_blank">${esc(i.name)}</a> ${esc(i.note||'')}`)}`;
if(sk)h+=`<h3>Скептик</h3>${list(sk.findings,f=>`<span class="chip ${f.severity==='fatal'?'bad':f.severity==='material'?'warn':''}">${esc(f.severity)}</span> <span class="chip">${esc(f.about)}</span> ${esc(f.text)}`)}<b>Пробелы</b>${list(sk.gaps)}`;
if(jd)h+=`<h3>Судья: ${esc(DEC[jd.decision_context]||jd.decision_context)}</h3><div>${esc(jd.rationale)}</div><pre>${esc(JSON.stringify(jd.scorecard,null,1))}</pre><b>Без ответа</b>${list(jd.unresolved_objections)}<b>Критические неизвестные</b>${list(jd.critical_unknowns)}`;
if(c.review&&c.review.errors&&Object.keys(c.review.errors).length)h+=`<h3 class="bad">Ошибки проверки</h3><pre>${esc(JSON.stringify(c.review.errors,null,1))}</pre>`;
const d=run.decisions[c.id];if(d)h+=`<h3>Решение</h3><div><b>${esc(DEC[d.status]||d.status)}</b> · ${esc(d.author)} · ${esc((d.at||'').slice(0,16))}<br>${esc(d.reason)}</div>`;
$('#detail-body').innerHTML=h;$('#detail').classList.add('open')}
function renderBacklog(){$('#backlog tbody').innerHTML=D.backlog.map(b=>`<tr><td><b>${esc(b.name)}</b><br><small>${esc(b.key)}</small></td><td>${esc(b.theme)}</td><td>${esc(b.status)}</td><td>${esc(b.last_seen_run)} <small>${esc(b.last_seen)}</small></td><td>${fmt(b.last_numbers?.volume_sum)}</td><td>${fmt(b.last_numbers?.cpc_median)}</td><td>${esc(DEC[b.decision?.status]||b.decision?.status||'')}</td></tr>`).join('')||'<tr><td colspan="7" class="muted">Бэклог пуст.</td></tr>'}
init();
</script></body></html>
"""


def build(root):
    data = collect(root)
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.replace("__DATA__", payload)
    target = Path(root) / PAGE
    atomic_text(target, page)
    return {"page": str(target), "runs": len(data["runs"]), "backlog": len(data["backlog"]), "bytes": len(page.encode("utf-8"))}
