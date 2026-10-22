"""Stage 2 — clusters: DeepSeek groups phrases into buyer jobs.

A cluster = one job a person wants to get done: canonical name, job statement,
product shape, intent class, head phrase, and a phrase list. The model measures nothing — it only
groups; its raw response is archived, and every cluster is reconciled against the backlog.
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

SYSTEM = """You group search phrases into buyer jobs (jobs to be done) for niche research.
Rules:
- One cluster = one concrete job a person wants to get done, not a topic and not a product category.
- The head phrase (head_phrase) is the most general phrase of the cluster from the list; the numbers will be queried for it later.
- product_shape: tool (a tool/SaaS), template (a template/checklist), service (a service by people), content (an article/training), unknown.
- intent: commercial (they are looking for something to buy: tool/software/pricing/alternative/service), tool_shaped (the phrase literally describes
  a tool: calculator/checker/validator/generator/converter/template), informational (how/what/why), navigational (a brand).
- Do not invent phrases: phrases contains only phrases from the input list, verbatim.
- Phrases that do not form a job (branded, random ones) go into the cluster with name "other".
- Do not create a cluster from one or two phrases: if a job does not gather at least four phrases, send them to "other".
- A shared word is not a job. Different buyers and different jobs are not merged, even when the phrases share
  a word: "referral" in HR recruiting, in patient referrals, and in affiliate marketing is three different
  jobs; "pim" in product catalogs, in Entra Privileged Identity Management, and in a candy brand is three different
  subjects; "migrate to shopify" for a merchant and "shopify to woocommerce" for a developer are two different jobs.
  If a group shows two buyers or two subjects, make two clusters.
- As many clusters per batch as there are jobs. Do not fit a number: twelve clean clusters
  and a large "other" is better than twenty-five where each holds three different jobs.
