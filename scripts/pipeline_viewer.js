(function () {
  "use strict";

  var STAGE_ORDER = ["staging", "discovery", "money", "distribution", "feasibility", "skeptic", "judge", "decision"];
  var REVIEW_ROLES = ["money", "distribution", "feasibility", "skeptic", "judge"];
  var STAGE_META = {
    staging: { name: "Staging", role: "Дешёвые гипотезы", product: "staging-файл без claims", rule: "Гипотеза становится кандидатом только после promote; staging не считается корпусом." },
    discovery: { name: "Discovery", role: "Кто покупатель и какую работу делает", product: "кандидат: покупатель, триггер, обходной путь, первые claims", rule: "Discovery не ставит коммерческих оценок и не валидирует своего кандидата." },
    money: { name: "Money", role: "Платят ли за эту работу", product: "claims и предложенный Payment Confidence", rule: "Цена на странице — оффер, а не платёж; adjacent и exact spending не смешиваются." },
    distribution: { name: "Distribution", role: "Как дотянуться до покупателя", product: "pass / fail / unknown и evidence канала", rule: "Существование канала не доказывает доступ; планируемый тест — не результат." },
    feasibility: { name: "Feasibility", role: "Какой маленький кусок продавать и проверять", product: "pass / fail / unknown и блокеры", rule: "Оценки трудозатрат — planning estimates; MVP-простота не отменяет слабые деньги." },
    skeptic: { name: "Skeptic", role: "Независимая атака на выводы", product: "дефекты claim/source, контрпримеры, severity", rule: "Не автор проверяемых находок; отсутствие данных — не доказательство провала." },
    judge: { name: "Judge", role: "Финальное суждение по замороженному корпусу", product: "assessment.json: уровни, гейты, оценки 0–4", rule: "Новые факты не собирает; противоречия не усредняет." },
    decision: { name: "Решение", role: "Отбор и отчёт", product: "decisions + report.md + русский нарратив", rule: "Ноль рекомендаций — валидный исход; критерии не смягчаются под план." }
  };
  var STAGE_ICONS = {
    staging: '<path d="M12 3 L20.5 7.6 L12 12.2 L3.5 7.6 Z"/><path d="M3.5 12 L12 16.6 L20.5 12"/><path d="M3.5 16.4 L12 21 L20.5 16.4"/>',
    discovery: '<circle cx="11" cy="11" r="6.2"/><path d="M15.6 15.6 L21 21"/><path d="M8.6 11.2 L10.6 13.2 L13.6 9.4"/>',
    money: '<path d="M12 3.4 V20.6"/><path d="M16.4 7.6 C16.4 5.6 14.4 4.6 12 4.6 C9.6 4.6 7.6 5.8 7.6 8 C7.6 10.2 9.6 11 12 11.5 C14.4 12 16.4 12.8 16.4 15 C16.4 17.2 14.4 18.4 12 18.4 C9.6 18.4 7.6 17.4 7.6 15.4"/>',
    distribution: '<circle cx="6" cy="12" r="2.4"/><circle cx="18" cy="6.2" r="2.4"/><circle cx="18" cy="17.8" r="2.4"/><path d="M8.2 11 L15.8 7.1"/><path d="M8.2 13 L15.8 16.9"/>',
    feasibility: '<circle cx="12" cy="12" r="3.1"/><path d="M12 3 V5.4"/><path d="M12 18.6 V21"/><path d="M3 12 H5.4"/><path d="M18.6 12 H21"/><path d="M5.6 5.6 L7.3 7.3"/><path d="M16.7 16.7 L18.4 18.4"/><path d="M18.4 5.6 L16.7 7.3"/><path d="M7.3 16.7 L5.6 18.4"/>',
    skeptic: '<path d="M12 3 L18.8 5.9 V11.8 C18.8 16.2 15.8 19.2 12 20.8 C8.2 19.2 5.2 16.2 5.2 11.8 V5.9 Z"/><path d="M9.2 12.1 L11.3 14.2 L15 10.4"/>',
    judge: '<path d="M12 4 V20"/><path d="M6.4 7 H17.6"/><path d="M6.4 7 L3.9 13 C3.9 14.7 5.1 15.5 6.4 15.5 C7.7 15.5 8.9 14.7 8.9 13 Z"/><path d="M17.6 7 L15.1 13 C15.1 14.7 16.3 15.5 17.6 15.5 C18.9 15.5 20.1 14.7 20.1 13 Z"/><path d="M9.2 20 H14.8"/>',
    decision: '<path d="M6 3.4 V20.6"/><path d="M6 4.8 H18.2 L15.2 9 L18.2 13.2 H6"/>'
  };
  var ROLE_PILL = { money: "Money", distribution: "Distribution", feasibility: "Feasibility", skeptic: "Skeptic", judge: "Judge" };
  var GLYPH = { accepted: "✓", bound: "⏳", prepared: "▢", none: "·", rejected: "✕" };

  var DATA = { runs: [] };
  try {
    DATA = JSON.parse(document.getElementById("runs-data").textContent);
  } catch (e) {
    DATA = { runs: [], error: "не удалось прочитать данные прогонов" };
  }
  var RUNS = DATA.runs || [];
  var state = { runId: null, tab: "overview", t: Infinity, selectedStage: null, playing: false, timer: null,
               sourceFilter: null, sourceSort: "date", sourceDesc: true };

  function $(sel, root) { return (root || document).querySelector(sel); }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function fmtInt(n) { return n === undefined || n === null ? "—" : String(n); }

  function currentRun() {
    for (var i = 0; i < RUNS.length; i++) if (RUNS[i].id === state.runId) return RUNS[i];
    return RUNS[0] || null;
  }

  function statusOf(task, t) {
    if (task.a !== undefined && t >= task.a) return "accepted";
    if (task.b !== undefined && t >= task.b) return "bound";
    if (task.q !== undefined && t >= task.q) return "prepared";
    return "none";
  }

  function stageCounts(run, t) {
    var counts = {};
    (run.stages || []).forEach(function (s) {
      counts[s.key] = {
        key: s.key,
        total: s.total || 0,
        accepted: s.accepted || 0,
        bound: s.bound || 0,
        prepared: s.prepared || 0,
        staging_label: s.staging_label,
        from_tasks: false
      };
    });
    (run.timeline && run.timeline.tasks ? run.timeline.tasks : []).forEach(function (task) {
      var c = counts[task.role];
      if (!c) return;
      if (!c.from_tasks) {
        c.from_tasks = true;
        c.total = 0;
        c.accepted = 0;
        c.bound = 0;
        c.prepared = 0;
      }
      c.total += 1;
      var st = statusOf(task, t);
      if (st === "accepted") c.accepted += 1;
      else if (st === "bound") c.bound += 1;
      else if (st === "prepared") c.prepared += 1;
    });
    return counts;
  }

  function candidateTask(run, candidateId, role) {
    var tasks = (run.timeline && run.timeline.tasks) || [];
    for (var i = 0; i < tasks.length; i++) {
      if (tasks[i].candidate === candidateId && tasks[i].role === role) return tasks[i];
    }
    return null;
  }

  /* ---------------- picker ---------------- */
  function renderPicker(run) {
    var sel = $("#runPicker");
    clear(sel);
    RUNS.forEach(function (r) {
      var opt = document.createElement("option");
      opt.value = r.id;
      var label = r.id + " · " + r.phase + " · " + r.stats.candidates + " канд.";
      if (r.decisions && r.decisions.length) label += " · " + r.decisions.length + " реш.";
      opt.textContent = label;
      if (run && r.id === run.id) opt.selected = true;
      sel.appendChild(opt);
    });
  }

  /* ---------------- hero ---------------- */
  function renderHero(run) {
    $("#runMeta").textContent = run.id + " · снапшот " + DATA.generated_at + " · revision " + fmtInt(run.revision);
    $("#runObjective").textContent = run.objective || "Манифест прогона не найден: доступны только частичные данные (поиски и источники).";
    $("#runLiveText").textContent = run.status === "complete"
      ? "прогон завершён: " + (run.decisions || []).length + " решений, отчёт " + (run.report_path ? "готов" : "не найден")
      : "прогон идёт: фаза «" + run.phase + "», статусы меняются";
    var stats = run.stats;
    var box = $("#stats");
    clear(box);
    [
      { v: fmtInt(stats.candidates), k: "задач покупателей найдено", d: "в " + fmtInt(stats.industries) + " отраслях" },
      { v: fmtInt(stats.claims), k: "подтверждённых наблюдений", d: "каждое со ссылкой на сохранённый источник" },
      { v: fmtInt(stats.sources), k: "источников скачано", d: fmtInt(stats.sources_cited) + " попали в выводы · " + fmtInt(stats.sources_orphan) + " никем не использованы" },
      { v: stats.assignments_accepted + " / " + stats.assignments_used, k: "заданий агентам принято", d: "работало агентов: " + fmtInt(stats.workers) },
      { v: fmtInt(stats.decisions), k: "решений по кандидатам", d: "остальные ждут проверок" }
    ].forEach(function (s) {
      var card = el("div", "stat");
      card.appendChild(el("div", "v", s.v));
      card.appendChild(el("div", "k", s.k));
      card.appendChild(el("div", "d", s.d));
      box.appendChild(card);
    });
  }

  /* ---------------- stages ---------------- */
  function renderFlow(run) {
    var track = $("#flowTrack");
    clear(track);
    $("#stageNote").hidden = true;
    var counts = stageCounts(run, state.t);
    STAGE_ORDER.forEach(function (key) {
      var meta = STAGE_META[key];
      var info = counts[key];
      if (!info) return;
      var st = stageState(info);
      var node = el("div", "node " + st);
      node.setAttribute("data-stage", key);
      var btn = el("button");
      btn.type = "button";
      var ring = el("span", "ring");
      ring.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + STAGE_ICONS[key] + "</svg>";
      btn.appendChild(ring);
      btn.appendChild(el("span", "nm", meta.name));
      btn.appendChild(el("span", "ct", stageLabel(key, info, run)));
      btn.addEventListener("click", function () { toggleStage(key, node); });
      node.appendChild(btn);
      track.appendChild(node);
    });
  }

  function stageState(info) {
    if (info.key === "staging") return info.accepted > 0 ? "done" : "waiting";
    if (info.total === 0) return "waiting";
    if (info.accepted === info.total) return "done";
    if (info.accepted > 0 || info.bound > 0 || info.prepared > 0) return "active";
    return "waiting";
  }

  function stageLabel(key, info, run) {
    if (key === "staging") return info.staging_label || (info.total ? info.accepted + " / " + info.total : "нет данных");
    if (key === "decision") return (run.decisions || []).length + " решений";
    var parts = [info.accepted + " / " + info.total];
    if (info.bound) parts.push(info.bound + " в работе");
    if (info.prepared) parts.push(info.prepared + " подготовлено");
    return parts.join(" · ");
  }

  function toggleStage(key, node) {
    var note = $("#stageNote");
    var meta = STAGE_META[key];
    var open = state.selectedStage === key;
    state.selectedStage = open ? null : key;
    Array.prototype.forEach.call(document.querySelectorAll(".node"), function (n) { n.classList.remove("selected"); });
    if (open) { note.hidden = true; return; }
    node.classList.add("selected");
    $("#stageNoteTitle").textContent = meta.name + " — " + meta.role;
    $("#stageNoteBody").textContent = "Продукт: " + meta.product + " Правило: " + meta.rule;
    note.hidden = false;
    highlightStage(key);
  }

  function highlightStage(key) {
    var run = currentRun();
    Array.prototype.forEach.call(document.querySelectorAll(".cand"), function (card) {
      var id = card.getAttribute("data-cand");
      if (key === "staging" || key === "decision") { card.classList.remove("dimmed", "hit"); return; }
      var task = candidateTask(run, id, key);
      if (!task) { card.classList.add("dimmed"); card.classList.remove("hit"); return; }
      var st = statusOf(task, state.t);
      card.classList.toggle("hit", st === "accepted" || st === "bound");
      card.classList.toggle("dimmed", st === "none" || st === "prepared");
    });
  }

  /* ---------------- timeline ---------------- */
  function renderTimeline(run) {
    var tl = run.timeline || { steps: [], tasks: [] };
    var range = $("#timeRange");
    range.min = "0";
    range.max = String(Math.max(0, tl.steps.length - 1));
    range.value = range.max;
    state.t = tl.steps.length ? tl.steps[tl.steps.length - 1].m : Infinity;
    applyTime(parseInt(range.value, 10));
  }

  function applyTime(index) {
    var run = currentRun();
    var tl = run.timeline || { steps: [], tasks: [] };
    var step = tl.steps[index];
    var t = step ? step.m : Infinity;
    state.t = t;
    $("#clock").textContent = step ? step.label : "сейчас";
    $("#clockNote").textContent = step ? step.note : "";
    var counts = stageCounts(run, t);
    Array.prototype.forEach.call(document.querySelectorAll(".node"), function (n) {
      var key = n.getAttribute("data-stage");
      var info = counts[key];
      if (!info) return;
      n.className = "node " + stageState(info);
      var ct = n.querySelector(".ct");
      if (ct) ct.textContent = stageLabel(key, info, run);
    });
    updatePips(run, t);
    updateChips(run, t);
  }

  /* ---------------- workers ---------------- */
  function renderWorkers(run) {
    var box = $("#workers");
    clear(box);
    (run.workers || []).forEach(function (w, idx) {
      var card = el("article", "worker" + (idx === 0 ? " root" : ""));
      var head = el("div", "worker-head");
      var av = el("div", "avatar", w.badge);
      var who = el("div", "who");
      who.appendChild(el("div", "nm", w.label));
      who.appendChild(el("div", "sub", w.sub));
      var free = el("span", "free" + (w.busy ? " busy" : ""), w.busy ? "занят" : "свободен");
      head.appendChild(av);
      head.appendChild(who);
      head.appendChild(free);
      card.appendChild(head);
      var list = el("div", "steps-list");
      (w.rows || []).forEach(function (row) {
        var step = el("div", "step");
        step.appendChild(el("span", "role", row.role));
        var chips = el("span", "chips");
        row.items.forEach(function (item) {
          var chip = el("button", "chip", item.text);
          chip.type = "button";
          chip.setAttribute("data-task", item.task);
          var dot = el("span", "d");
          chip.insertBefore(dot, chip.firstChild);
          chip.addEventListener("click", function () { focusCandidate(item.candidate); });
          chips.appendChild(chip);
        });
        step.appendChild(chips);
        list.appendChild(step);
      });
      card.appendChild(list);
      box.appendChild(card);
    });
    if (!(run.workers || []).length) {
      box.appendChild(el("div", "cand", "В этом прогоне нет привязанных агентов: манифест отсутствует."));
    }
  }

  function updateChips(run, t) {
    Array.prototype.forEach.call(document.querySelectorAll(".chip[data-task]"), function (chip) {
      var id = chip.getAttribute("data-task");
      var task = null;
      ((run.timeline && run.timeline.tasks) || []).forEach(function (x) { if (x.id === id) task = x; });
      if (!task) return;
      var st = statusOf(task, t);
      chip.className = "chip " + (st === "accepted" ? "ok" : st === "bound" ? "run" : st === "prepared" ? "prep" : "queued");
    });
  }

  /* ---------------- candidates ---------------- */
  function renderCandidates(run) {
    var box = $("#cands");
    clear(box);
    (run.candidates || []).forEach(function (c) {
      var card = el("article", "cand");
      card.setAttribute("data-cand", c.id);
      var head = el("div", "cand-head");
      head.appendChild(el("h3", null, c.title));
      head.appendChild(el("span", "tag", c.industry));
      card.appendChild(head);
      if (c.subtitle) card.appendChild(el("p", "small", c.subtitle));
      if (c.who) card.appendChild(el("p", "small muted", c.who));
      var pips = el("div", "pips");
      REVIEW_ROLES.forEach(function (role) {
        var pip = el("span", "pip");
        pip.setAttribute("data-pip", role);
        pip.setAttribute("data-cand", c.id);
        var d = el("span", "d");
        pip.appendChild(d);
        pip.appendChild(document.createTextNode(ROLE_PILL[role]));
        pips.appendChild(pip);
      });
      if (c.decision) {
        var dec = el("span", "pip " + (c.decision.status === "qualified" ? "ok" : c.decision.status === "rejected" ? "run" : "prep"), c.decision.status);
        pips.appendChild(dec);
      }
      card.appendChild(pips);
      var foot = el("div", "cand-foot");
      foot.appendChild(el("span", "tier", c.tier && c.tier !== "—" ? "Payment " + c.tier : "нет оценки"));
      foot.appendChild(el("span", null, c.claims + " claims"));
      card.appendChild(foot);
      card.appendChild(el("p", "next", c.next));
      var more = el("button", "more", "Подробнее →");
      more.type = "button";
      more.setAttribute("aria-expanded", "false");
      card.appendChild(more);
      var detail = el("div", "detail");
      detail.hidden = true;
      var dl = el("dl");
      [["Покупатель", c.who], ["Задача", c.job], ["Триггер", c.trigger], ["Pain", c.pain]].forEach(function (pair) {
        if (!pair[1]) return;
        dl.appendChild(el("dt", null, pair[0]));
        dl.appendChild(el("dd", null, pair[1]));
      });
      REVIEW_ROLES.forEach(function (role) {
        var r = (c.roles || {})[role];
        if (!r) return;
        var label = ROLE_PILL[role] + (r.verdict ? " · " + r.verdict : "") + " · " + r.status;
        dl.appendChild(el("dt", null, ROLE_PILL[role]));
        var dd = el("dd");
        dd.appendChild(document.createTextNode(label + (r.agent ? " (" + r.agent + ")" : "")));
        if (r.excerpt) {
          dd.appendChild(document.createElement("br"));
          dd.appendChild(el("span", "muted", r.excerpt));
        }
        dl.appendChild(dd);
      });
      if (c.decision) {
        dl.appendChild(el("dt", null, "Решение"));
        dl.appendChild(el("dd", null, c.decision.status + (c.decision.reason ? " — " + c.decision.reason : "")));
      }
      detail.appendChild(dl);
      card.appendChild(detail);
      more.addEventListener("click", function () {
        var open = !detail.hidden;
        detail.hidden = open;
        more.setAttribute("aria-expanded", open ? "false" : "true");
        more.textContent = open ? "Подробнее →" : "Свернуть ↑";
      });
      box.appendChild(card);
    });
    if (!(run.candidates || []).length) {
      box.appendChild(el("div", "cand", "Кандидаты не найдены: в каталоге прогона нет artifacts кандидатов."));
    }
  }

  function updatePips(run, t) {
    Array.prototype.forEach.call(document.querySelectorAll(".pip[data-pip]"), function (pip) {
      var role = pip.getAttribute("data-pip");
      var cand = pip.getAttribute("data-cand");
      var task = candidateTask(run, cand, role);
      if (!task) { pip.className = "pip"; return; }
      var st = statusOf(task, t);
      pip.className = "pip" + (st === "accepted" ? " ok" : st === "bound" ? " run" : st === "prepared" ? " prep" : "");
    });
  }

  function focusCandidate(id) {
    var card = document.querySelector('.cand[data-cand="' + id + '"]');
    if (!card) return;
    var more = card.querySelector(".more");
    var detail = card.querySelector(".detail");
    if (more && detail.hidden) more.click();
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    card.classList.add("hit");
    setTimeout(function () { card.classList.remove("hit"); }, 2200);
  }

  /* ---------------- tabs ---------------- */
  var TABS = [
    { key: "overview", label: "Обзор" },
    { key: "sources", label: "Источники" },
    { key: "agents", label: "Агенты и модели" },
    { key: "candidates", label: "Кандидаты" },
    { key: "tech", label: "Технически" }
  ];

  function renderTabs() {
    var nav = $("#tabs");
    clear(nav);
    TABS.forEach(function (tab) {
      var btn = el("button", null, tab.label);
      btn.type = "button";
      btn.id = "tabbtn-" + tab.key;
      btn.setAttribute("role", "tab");
      btn.setAttribute("aria-controls", "tab-" + tab.key);
      btn.addEventListener("click", function () { selectTab(tab.key); });
      nav.appendChild(btn);
    });
    applyTab();
  }

  function applyTab() {
    TABS.forEach(function (tab) {
      var panel = $("#tab-" + tab.key);
      var btn = $("#tabbtn-" + tab.key);
      var active = tab.key === state.tab;
      if (panel) panel.hidden = !active;
      if (btn) btn.setAttribute("aria-selected", active ? "true" : "false");
    });
  }

  function selectTab(key) {
    state.tab = key;
    applyTab();
    writeHash();
  }

  function writeHash() {
    var next = "run=" + state.runId + "&tab=" + state.tab;
    if (location.hash !== "#" + next) location.hash = next;
  }

  /* ---------------- overview ---------------- */
  var PHASE_RU = {
    discovery: "ищем рабочие задачи покупателей",
    evidence: "собираем доказательства по найденным задачам",
    review: "независимая проверка и суждение",
    decision: "решаем по каждому кандидату",
    complete: "прогон закрыт"
  };

  function box(title, nodes) {
    var b = el("div", "box");
    b.appendChild(el("h3", null, title));
    nodes.forEach(function (n) { b.appendChild(typeof n === "string" ? html(el("p"), n) : n); });
    return b;
  }

  function html(node, markup) { node.innerHTML = markup; return node; }

  function facts(items) {
    var list = el("ul", "facts");
    items.forEach(function (item) {
      if (!item) return;
      var li = el("li");
      li.appendChild(el("b", null, item[0]));
      li.appendChild(html(el("span"), item[1]));
      list.appendChild(li);
    });
    return list;
  }

  function renderOverview(run) {
    var wrap = $("#overview");
    clear(wrap);
    var s = run.stats;
    var src = run.sources || { totals: {}, diagnostics: [] };
    var totals = src.totals || {};

    wrap.appendChild(box("Зачем этот проект", [
      "Мы ищем <strong>узкую повторяющуюся работу</strong>, которую кто-то уже делает руками и за которую уже уходят деньги или часы сотрудников.",
      "Сначала дёшево набрасываем много гипотез, потом каждую проверяем всё дороже: есть ли покупатель, платят ли за эту работу, можно ли до него дотянуться и можно ли сделать маленький продукт.",
      "Главное правило: <strong>цена на сайте конкурента — не доказательство оплаты</strong>. Доказательством считается независимый рассказ покупателя, наблюдаемые расходы на людей или подряд, платные альтернативы и повторяемость."
    ]));

    wrap.appendChild(box("Как читать эту страницу", [
      "<strong>Источники</strong> — всё, что мы скачали наружу: кто из агентов это сделал и попало ли оно в выводы.",
      "<strong>Агенты и модели</strong> — какая модель назначена каждой роли и что эта роль произвела.",
      "<strong>Кандидаты</strong> — найденные задачи покупателей и статус их проверки.",
      "<strong>Технически</strong> — стадии, хронология и лимиты прогона."
    ]));

    var decisions = run.decisions || [];
    var qualified = decisions.filter(function (d) { return d && d.status === "qualified"; }).length;
    wrap.appendChild(box("Где мы сейчас", [
      facts([
        [run.status === "complete" ? "закрыт" : "идёт", "прогон: " + (PHASE_RU[run.phase] || run.phase)],
        [fmtInt(s.candidates), "кандидатов найдено, в " + fmtInt(s.industries) + " отраслях покупателей"],
        [fmtInt(s.claims), "утверждений с привязкой к сохранённым источникам"],
        [fmtInt(decisions.length), "решений принято" + (qualified ? ", из них рекомендовано: " + qualified : ", рекомендованных нет")],
        [run.updated_at || "—", "последнее событие прогона"]
      ])
    ]));

    var found = (run.candidates || []).slice().sort(function (a, b) { return (b.claims || 0) - (a.claims || 0); }).slice(0, 3);
    var foundNodes = found.length
      ? [facts(found.map(function (c) { return [fmtInt(c.claims), "<strong>" + c.title + "</strong> — " + (c.subtitle || "") ]; })),
         el("p", "small muted", "Числа слева — сколько подтверждённых наблюдений собрано по кандидату.")]
      : ["Кандидатов пока нет: прогон либо только начался, либо остановился на стадии гипотез."];
    wrap.appendChild(box("Что нашли", foundNodes));

    var blockers = [];
    (src.diagnostics || []).forEach(function (d) { blockers.push([fmtInt(d.count), d.text]); });
    (run.notes || []).forEach(function (n) { blockers.push(["!", n]); });
    wrap.appendChild(box("Что мешает доверять результату", blockers.length
      ? [facts(blockers), el("p", "small muted", "Это не скрытые ошибки, а записанные факты прогона: их видно и можно исправить следующим заданием.")]
      : ["Явных проблем в этом прогоне не записано."]));

    var gloss = $("#glossary");
    clear(gloss);
    var roles = el("div", "glossary");
    (run.agents || []).forEach(function (a) {
      if (!a.purpose) return;
      var row = el("div", "row");
      row.appendChild(el("b", null, a.label));
      row.appendChild(el("span", null, a.purpose));
      roles.appendChild(row);
    });
    gloss.appendChild(box("Роли агентов", [roles]));

    var kinds = el("div", "glossary");
    (src.by_kind || []).forEach(function (k) {
      var row = el("div", "row");
      row.appendChild(el("b", null, k.label));
      row.appendChild(el("span", null, k.purpose || ""));
      kinds.appendChild(row);
    });
    gloss.appendChild(box("Типы источников в этом прогоне", kinds.children.length
      ? [kinds, el("p", "small muted", "«Скачано» не значит «доказано»: пока утверждение не сослалось на источник, он ничего не подтверждает.")]
      : ["В этом прогоне источники не сохранялись."]));
  }

  /* ---------------- sources ---------------- */
  var SORTS = [
    { key: "date", label: "Когда" },
    { key: "kind_label", label: "Тип" },
    { key: "role_label", label: "Агент / роль" },
    { key: "candidate", label: "Кандидат" },
    { key: "url", label: "Что скачали" },
    { key: "purpose", label: "Зачем" },
    { key: "claims", label: "Ссылок" },
    { key: "status", label: "Статус" }
  ];
  var ROW_LIMIT = 250;

  function sourceFilterState() {
    return state.sourceFilter || (state.sourceFilter = { kind: "", role: "", candidate: "", agent: "", orphan: false, text: "" });
  }

  function renderSources(run) {
    var src = run.sources || { rows: [], by_kind: [], by_agent: [], diagnostics: [], totals: {} };
    var totals = src.totals || {};
    var chips = $("#sourceChips");
    clear(chips);
    [["всего источников", totals.sources], ["процитировано в утверждениях", totals.cited],
     ["никем не использовано", totals.orphan], ["ошибок обращения", totals.failed]
    ].forEach(function (pair) {
      var c = el("span", "c");
      c.appendChild(el("b", null, fmtInt(pair[1])));
      c.appendChild(document.createTextNode(" " + pair[0]));
      chips.appendChild(c);
    });
    (src.by_kind || []).forEach(function (k) {
      var c = el("span", "c");
      c.appendChild(el("b", null, fmtInt(k.total)));
      c.appendChild(document.createTextNode(" " + k.label.toLowerCase()));
      chips.appendChild(c);
    });

    var diag = $("#sourceDiag");
    clear(diag);
    (src.diagnostics || []).forEach(function (d) {
      var node = el("div", "d " + (d.level || "info"));
      node.appendChild(el("b", null, fmtInt(d.count)));
      node.appendChild(el("span", null, d.text));
      diag.appendChild(node);
    });

    renderSourceFilters(run, src);
    renderSourceTable(run, src);
  }

  function selectField(label, options, value, onChange) {
    var wrap = el("label", null, label);
    var sel = document.createElement("select");
    options.forEach(function (opt) {
      var o = document.createElement("option");
      o.value = opt[0];
      o.textContent = opt[1];
      if (opt[0] === value) o.selected = true;
      sel.appendChild(o);
    });
    sel.addEventListener("change", function (e) { onChange(e.target.value); });
    wrap.appendChild(sel);
    return wrap;
  }

  function renderSourceFilters(run, src) {
    var f = sourceFilterState();
    var box = $("#sourceFilters");
    clear(box);
    var kinds = [["", "все типы"]].concat((src.by_kind || []).map(function (k) { return [k.key, k.label + " (" + k.total + ")"]; }));
    var roles = [["", "все роли"]].concat((src.by_role || []).map(function (r) { return [r.role, r.label + " (" + r.total + ")"]; }));
    var agents = [["", "все агенты"]].concat((src.by_agent || []).map(function (a) { return [a.agent, a.agent + " (" + a.total + ")"]; }));
    var cands = [["", "все кандидаты"]].concat(Object.keys(src.by_candidate || {}).sort().map(function (c) {
      return [c, c + " (" + src.by_candidate[c] + ")"];
    }));
    box.appendChild(selectField("Тип источника", kinds, f.kind, function (v) { f.kind = v; renderSourceTable(run, src); }));
    box.appendChild(selectField("Роль агента", roles, f.role, function (v) { f.role = v; renderSourceTable(run, src); }));
    box.appendChild(selectField("Агент", agents, f.agent, function (v) { f.agent = v; renderSourceTable(run, src); }));
    box.appendChild(selectField("Кандидат", cands, f.candidate, function (v) { f.candidate = v; renderSourceTable(run, src); }));

    var search = el("label", null, "Поиск по тексту");
    var input = document.createElement("input");
    input.type = "search";
    input.value = f.text;
    input.placeholder = "домен, запрос, цель";
    input.addEventListener("input", function (e) { f.text = e.target.value; renderSourceTable(run, src); });
    search.appendChild(input);
    box.appendChild(search);

    var toggle = el("span", "toggle");
    var check = document.createElement("input");
    check.type = "checkbox";
    check.id = "orphanOnly";
    check.checked = !!f.orphan;
    check.addEventListener("change", function (e) { f.orphan = e.target.checked; renderSourceTable(run, src); });
    var lbl = el("label", null, "только никем не использованные");
    lbl.setAttribute("for", "orphanOnly");
    lbl.style.display = "inline";
    toggle.appendChild(check);
    toggle.appendChild(lbl);
    box.appendChild(toggle);
  }

  function filteredSources(src) {
    var f = sourceFilterState();
    var text = (f.text || "").toLowerCase();
    return (src.rows || []).filter(function (r) {
      if (f.kind && r.kind !== f.kind) return false;
      if (f.role && r.role !== f.role) return false;
      if (f.agent && (r.worker || "не атрибутировано") !== f.agent) return false;
      if (f.candidate && r.candidate !== f.candidate) return false;
      if (f.orphan && !r.orphan) return false;
      if (text) {
        var hay = [r.url, r.query, r.purpose, r.assignment, r.provider].join(" ").toLowerCase();
        if (hay.indexOf(text) === -1) return false;
      }
      return true;
    });
  }

  function renderSourceTable(run, src) {
    var table = $("#sourceTable");
    var head = table.querySelector("thead");
    var body = table.querySelector("tbody");
    clear(head);
    clear(body);
    var hrow = el("tr");
    SORTS.forEach(function (col) {
      var th = el("th", null, col.label + (state.sourceSort === col.key ? (state.sourceDesc ? " ↓" : " ↑") : ""));
      th.tabIndex = 0;
      th.addEventListener("click", function () {
        state.sourceDesc = state.sourceSort === col.key ? !state.sourceDesc : false;
        state.sourceSort = col.key;
        renderSourceTable(run, src);
      });
      hrow.appendChild(th);
    });
    head.appendChild(hrow);

    var rows = filteredSources(src);
    var key = state.sourceSort || "date";
    rows.sort(function (a, b) {
      var x = a[key], y = b[key];
      if (typeof x === "number" || typeof y === "number") return (state.sourceDesc ? -1 : 1) * ((x || 0) - (y || 0));
      x = String(x || ""); y = String(y || "");
      return (state.sourceDesc ? -1 : 1) * (x < y ? -1 : x > y ? 1 : 0);
    });

    rows.slice(0, ROW_LIMIT).forEach(function (r) {
      var tr = el("tr");
      tr.appendChild(el("td", null, (r.date || "") + " " + (r.fetched_at || "")));
      tr.appendChild(el("td", null, r.kind_label));
      var who = el("td");
      who.appendChild(el("div", null, r.role_label || "—"));
      who.appendChild(el("div", "small muted", r.worker || "не записан"));
      tr.appendChild(who);
      tr.appendChild(el("td", null, r.candidate || "—"));
      tr.appendChild(el("td", "u", r.url || r.query || "—"));
      tr.appendChild(el("td", "u", r.purpose || "—"));
      tr.appendChild(el("td", "n", String(r.claims)));
      var st = el("td");
      if (r.status === "collected") st.appendChild(el("span", "badge ok", "сохранён"));
      else st.appendChild(el("span", "badge err", "ошибка"));
      if (r.orphan) st.appendChild(el("span", "badge orphan", "не использован"));
      tr.appendChild(st);
      tr.addEventListener("click", function () { toggleSourceDetail(tr, r); });
      body.appendChild(tr);
    });

    var more = $("#sourceMore");
    more.textContent = rows.length > ROW_LIMIT
      ? "Показаны первые " + ROW_LIMIT + " из " + rows.length + " строк — сузьте фильтр."
      : "Строк: " + rows.length + (rows.length === (src.rows || []).length ? "" : " из " + (src.rows || []).length);
  }

  function toggleSourceDetail(tr, row) {
    if (tr.nextSibling && tr.nextSibling.classList && tr.nextSibling.classList.contains("detail")) {
      tr.parentNode.removeChild(tr.nextSibling);
      tr.classList.remove("open");
      return;
    }
    var detail = el("tr", "detail");
    var td = el("td");
    td.colSpan = SORTS.length;
    td.appendChild(html(el("div"), "<code>" + row.id + "</code> · провайдер <code>" + row.provider + "</code>"
      + (row.assignment ? " · задание <code>" + row.assignment + "</code>" : "")
      + (row.stage ? " · стадия <code>" + row.stage + "</code>" : "")
      + " · атрибуция: " + row.attribution));
    if (row.url) td.appendChild(html(el("div", "small muted"), row.url));
    if (row.error) td.appendChild(el("div", "small", "Ошибка: " + row.error));
    (row.claim_list || []).forEach(function (c) {
      td.appendChild(el("div", "claim", (c.id || "") + ": " + c.text));
    });
    if (!row.claims) td.appendChild(el("div", "claim", "Ни одно утверждение на этот источник не ссылается."));
    detail.appendChild(td);
    tr.classList.add("open");
    tr.parentNode.insertBefore(detail, tr.nextSibling);
  }

  /* ---------------- agents and models ---------------- */
  function modelText(choice) {
    if (!choice) return "не задано";
    var provider = choice.provider || "любой";
    var model = choice.model || "любая";
    var effort = choice.reasoning_effort ? ", усилие: " + choice.reasoning_effort : "";
    return provider + " / " + model + effort;
  }

  function renderAgents(run) {
    var routing = DATA.routing || { roles: {}, providers: [] };
    var top = $("#routingBox");
    clear(top);

    var current = el("div", "glossary");
    Object.keys(routing.roles || {}).forEach(function (role) {
      var choice = routing.roles[role];
      var row = el("div", "row");
      row.appendChild(el("b", null, choice.label || role));
      row.appendChild(el("span", null, modelText(choice) + " (" + choice.routing_source + ")"));
      current.appendChild(row);
    });
    top.appendChild(box("Текущая маршрутизация (config/model-routing.yaml)", [
      current,
      el("p", "small muted", "Это то, что запрашивается у новых заданий. Прогон ниже показывает, что было записано фактически.")
    ]));
    top.appendChild(box("Сменить модель роли", [buildRoutingForm(routing)]));

    var box2 = $("#agents");
    clear(box2);
    (run.agents || []).forEach(function (a) {
      var card = el("article", "agent" + (a.assignments ? "" : " idle"));
      card.appendChild(el("h3", null, a.label));
      card.appendChild(el("div", "rl", a.role + (a.model_class ? " · класс модели: " + a.model_class : "")));
      if (a.purpose) card.appendChild(el("p", "why", a.purpose));
      var dl = el("dl", "kv");
      [["Запрошено", modelText(a.requested)],
       ["Фактически", (a.actual && a.actual.length) ? a.actual.join(", ") : "не записано"],
       ["Заданий", a.assignments + " (принято " + a.accepted + ")"],
       ["Агенты", (a.workers || []).join(", ") || "—"],
       ["Источников", String(a.sources)],
       ["Утверждений", String(a.claims)],
       ["Токенов", a.tokens ? (fmtInt(a.tokens.total) + " за " + fmtInt(a.model_calls) + " вызовов") : "не записано"]
      ].forEach(function (pair) {
        dl.appendChild(el("dt", null, pair[0]));
        dl.appendChild(el("dd", pair[1] === "не записано" || pair[1] === "не задано" ? "muted" : null, pair[1]));
      });
      card.appendChild(dl);
      box2.appendChild(card);
    });
    if (!(run.agents || []).length) {
      box2.appendChild(el("div", "cand", "В этом прогоне нет манифеста: роли и модели не записаны."));
    }
  }

  function buildRoutingForm(routing) {
    var wrap = el("div");
    var form = el("div", "form");
    var roles = Object.keys(routing.roles || {});
    var providers = (routing.providers || []);
    var pick = { role: roles[0] || "skeptic", provider: "", model: "", effort: "" };

    form.appendChild(selectField("Роль", roles.map(function (r) {
      return [r, (routing.roles[r].label || r) + " (" + r + ")"];
    }), pick.role, function (v) { pick.role = v; update(); }));
    form.appendChild(selectField("Провайдер", [["", "любой (сброс)"]].concat(providers.map(function (p) {
      return [p.id, p.label || p.id];
    })), pick.provider, function (v) { pick.provider = v; update(); }));

    var modelLabel = el("label", null, "Модель");
    var input = document.createElement("input");
    input.type = "text";
    input.setAttribute("list", "modelList");
    input.placeholder = "например deepseek-reasoner";
    input.addEventListener("input", function (e) { pick.model = e.target.value.trim(); update(); });
    modelLabel.appendChild(input);
    var datalist = document.createElement("datalist");
    datalist.id = "modelList";
    providers.forEach(function (p) {
      (p.models || []).forEach(function (m) {
        var o = document.createElement("option");
        o.value = m;
        datalist.appendChild(o);
      });
    });
    modelLabel.appendChild(datalist);
    form.appendChild(modelLabel);

    form.appendChild(selectField("Усилие рассуждения", [["", "не менять"], ["low", "low"], ["medium", "medium"], ["high", "high"]],
      pick.effort, function (v) { pick.effort = v; update(); }));

    var copy = el("button", "copy", "Скопировать команду");
    copy.type = "button";
    form.appendChild(copy);
    wrap.appendChild(form);

    var command = el("pre", "snippet");
    var yaml = el("pre", "snippet");
    wrap.appendChild(command);
    wrap.appendChild(yaml);
    wrap.appendChild(el("p", "small muted", "Страница ничего не записывает сама: выполните команду в репозитории, затем пересоберите страницу — python3 scripts/pipeline_viewer.py"));

    function commandText() {
      var parts = ["python3 scripts/routing.py set --role " + pick.role];
      parts.push("--provider " + (pick.provider || "any"));
      if (pick.model) parts.push("--model " + pick.model);
      if (pick.effort) parts.push("--reasoning-effort " + pick.effort);
      return parts.join(" ");
    }

    function update() {
      command.textContent = commandText();
      yaml.textContent = "# config/model-routing.yaml\nroles:\n  " + pick.role + ": {provider: "
        + (pick.provider || "null") + ", model: " + (pick.model || "null")
        + ", reasoning_effort: " + (pick.effort || (routing.roles[pick.role] || {}).reasoning_effort || "null") + "}";
    }

    copy.addEventListener("click", function () {
      var text = commandText();
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () { copy.textContent = "Скопировано"; setTimeout(function () { copy.textContent = "Скопировать команду"; }, 1600); },
          function () { selectText(command); });
      } else selectText(command);
    });
    update();
    return wrap;
  }

  function selectText(node) {
    var range = document.createRange();
    range.selectNodeContents(node);
    var sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }

  /* ---------------- params ---------------- */
  function kv(pairs) {
    var dl = el("dl", "kv");
    pairs.forEach(function (pair) {
      if (pair[1] === undefined || pair[1] === null || pair[1] === "") return;
      dl.appendChild(el("div", "k", pair[0]));
      var v = el("div");
      v.innerHTML = pair[1];
      dl.appendChild(v);
    });
    return dl;
  }

  function ul(items) {
    var list = el("ul");
    items.forEach(function (text) {
      if (!text) return;
      var li = el("li");
      li.innerHTML = text;
      list.appendChild(li);
    });
    return list;
  }

  function card(title, body, cls) {
    var c = el("div", "param" + (cls ? " " + cls : ""));
    c.appendChild(el("h3", null, title));
    if (typeof body === "string") c.appendChild(el("p", "small muted", body));
    else c.appendChild(body);
    return c;
  }

  function renderParams(run) {
    var box = $("#params");
    clear(box);
    var s = run.stats;
    var limits = run.limits || {};
    var caps = (run.capabilities && run.capabilities.tools) || {};
    var params = [];

    var passport = el("div");
    passport.appendChild(kv([
      ["run_id", "<code>" + run.id + "</code>"],
      ["Цель", run.objective],
      ["Охват", run.scope ? "<code>scope: " + run.scope + "</code>" : ""],
      ["Стоп-условие", run.stopping_conditions],
      ["Разрешение", run.brief ? "<code>" + run.brief + "</code>" : ""],
      ["Конфиг", run.config_hash ? "<code>config.json</code>, hash <code>" + run.config_hash + "</code>" : ""],
      ["Фаза", "<code>" + run.phase + "</code> · статус <code>" + run.status + "</code> · revision " + fmtInt(run.revision)],
      ["Отчёт", run.report_path ? "<code>" + run.report_path + "</code>" : "нет"]
    ]));
    params.push(card("Паспорт прогона", passport, "wide"));

    if (Object.keys(limits).length) {
      params.push(card("Лимиты", ul([
        "<b>" + fmtInt(limits.max_active_agents) + "</b> активных агента — отсюда волны",
        "<b>" + fmtInt(limits.max_candidates) + "</b> кандидатов максимум (факт: " + s.candidates + ")",
        "<b>" + fmtInt(limits.max_assignments) + "</b> заданий (факт: " + s.assignments_used + ")",
        "<b>" + fmtInt(limits.max_attempts_per_assignment) + "</b> попытки на задание",
        "<b>" + fmtInt(limits.max_search_queries_per_attempt) + "</b> поисковых запроса на попытку"
      ])));
    }

    var budget = el("div");
    [["Задания", s.assignments_accepted + " принято из " + s.assignments_used + " использованных"],
     ["Claims", String(s.claims)],
     ["Поиски", s.searches_ok + " успешных из " + s.searches],
     ["Источники", String(s.sources)],
     ["Кандидаты", s.candidates + " в " + s.industries + " индустриях"],
     ["Решения", String((run.decisions || []).length)]
    ].forEach(function (pair) {
      var row = el("div", "barrow");
      var lbl = el("div", "lbl");
      lbl.appendChild(el("span", null, pair[0]));
      lbl.appendChild(el("span", null, pair[1]));
      row.appendChild(lbl);
      budget.appendChild(row);
    });
    params.push(card("Расход бюджета", budget));

    if (Object.keys(caps).length) {
      params.push(card("Снапшот возможностей", ul([
        caps.collaboration ? "Коллаборация: " + caps.collaboration.join(", ") : "",
        caps.network ? "Сеть: <code>" + caps.network + "</code>" : "",
        caps.web_search_provider === false ? "Веб-провайдер в root-сессии: нет" : "",
        caps.local_exec ? "Локальное исполнение: есть" : "",
        run.capabilities && run.capabilities.recorded_at ? "Записан: " + run.capabilities.recorded_at : ""
      ])));
    }

    if (run.portfolio) {
      var quota = run.portfolio.portfolio_rules || {};
      var enforce = run.portfolio.enforcement || {};
      params.push(card("Квоты портфеля", ul([
        "≥ " + fmtInt(enforce.minimum_industries) + " индустрий покупателей (факт: " + s.industries + ")",
        "≥ 40% «скучных» отраслей (настроено: " + fmtInt(quota.min_boring_industries_pct) + "%)",
        "≥ 30% нетехнических покупателей (настроено: " + fmtInt(quota.min_non_technical_buyers_pct) + "%)",
        "≤ 20% AI-агенты, ≤ 20% devops/cloud, ≤ 10% ecommerce, ≤ 10% MCP",
        "Незнакомое остаётся <code>null</code>, квота сообщается как «не оценена»"
      ])));
    }

    params.push(card("Гейты и независимость", ul([
      "Нужны принятые discovery, money, distribution, feasibility, skeptic, judge",
      "Любой unknown в обязательном гейте → deferred, а не «никто не платит»",
      "Discovery-автор не валидирует и не судит своего кандидата",
      "Skeptic не автор проверяемых находок, judge — ни одного разбора",
      "Независимость ролевая; <code>reused_context</code> фиксируется и не превращает запуски в статистически независимые"
    ])));

    if ((run.notes || []).length) {
      params.push(card("Что важно знать про этот прогон", ul(run.notes.map(function (n) { return n; })), "warn"));
    }

    params.push(card("Где смотреть", kv([
      ["Каталог", "<code>" + run.path + "</code>"],
      ["Манифест", run.manifest_path ? "<code>" + run.manifest_path + "</code>" : "нет"],
      ["Журналы", "<code>events.jsonl</code> · <code>claims.jsonl</code> · <code>searches.jsonl</code>"],
      ["Отчёт", run.report_path ? "<code>" + run.report_path + "</code>" : "не создан"]
    ])));

    params.forEach(function (p) { box.appendChild(p); });
  }

  /* ---------------- playground ---------------- */
  function renderFooter() {
    var f = $("#footer");
    clear(f);
    f.appendChild(el("div", "small", "Страница собрана скриптом scripts/pipeline_viewer.py по каталогам runs/<run-id>/: manifest.json, config.json, events.jsonl, claims.jsonl, searches.jsonl, source_usage.jsonl, sources/*.meta.json и work/*/attempt-N/result.json."));
    f.appendChild(el("div", "small", "Модели ролей: config/model-routing.yaml (python3 scripts/routing.py show). Данные встроены в страницу — файл работает офлайн, без сервера. Обновить: python3 scripts/pipeline_viewer.py"));
  }

  function renderAll() {
    var run = currentRun();
    if (!run) return;
    state.selectedStage = null;
    state.sourceFilter = null;
    renderPicker(run);
    renderHero(run);
    renderOverview(run);
    renderSources(run);
    renderAgents(run);
    renderFlow(run);
    renderTimeline(run);
    renderWorkers(run);
    renderCandidates(run);
    renderParams(run);
    applyTime(parseInt($("#timeRange").value, 10));
    highlightStage(state.selectedStage);
  }

  function selectRun(id) {
    state.runId = id;
    writeHash();
    renderAll();
  }

  function togglePlay() {
    var btn = $("#playBtn");
    if (state.playing) {
      clearInterval(state.timer);
      state.playing = false;
      btn.textContent = "▶";
      btn.setAttribute("aria-label", "Проиграть хронологию");
      return;
    }
    var run = currentRun();
    var total = ((run.timeline && run.timeline.steps) || []).length;
    if (total < 2) return;
    state.playing = true;
    btn.textContent = "❚❚";
    btn.setAttribute("aria-label", "Пауза");
    var i = parseInt($("#timeRange").value, 10);
    if (i >= total - 1) i = 0;
    state.timer = setInterval(function () {
      i += 1;
      $("#timeRange").value = String(i);
      applyTime(i);
      if (i >= total - 1) togglePlay();
    }, 850);
  }

  function readHash() {
    var hash = location.hash || "";
    var run = /run=([^&]+)/.exec(hash);
    var tab = /tab=([^&]+)/.exec(hash);
    return { run: run ? decodeURIComponent(run[1]) : null, tab: tab ? decodeURIComponent(tab[1]) : null };
  }

  function boot() {
    var initial = readHash();
    state.runId = initial.run && RUNS.some(function (r) { return r.id === initial.run; }) ? initial.run : (RUNS[0] ? RUNS[0].id : null);
    if (initial.tab && TABS.some(function (t) { return t.key === initial.tab; })) state.tab = initial.tab;
    $("#runPicker").addEventListener("change", function (e) { selectRun(e.target.value); });
    $("#timeRange").addEventListener("input", function (e) {
      if (state.playing) togglePlay();
      applyTime(parseInt(e.target.value, 10));
    });
    $("#playBtn").addEventListener("click", togglePlay);
    window.addEventListener("hashchange", function () {
      var next = readHash();
      if (next.tab && next.tab !== state.tab && TABS.some(function (t) { return t.key === next.tab; })) {
        state.tab = next.tab;
        applyTab();
      }
      if (next.run && next.run !== state.runId) selectRun(next.run);
    });
    renderFooter();
    renderAll();
    renderTabs();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