Reply with JSON only, shaped {"clusters":[{"name":"...","job":"...","product_shape":"...","intent":"...","head_phrase":"...","phrases":["..."]}]}.
name is a short English job name (3–6 words), job is one sentence in Russian."""


# Tool shapes: if the head phrase literally names a tool, the cluster cannot be
# "informational". The list is the same as the tool_shaped operator at the expand stage.
TOOL_SHAPES = ("tool", "tools", "maker", "generator", "checker", "check", "validator", "converter",
               "calculator", "software", "template", "templates", "monitor", "tracker", "linter",
               "audit", "analyzer", "scanner", "tester", "test", "api", "app", "platform")


def tool_shaped(phrase):
    """The phrase literally describes a tool."""
    words = set((phrase or "").lower().replace("-", " ").split())
    return any(shape in words for shape in TOOL_SHAPES)


def tool_shaped_cluster(head, phrases, *, share=0.25):
    """The cluster intent is at least tool_shaped when the phrases show it, not the model's opinion.

    The split pass produced subclusters where the model labeled informational a cluster headed
    "youtube thumbnail maker"; the intent gate removed them for labeling, not for the niche. One head
    is not enough: a deliverability cluster arrived headed "email deliverability check" while its
    most voluminous phrases were tester, tools, checker, platform. So the phrase share counts too.
    """
    if tool_shaped(head):
        return True
    if not phrases:
        return False
    return sum(1 for p in phrases if tool_shaped(p)) / len(phrases) >= share


def _coverage(phrase, phrases):
    """How many other cluster phrases contain all words of this phrase."""
    bag = set(phrase.lower().split())
    return sum(1 for other in phrases if other != phrase and bag <= set(other.lower().split()))


def central_phrase(phrases):
    """The most general cluster phrase: the one whose words occur in the largest number of others.

    Previously, when the model's head phrase was unusable, the first list item was substituted, and the list
    arrives sorted — that is, the first alphabetically. That is how a podcast-transcription cluster got named
    "apple podcast hide transcript" with zero volume, and the screening signals were collected for that
    same phrase: the marketplaces, the SERP, and the marketplace answered about the wrong job. The judge
    dropped one whole cluster because of that.
    """
    if not phrases:
        return None
    return min(phrases, key=lambda p: (-_coverage(p, phrases), len(p.split()), p.lower()))


def better_head(head, phrases, *, min_coverage=5, ratio=5):
    """The model's head phrase, or the more general one if the model chose a narrow tail.

    The fallback only helps when the model named a phrase outside the cluster. But it regularly
    picks a real yet narrow tail: "atlantis terraform drift" while "terraform drift" is alive, or
    "how much tokens cost" while "token cost" is alive. Such a tail has zero volume, and the
    screening signals are collected for exactly it, so the verdict ends up about the wrong job.
    The difference is visible through coverage: a tail's words occur in one or two cluster phrases,
    a general phrase's words in a hundred (1 vs 99 and 1 vs 277 in the devops-ai-2026-09-22 run).
    The replacement happens only when the difference is severalfold, so equal heads stay as they are.
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
    """One or more model calls; with several batches, one shared merge pass."""
    settings = config.get("cluster") or {}
    choice = (config.get("models") or {}).get("cluster")
    batch = int(settings.get("phrases_per_call") or 350)
    allowed = {" ".join(p.lower().split()): p for p in phrases}
    clusters, source_ids, usage_total = [], [], {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    min_batch = int(settings.get("min_phrases_per_call") or 40)

    def ask_batch(chunk):
        """Clusters for one batch. A reply cut off by the output limit → halve the batch."""
        messages = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": "Phrases:\n" + json.dumps(chunk, ensure_ascii=False)}]
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
        # Merging collapses the same; splitting pulls apart the different. The order is exactly this,
        # otherwise a mix assembled by merging two batches would stay unnoticed.
        #
        # There are several passes: one resolves the top layer of the mix, but a subcluster can itself
        # turn out to be a mix. In the verification run of 2026-09-23, after one pass the skeptic
        # still found six jobs in "SEO rank tracking tools" and three in "media kit".
        # The loop stops when a pass changed nothing.
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
        # Splitting cuts each cluster separately and can therefore produce two almost
        # identical jobs from different parents: in the 2026-09-24 run "diagnose email
        # deliverability" and "email deliverability diagnostics" lived side by side and split
        # the volume of one niche in half. The final merge collapses such pairs back together.
        if len(clusters) > 1:
            clusters, after_split = _merge(run_id, clusters, choice, root, who)
            source_ids.append(after_split)
    return clusters, source_ids, usage_total


SPLIT_SYSTEM = """You are given one cluster of search phrases that is supposed to be one buyer job. Verify that.

Split ONLY when the cluster contains different SUBJECTS or different BUYERS:
- "referral": employee referrals in HR, patient referrals between clinics, and affiliate marketing are
  three different buyers, three clusters.
- "PIM": product catalogs, Entra Privileged Identity Management, RF intermodulation, and a candy
  brand are four different subjects.
- "migrate to shopify": a merchant moving to Shopify and a developer moving away from Shopify are different buyers
  and opposite directions.

Do NOT split when the subject and buyer are the same and only the journey stage or query shape differs.
The same person about the same job asks "what is X", "X best practices", "X tool",
"X pricing", "X alternatives", "how to fix X", "X course" — that is ONE job, and splitting it into
"definitions", "tools", "learning", and "diagnostics" is not allowed. Likewise do not split by platform,
by language, or by a synonym of the same subject.

If the cluster is about one job, return it as-is as a single group with coherent: true. If it is glued — divide it
into groups, each of which is one job. A glued cluster usually yields two or three groups; if more than four
groups come out, you are most likely cutting by journey stage, not by job — go back to one group.
Phrases that belong to no job go into the group with name "other".
Each phrase belongs to exactly one group, verbatim from the input list; invent nothing.

The groups' intent and product_shape are the same as the original cluster's, as long as the job is the same.
Change them only when a group is truly about something else: for example, a group entirely about "what is X"
becomes informational, and a group about hiring a contractor becomes service. A group containing "X tool",
"X software", "X checker" cannot be informational.

Reply with JSON only: {"coherent": true|false, "groups":[{"name":"...","job":"...","product_shape":"...",
"intent":"...","head_phrase":"...","phrases":["..."]}]}.
name is a short English job name (3–6 words), job is one sentence in Russian."""


def split_cluster(run_id, cluster, choice, root, who, *, min_phrases, sample, max_groups=4):
    """Split a cluster that holds several jobs. Returns a list of clusters.

    The skeptic caught glue in four of the eight clusters reviewed on September 22 — but caught it
    at the very end, after the cluster had already consumed measurements and review, and the verdict
    reduced to "the numbers were computed over a mixture". Here the same question is asked right after
    clustering, where it costs one cheap call and the answer can still change what we measure.

    A model error must not cost the cluster: on a truncated, incomplete reply, or lost phrases,
    the original cluster is returned untouched.
    """
    phrases = cluster.get("phrases") or []
    if len(phrases) < 2 * min_phrases:
        return [cluster]
    # A strided sample, not the first N: the list arrives sorted, and the first 120 phrases
    # of 399 are one alphabetical corner of the cluster. The "media kit" cluster never split
    # even though the skeptic found three jobs in it: the model simply never saw the other two thirds.
    step = max(1, (len(phrases) + sample - 1) // sample)
    shown = phrases[::step][:sample]
    messages = [{"role": "system", "content": SPLIT_SYSTEM},
                {"role": "user", "content": json.dumps({"name": cluster.get("name"), "job": cluster.get("job"),
                                                        "phrases": shown}, ensure_ascii=False)}]
    try:
        # temperature=0: splitting changes the composition of clusters, and therefore what we measure.
        # On the same cluster, the answer must not drift from run to run.
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
        # Fields the model omitted inherit from the parent, not from _clean's defaults:
        # otherwise a subcluster quietly becomes informational and fails the intent gate because
        # the model did not repeat the field — a verdict about the reply shape, not about the niche.
        for field in ("intent", "product_shape"):
            if not group.get(field) or group.get(field) == cluster.get(field):
                cleaned[field] = cluster.get(field, cleaned[field])
        out.append({**cluster, **cleaned, "phrases": fresh,
                    "head_phrase": better_head(cleaned["head_phrase"], fresh),
                    "split_from": cluster.get("name"), "source_id": source_id})
    # Too fine a split is a sign of cutting by journey stage, not by job: in review, a clean
    # "email deliverability" cluster fell apart into seven pieces like "definitions",
    # "tools", "learning". Such a reply is discarded entirely.
    if len(out) > int(max_groups):
        return [cluster]
    # A split that lost more than half the shown phrases is not a split but data loss.
    if len(out) < 2 or len(taken) < len(shown) // 2:
        return [cluster]
    _assign_rest([p for p in phrases if p not in taken], out)
    for part in out:
        part["head_phrase"] = better_head(part["head_phrase"], part["phrases"])
    return out


def _assign_rest(rest, groups):
    """Phrases outside the sample go to the closest group by words, not to the trash.

    The model is shown a sample (120 phrases of 399), and previously everything that did not make it
    simply disappeared: the "media kit" cluster lost three quarters of its phrases after splitting,
    and the volume was no longer computed over it. Affinity is measured as the share of shared words
    with the group's phrases.
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
            # The vocabulary does not grow while distributing: otherwise the first group collects random words
            # and starts attracting everything to itself. Affinity is measured only over the phrases
            # the model placed into the group itself.


MERGE_SYSTEM = """You are given a list of job clusters from different phrase batches. Merge duplicates: clusters about the same buyer
job. Reply with JSON only: {"groups":[{"name":"...","job":"...","product_shape":"...","intent":"...","members":[indices]}]}.
Each index belongs to exactly one group. Do not merge different jobs just because they are about the same platform."""


def _merge(run_id, clusters, choice, root, who, *, window=60, min_window=10):
    """Merging by windows, not one call over the whole list.

    The creator-marketing-2026-09-22 run died here: 141 clusters in one request, and the reply
    was cut at the 8000-token output limit. The main pass has long known how to halve a batch,
    but merging did not, so the whole run stalled at the clustering stage.

    A window cuts the list into parts; a second pass catches duplicates that landed in different parts.
    No more than three passes: after that the list no longer shrinks and the calls cost money.
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
    """Clustering without a model: the common "core" of a phrase after stripping modifiers.

    records — expand-stage records (phrase, source, operator) or plain strings. Intent and head
    phrase are computed only over autocomplete phrases: operator phrases ("seed checker") are our
    templates, not observed demand, and must not vote. Marked method=heuristic.
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
        out.append({"name": key.title()[:80], "job": f"Jobs around \"{key}\" (heuristic grouping, no model)",
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
    text = (f"{summary['clusters']} clusters from {summary['phrases_clustered']} phrases; "
            f"already in backlog {summary['already_in_backlog']}; tokens {summary['tokens'].get('total_tokens', 0)}")
    if summary.get("method") == "heuristic":
        text += f"; WARNING: clustering is heuristic without a model ({summary.get('llm_error') or 'method set explicitly'})"
    return text
