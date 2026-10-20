"""Офлайн-проверки пайплайна v2: конфиг, операторы, разбор, гейты, мост, бэклог, отчёт. Без сети и без моделей."""
import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.pipeline import backlog, bridge, cluster, expand, gate, measure, report, review, screen  # noqa: E402
from scripts.pipeline.config import DEFAULTS, cpc_band, load_config, select_themes, validate_config  # noqa: E402
from scripts.pipeline.connectors import serp  # noqa: E402
from scripts.pipeline.run import create_run, finish_stage, read_stage_jsonl, write_stage_jsonl  # noqa: E402
from scripts.pipeline.viewer import collect  # noqa: E402

THEME = {"id": "t", "title": "T", "seeds": ["shopify migration"], "platforms": ["shopify", "woocommerce"], "verticals": ["fashion"]}


# Фон поиска в тестах задаётся явно: гейт тренда судит относительно него, и брать его из
# рабочего config/calibration-baseline.json значит менять вердикты при каждой новой калибровке.
FON = {"trend_yoy_median": -0.216, "at": "2026-09-21T00:00:00+00:00", "run": "calib-test"}


def config_with(**override):
    cfg = json.loads(json.dumps(DEFAULTS))
    cfg["themes"] = [THEME]
    cfg.update(override)
    return cfg


class ConfigTests(unittest.TestCase):
    def test_repo_config_loads_and_selects_themes(self):
        cfg = load_config()
        self.assertIn(cfg["mode"], ("self_serve", "services", "any"))
        self.assertTrue(select_themes(cfg, None))
        with self.assertRaisesRegex(ValueError, "unknown themes"):
            select_themes(cfg, ["nope"])

    def test_validation_reports_clear_errors(self):
        cfg = config_with(mode="wrong")
        cfg["cpc"]["self_serve"] = [12, 2]
        errors = validate_config(cfg)
        self.assertTrue(any("mode" in e for e in errors))
        self.assertTrue(any("cpc.self_serve" in e for e in errors))
        self.assertIsNone(cpc_band(config_with(mode="any")))


class ExpandTests(unittest.TestCase):
    def test_operators_generate_pairs_alternatives_and_tool_shapes(self):
        cfg = config_with()
        phrases = expand.operator_phrases(THEME, cfg)
        text = [p for p, _, _ in phrases]
        self.assertIn("shopify to woocommerce migration", text)
        self.assertIn("woocommerce alternative", text)
        self.assertIn("shopify migration calculator", text)
        self.assertIn("shopify migration for fashion", text)

    def test_normalize_and_walk(self):
        self.assertEqual(expand.normalize("  Shopify   Migration! "), "shopify migration")
        self.assertEqual(sorted(expand._walk_strings({"a": [{"keyword": "x"}, "y"], "b": {"suggestion": "z"}})), ["x", "y", "z"])


class ClusterTests(unittest.TestCase):
    def test_clean_keeps_only_input_phrases_and_fixes_head(self):
        allowed = {"shopify migration": "Shopify migration", "shopify migration tool": "shopify migration tool"}
        cleaned = cluster._clean({"name": "Migrate to Shopify", "job": "x", "product_shape": "tool", "intent": "commercial",
                                  "head_phrase": "made up", "phrases": ["Shopify migration", "shopify migration tool", "invented"]}, allowed)
        self.assertEqual(cleaned["phrases"], ["Shopify migration", "shopify migration tool"])
        self.assertEqual(cleaned["head_phrase"], "Shopify migration")
        self.assertEqual(cleaned["intent"], "commercial")

    def test_head_phrase_falls_back_to_the_most_general_phrase_not_the_first(self):
        """Список приходит отсортированным, поэтому «первая» значило «первая по алфавиту»."""
        phrases = ["apple podcast hide transcript", "podcast transcript", "podcast audio transcript",
                   "podcast episode transcript", "do podcasts have transcripts"]
        self.assertEqual(cluster.central_phrase(phrases), "podcast transcript")
        allowed = {p.lower(): p for p in phrases}
        cleaned = cluster._clean({"name": "Podcast transcripts", "job": "x", "product_shape": "tool",
                                  "intent": "commercial", "head_phrase": "не из кластера", "phrases": phrases}, allowed)
        self.assertEqual(cleaned["head_phrase"], "podcast transcript")
        self.assertIsNone(cluster.central_phrase([]))

    def test_a_tool_shaped_head_cannot_be_informational(self):
        """Расщепление давало informational кластеру с головой «youtube thumbnail maker»."""
        allowed = {"youtube thumbnail maker": "youtube thumbnail maker",
                   "youtube thumbnail maker free": "youtube thumbnail maker free",
                   "how to make a youtube thumbnail": "how to make a youtube thumbnail"}
        cleaned = cluster._clean({"name": "YouTube thumbnail creation", "job": "x", "product_shape": "tool",
                                  "intent": "informational", "head_phrase": "youtube thumbnail maker",
                                  "phrases": list(allowed)}, allowed)
        self.assertEqual(cleaned["intent"], "tool_shaped")
        # Голова без формы инструмента остаётся как размечена.
        plain = {"what is a youtube thumbnail": "what is a youtube thumbnail",
                 "youtube thumbnail size": "youtube thumbnail size"}
        self.assertEqual(cluster._clean({"name": "n", "job": "x", "product_shape": "content",
                                         "intent": "informational", "head_phrase": "what is a youtube thumbnail",
                                         "phrases": list(plain)}, plain)["intent"], "informational")
        self.assertTrue(cluster.tool_shaped("email marketing template"))
        self.assertFalse(cluster.tool_shaped("email marketing best practices"))

    def test_a_narrow_tail_loses_the_head_to_the_general_phrase(self):
        """Модель называет головой частный хвост, а по нему собираются сигналы отсева."""
        phrases = ["atlantis terraform drift", "atlantis terraform drift detection", "terraform drift"]
        phrases += [f"terraform drift {w}" for w in
                    ("detection", "detection tools", "check", "remediation", "aws", "azure", "fix")]
        self.assertEqual(cluster.better_head("atlantis terraform drift", phrases), "terraform drift")
        # Равные по общности головы не трогаются: замена только при разнице в разы.
        peers = ["cloud functions cost", "cloud run cost", "cloud sql cost", "cloud storage cost"]
        self.assertEqual(cluster.better_head("cloud functions cost", peers), "cloud functions cost")


class BacklogFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = config_with()

    def tearDown(self):
        self.tmp.cleanup()

    def test_numbers_from_an_older_metric_definition_are_not_reused(self):
        """Возраст — не единственный способ протухнуть: определение метрики тоже меняется."""
        cluster = {"name": "Uptime monitoring", "theme": "t"}
        full = {"volume_sum": 900, "volume_recent": 880, "trend_yoy": -0.1, "cpc_median": 5.0}
        backlog.touch(self.cfg, cluster, "r1", status="measured", numbers=full, root=self.root)
        key = backlog.key_for(cluster["name"])
        self.assertIsNotNone(backlog.fresh_numbers(self.cfg, key, root=self.root))

        # Запись без полей, по которым судит гейт, переиспользовать нельзя: кластер прошёл бы
        # по volume_sum с пометкой «тренд неизвестен», хотя перемерить ничего не стоит.
        for field in backlog.GATE_FIELDS:
            backlog.touch(self.cfg, cluster, "r1", status="measured",
                          numbers={k: v for k, v in full.items() if k != field}, root=self.root)
            self.assertIsNone(backlog.fresh_numbers(self.cfg, key, root=self.root), field)
        backlog.touch(self.cfg, cluster, "r1", status="measured", numbers=full, root=self.root)

        records = backlog.load(self.cfg, self.root)
        records[key]["last_numbers"]["metrics_version"] = backlog.METRICS_VERSION - 1
        backlog.save(self.cfg, records, self.root)
        self.assertIsNone(backlog.fresh_numbers(self.cfg, key, root=self.root))

        # Числа, записанные до появления версии, тоже не переиспользуются.
        records[key]["last_numbers"].pop("metrics_version")
        backlog.save(self.cfg, records, self.root)
        self.assertIsNone(backlog.fresh_numbers(self.cfg, key, root=self.root))


class ReportNamingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / "creator-marketing-2026-09-22"
        self.folder.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_runs_carry_the_run_id_in_the_file_name(self):
        """«report.pdf» в Telegram одинаков для всех прогонов; id несёт и тему, и дату."""
        from scripts.report_pdf import report_stem
        self.assertEqual(report_stem(self.folder), "report-creator-marketing-2026-09-22")

    def test_an_older_run_keeps_reading_under_its_old_name(self):
        from scripts.report_pdf import report_stem
        (self.folder / "report.md").write_text("старый отчёт", encoding="utf-8")
        self.assertEqual(report_stem(self.folder), "report")

    def test_narrative_digest_ignores_the_machine_section(self):
        """Хэш обязан ловить правку человеческой части и не шуметь на пересборке машинной."""
        head = "# Отчёт\n\nЧеловеческий текст.\n\n"
        machine = report.BEGIN + "\n\nчисла\n\n" + report.END + "\n"
        first = report.narrative_digest(head + machine)
        self.assertEqual(first["sha256"], report.narrative_digest(head + report.BEGIN + "\n\nдругие числа\n\n" + report.END)["sha256"])
        self.assertNotEqual(first["sha256"], report.narrative_digest(head.replace("текст", "текст правленый") + machine)["sha256"])


class ClusterMergeTests(unittest.TestCase):
    def test_merge_windows_the_listing_and_halves_on_truncation(self):
        """141 кластер в одном запросе обрезало лимитом вывода, и прогон вставал целиком."""
        from unittest import mock
        from scripts.pipeline.llmjson import TruncatedReply

        sizes = []

        def fake_ask_json(run_id, role, messages, **kw):
            listing = json.loads(messages[1]["content"])
            sizes.append(len(listing))
            if len(listing) > 40:
                raise TruncatedReply("ответ оборван на лимите вывода")
            groups = [{"name": listing[i]["name"], "job": "j", "members": [x["index"] for x in listing[i:i + 2]]}
                      for i in range(0, len(listing), 2)]
            return {"groups": groups}, f"src-{len(sizes)}", {}

        clusters = [{"name": f"c{i}", "job": "j", "head_phrase": f"p{i}", "phrases": [f"p{i}"]} for i in range(141)]
        with mock.patch.object(cluster, "ask_json", fake_ask_json):
            merged, source_id = cluster._merge("r", clusters, None, ".", None)
        self.assertLess(len(merged), len(clusters))
        self.assertTrue(source_id)
        self.assertLessEqual(max(sizes), 60)
        self.assertTrue(any(s <= 30 for s in sizes), "порция не делилась при обрыве")


class ClusterSplitTests(unittest.TestCase):
    """Расщепление склеенных кластеров: см. docs/journal.md, пункт про кластеризацию."""

    GLUED = {"name": "referral tracking", "job": "учёт рефералов", "product_shape": "tool",
             "intent": "commercial", "head_phrase": "referral tracking",
             "phrases": ["referral tracking", "employee referral tracking", "employee referral program hr",
                         "patient referral tracking", "patient referral clinic", "referral tracking software",
                         "customer referral program", "affiliate referral tracking"]}

    def _split(self, reply, **kw):
        from unittest import mock
        with mock.patch.object(cluster, "ask_json", lambda *a, **k: (reply, "src-1", {})):
            return cluster.split_cluster("r", dict(self.GLUED), None, ".", None,
                                         min_phrases=kw.pop("min_phrases", 2), sample=120, **kw)

    def test_a_glued_cluster_is_split_by_buyer(self):
        reply = {"coherent": False, "groups": [
            {"name": "employee referral tracking", "job": "HR", "phrases":
                ["employee referral tracking", "employee referral program hr", "referral tracking"]},
            {"name": "patient referral tracking", "job": "клиники", "phrases":
                ["patient referral tracking", "patient referral clinic"]},
            {"name": "customer referral tracking", "job": "маркетинг", "phrases":
                ["customer referral program", "affiliate referral tracking", "referral tracking software"]}]}
        out = self._split(reply)
        self.assertEqual([c["name"] for c in out],
                         ["employee referral tracking", "patient referral tracking", "customer referral tracking"])
        # Каждая фраза попадает ровно в один подкластер.
        seen = [p for c in out for p in c["phrases"]]
        self.assertEqual(len(seen), len(set(seen)))
        # Поля, которых модель не назвала, наследуются от родителя, а не падают в informational.
        self.assertTrue(all(c["intent"] == "commercial" for c in out))
        self.assertTrue(all(c["split_from"] == "referral tracking" for c in out))

    def test_phrases_outside_the_sample_are_not_lost(self):
        """Модели показывают выборку; всё, что в неё не попало, раньше просто исчезало."""
        from unittest import mock
        big = dict(self.GLUED)
        big["phrases"] = self.GLUED["phrases"] + [f"employee referral {i}" for i in range(20)] \
                                              + [f"patient referral {i}" for i in range(20)]
        reply = {"coherent": False, "groups": [
            {"name": "employee referral tracking", "phrases":
                ["employee referral tracking", "employee referral program hr", "referral tracking"]},
            {"name": "patient referral tracking", "phrases":
                ["patient referral tracking", "patient referral clinic", "referral tracking software"]}]}
        with mock.patch.object(cluster, "ask_json", lambda *a, **k: (reply, "src-1", {})):
            out = cluster.split_cluster("r", big, None, ".", None, min_phrases=2, sample=6)
        self.assertEqual(sum(len(c["phrases"]) for c in out), len(big["phrases"]))
        by_name = {c["name"]: c["phrases"] for c in out}
        self.assertIn("employee referral 7", by_name["employee referral tracking"])
        self.assertIn("patient referral 7", by_name["patient referral tracking"])

    def test_the_sample_strides_across_the_cluster(self):
        """Первые N фраз отсортированного списка — один алфавитный угол кластера."""
        from unittest import mock
        big = dict(self.GLUED)
        big["phrases"] = [f"{letter} referral tracking" for letter in "abcdefghij"]
        seen = {}

        def capture(run_id, role, messages, **kw):
            seen["phrases"] = json.loads(messages[1]["content"])["phrases"]
            return {"coherent": True, "groups": []}, "src-1", {}

        with mock.patch.object(cluster, "ask_json", capture):
            cluster.split_cluster("r", big, None, ".", None, min_phrases=2, sample=5)
        self.assertIn("a referral tracking", seen["phrases"])
        self.assertIn("i referral tracking", seen["phrases"])

    def test_a_coherent_cluster_is_returned_untouched(self):
        self.assertEqual(self._split({"coherent": True, "groups": []}), [dict(self.GLUED)])

    def test_slicing_by_funnel_stage_is_rejected(self):
        """Чистый кластер разваливался на «определения», «инструменты», «обучение» — это не работы."""
        reply = {"coherent": False, "groups": [{"name": f"g{i}", "phrases": [ph]}
                                               for i, ph in enumerate(self.GLUED["phrases"])]}
        self.assertEqual(self._split(reply, min_phrases=1, max_groups=4), [dict(self.GLUED)])

    def test_a_split_that_loses_most_phrases_is_rejected(self):
        """Модель, молча потерявшая большинство фраз, отдала не расщепление, а обрезок."""
        reply = {"coherent": False, "groups": [
            {"name": "a", "phrases": ["referral tracking", "referral tracking software"]},
            {"name": "b", "phrases": ["patient referral tracking", "invented phrase"]}]}
        self.assertEqual(self._split(reply), [dict(self.GLUED)])

    def test_a_model_failure_keeps_the_cluster(self):
        from unittest import mock
        from scripts.pipeline.llmjson import TruncatedReply

        def boom(*a, **k):
            raise TruncatedReply("оборван")

        with mock.patch.object(cluster, "ask_json", boom):
            self.assertEqual(cluster.split_cluster("r", dict(self.GLUED), None, ".", None,
                                                   min_phrases=2, sample=120), [dict(self.GLUED)])

    def test_a_small_cluster_is_not_worth_a_call(self):
        from unittest import mock
        called = []
        with mock.patch.object(cluster, "ask_json", lambda *a, **k: called.append(1) or ({}, "s", {})):
            cluster.split_cluster("r", dict(self.GLUED), None, ".", None, min_phrases=8, sample=120)
        self.assertEqual(called, [])


class ReviewSelectTests(unittest.TestCase):
    @staticmethod
    def _cluster(name, volume):
        return {"cluster_id": name, "name": name, "numbers": {"volume_recent": volume},
                "gate": {"status": "passed", "passed": True}}

    def test_selection_spreads_across_volume_bands(self):
        """Двенадцать самых крупных — это двенадцать самых конкурентных категорий."""
        gated = [self._cluster(f"big-{i}", 100000 - i) for i in range(5)]
        gated += [self._cluster(f"mid-{i}", 5000 - i) for i in range(5)]
        gated += [self._cluster(f"small-{i}", 500 - i) for i in range(5)]
        chosen = [c["cluster_id"] for c in review.select(gated, config_with(review={"max_clusters": 6}))]
        self.assertEqual(len(chosen), 6)
        self.assertEqual(sum(1 for c in chosen if c.startswith("big")), 2)
        self.assertEqual(sum(1 for c in chosen if c.startswith("mid")), 2)
        self.assertEqual(sum(1 for c in chosen if c.startswith("small")), 2)
        # Внутри полосы объём по-прежнему решает.
        self.assertIn("big-0", chosen)
        self.assertIn("small-0", chosen)

    def test_an_empty_band_does_not_waste_the_budget(self):
        gated = [self._cluster(f"mid-{i}", 5000 - i) for i in range(4)]
        chosen = [c["cluster_id"] for c in review.select(gated, config_with(review={"max_clusters": 3}))]
        self.assertEqual(chosen, ["mid-0", "mid-1", "mid-2"])


class ScreenTests(unittest.TestCase):
    def test_key_words_and_title_match(self):
        self.assertEqual(screen.key_words("shopify to woocommerce migration tool"), ["shopify", "woocommerce", "migration"])
        self.assertTrue(screen._title_matches("Migrate my Shopify store", ["shopify", "migration"]))
        self.assertFalse(screen._title_matches("Logo design", ["shopify", "migration"]))

    def test_parse_upwork_tolerates_shapes(self):
        payload = {"jobs": [{"title": "Shopify migration", "budget": {"amount": 500}, "posted_on": "2099-01-01T00:00:00Z",
                             "proposals_tier": "Fewer than 5", "client": {"total_spent": 12000}}]}
        jobs = screen.parse_upwork(payload)
        self.assertEqual(jobs[0]["budget_usd"], 500)
        self.assertEqual(jobs[0]["client_spent"], 12000)

    def test_evaluate_pending_when_bridge_missing_and_pass_when_signals_good(self):
        """Ждать и отказывать можно только из-за обязательных проверок, а их задаёт конфиг."""
        cfg = config_with()
        self.assertEqual(cfg["screen"]["require"], ["software"])
        waiting = {"money": {"upwork": {"status": "collected", "jobs": 4}, "freelancer": {"status": "collected", "jobs": 0}},
                   "software": {"status": "pending_bridge"}, "reachable": {"status": "collected", "brands_top5_max": 1}}
        self.assertEqual(screen.evaluate(waiting, cfg)["status"], "pending")
        good = dict(waiting, software={"status": "collected", "paid_with_users": 1, "gap": False})
        self.assertEqual(screen.evaluate(good, cfg)["status"], "passed")
        bad = dict(good, software={"status": "collected", "paid_with_users": 0, "gap": False})
        verdict = screen.evaluate(bad, cfg)
        self.assertEqual(verdict["status"], "failed")
        self.assertIn("software", verdict["failed"])

    def test_missing_upwork_answer_no_longer_holds_a_self_serve_cluster(self):
        """Заказов на бирже у продукта за $19/мес нет по построению: ждать их незачем.

        В прогоне 2026-09-20 именно это правило сняло 42 кластера из 60 — за то, что они
        похожи на то, что мы ищем. Сигнал остаётся в карточке, но вердикт больше не держит.
        """
        cfg = config_with()
        signals = {"money": {"upwork": {"status": "pending_bridge"}, "freelancer": {"status": "collected", "jobs": 0}},
                   "software": {"status": "collected", "paid_with_users": 2, "gap": False},
                   "reachable": {"status": "collected", "brands_top5_max": 5}}
        verdict = screen.evaluate(signals, cfg)
        self.assertEqual(verdict["status"], "passed")
        self.assertFalse(verdict["checks"]["money"])
        self.assertFalse(verdict["checks"]["reachable"])


class MeasureAndGateTests(unittest.TestCase):
    def test_aggregate_and_trend(self):
        rows = [{"phrase": "a", "volume": 100, "cpc": 3.0, "competition": 0.1}, {"phrase": "b", "volume": 50, "cpc": 9.0, "competition": 0.3},
                {"phrase": "c", "volume": 0, "cpc": 0, "competition": None}]
        agg = measure.aggregate(rows, "a", "test", ["src-x"])
        self.assertEqual(agg["volume_sum"], 150)
        self.assertEqual(agg["cpc_median"], 6.0)
        self.assertEqual(agg["phrases_with_volume"], 2)
        self.assertGreater(measure.trend_slope([10, 12, 14, 16]), 0)
        self.assertIsNone(measure.trend_slope([0, 0]))

    def test_parse_ubersuggest_shapes(self):
        rows, monthly = measure.parse_ubersuggest({"searched_keywords": [{"keyword": "x", "volume": 5, "cpcDollars": 1.5, "competition": 0.2}],
                                                   "results": [{"keyword": "y", "volume": 7, "cpc": 2}, {"keyword": "z"}]})
        self.assertEqual([r["phrase"] for r in rows], ["x", "y"])
        self.assertIsNone(monthly)

    def test_gate_modes(self):
        base = {"intent": "commercial", "phrases": ["uptime monitoring tool"],
                "numbers": {"status": "collected", "volume_sum": 900, "volume_recent": 900, "cpc_median": 5.0,
                            "trend_slope": 0.0, "trend_yoy": 0.0},
                "screen": {"checks": {"money": True, "reachable": True, "software": True}}}
        self.assertTrue(gate.evaluate(base, config_with(), fon=FON)["passed"])
        self.assertIn("cpc_band[services]:5.0 not in [12, 60]", gate.evaluate(base, config_with(mode="services"), fon=FON)["failed_rules"])
        self.assertTrue(gate.evaluate(dict(base, numbers={**base["numbers"], "cpc_median": 30}), config_with(mode="any"), fon=FON)["passed"])
        unknown = gate.evaluate(dict(base, numbers={"status": "not_collected"}), config_with(), fon=FON)
        self.assertEqual(unknown["status"], "unknown")
        self.assertFalse(unknown["passed"])
        # Фразы кластера не описывают инструмент, поэтому разметка модели принимается как есть.
        teaching = dict(base, intent="informational", head_phrase="what is uptime monitoring",
                        phrases=["what is uptime monitoring", "why servers go down"])
        self.assertIn("intent:informational", gate.evaluate(teaching, config_with(), fon=FON)["failed_rules"])

    def test_trend_gate_judges_against_the_search_baseline_not_zero(self):
        """Падение наравне с фоном — не повод для отказа: падает весь поиск, а не ниша."""
        base = {"intent": "commercial", "phrases": ["status page software"],
                "numbers": {"status": "collected", "volume_recent": 900, "cpc_median": 5.0},
                "screen": {"checks": {"software": True}}}
        with_fall = dict(base, numbers={**base["numbers"], "trend_yoy": -0.25})
        self.assertTrue(gate.evaluate(with_fall, config_with(), fon=FON)["passed"])
        self.assertIn("min_trend_yoy_vs_baseline",
                      " ".join(gate.evaluate(dict(base, numbers={**base["numbers"], "trend_yoy": -0.7}),
                                             config_with(), fon=FON)["failed_rules"]))
        # Без снятого фона порог строже, и кластер об этом сообщает, а не молчит.
        blind = gate.evaluate(with_fall, config_with(), fon={})
        self.assertIn("baseline_missing", " ".join(blind["flags"]))

    def test_crowded_serp_marks_but_does_not_reject(self):
        """Плотность брендов в топ-5 — признак, что за трафик платят, а не причина не заходить."""
        cluster = {"intent": "commercial", "phrases": ["invoicing software"],
                   "numbers": {"status": "collected", "volume_recent": 900, "cpc_median": 8.0, "trend_yoy": 0.0},
                   "screen": {"checks": {"software": True, "reachable": False}}}
        verdict = gate.evaluate(cluster, config_with(), fon=FON)
        self.assertTrue(verdict["passed"])
        self.assertIn("crowded_serp", " ".join(verdict["flags"]))

    def test_services_shaped_cluster_fails_in_self_serve(self):
        """Ставка услугу от продукта не отличает — отличает формулировка фраз."""
        numbers = {"status": "collected", "volume_recent": 2000, "cpc_median": 23.0, "trend_yoy": -0.2}
        services = {"intent": "commercial", "screen": {"checks": {"software": True}}, "numbers": numbers,
                    "phrases": ["erp implementation consultant", "sap implementation partner", "erp consulting firm"]}
        self.assertIn("services_shaped", " ".join(gate.evaluate(services, config_with(), fon=FON)["failed_rules"]))
        product = dict(services, phrases=["email verification service", "email verification api", "bulk email verification"])
        self.assertTrue(gate.evaluate(product, config_with(), fon=FON)["passed"])
        # Слово после «for» называет покупателя: продукт для агентств — всё ещё продукт.
        sold_to_agencies = dict(services, phrases=["social media scheduling for agencies",
                                                   "invoicing software for freelancers", "link in bio tool"])
        self.assertTrue(gate.evaluate(sold_to_agencies, config_with(), fon=FON)["passed"])

    def test_head_phrase_nobody_searches_is_flagged(self):
        """По головной фразе собраны сигналы отсева: её нулевой объём обесценивает их все."""
        cluster = {"intent": "commercial", "phrases": ["apple podcast hide transcript"],
                   "head_phrase": "apple podcast hide transcript",
                   "numbers": {"status": "collected", "volume_recent": 12398, "volume_head": 0,
                               "cpc_median": 3.06, "trend_yoy": 0.098},
                   "screen": {"checks": {"software": True}}}
        self.assertIn("head_no_volume", " ".join(gate.evaluate(cluster, config_with(), fon=FON)["flags"]))

    def test_a_cluster_whose_phrases_describe_a_tool_is_not_rejected_as_informational(self):
        """Кластер с головой «email deliverability check» снимался за разметку, а не за нишу."""
        cluster = {"intent": "informational", "head_phrase": "email deliverability check",
                   "phrases": ["email deliverability check", "email deliverability tester",
                               "email deliverability tools", "email deliverability checker",
                               "email deliverability platform", "why does email go to spam"],
                   "numbers": {"status": "collected", "volume_recent": 4169, "cpc_median": 7.93, "trend_yoy": 0.073},
                   "screen": {"checks": {"software": True}}}
        verdict = gate.evaluate(cluster, config_with(), fon=FON)
        self.assertTrue(verdict["passed"])
        self.assertIn("intent_relabelled", " ".join(verdict["flags"]))
        # Настоящий информационный кластер остаётся отклонённым.
        teaching = dict(cluster, head_phrase="what is email deliverability",
                        phrases=["what is email deliverability", "email deliverability meaning",
                                 "why does email go to spam", "email deliverability explained"])
        self.assertIn("intent:informational", gate.evaluate(teaching, config_with(), fon=FON)["failed_rules"])

    def test_one_phrase_cluster_is_flagged(self):
        cluster = {"intent": "commercial", "phrases": ["trace ai agents"],
                   "numbers": {"status": "collected", "volume_recent": 49870, "cpc_median": 8.0, "trend_yoy": 0.0,
                               "volume_top_share": 0.993},
                   "screen": {"checks": {"software": True}}}
        self.assertIn("one_phrase", " ".join(gate.evaluate(cluster, config_with(), fon=FON)["flags"]))


class SerpTests(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(serp.classify("https://www.reddit.com/r/shopify/comments/1"), "forum")
        self.assertEqual(serp.classify("https://www.youtube.com/watch?v=1"), "video")
        self.assertEqual(serp.classify("https://apps.shopify.com/migrate"), "marketplace")
        self.assertEqual(serp.classify("https://litextension.com/"), "brand")
        self.assertEqual(serp.classify("https://vendor.com/blog/how-to-migrate"), "blog")
        self.assertEqual(serp.classify("https://help.shopify.com/en/manual"), "docs")


class RunBridgeBacklogReportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.cfg = config_with()
        self.cfg["notify"]["telegram"] = False
        self.cfg["backlog"]["path"] = "backlog/clusters.jsonl"
        self.manifest = create_run(self.root, "demand-test", self.cfg, [THEME], mode="self_serve", objective="test", authorization="synthetic")
        self.folder = self.root / "runs" / "demand-test"

    def tearDown(self):
        self._tmp.cleanup()

    def test_bridge_roundtrip(self):
        req = bridge.plan(self.folder, "upwork.find_jobs", {"action": "search", "params": {"title": "shopify migration"}}, purpose="p", stage="screen")
        self.assertEqual(req["status"], "pending")
        self.assertIsNone(bridge.response(self.folder, req))
        again = bridge.plan(self.folder, "upwork.find_jobs", {"action": "search", "params": {"title": "shopify migration"}}, purpose="p", stage="screen")
        self.assertEqual(again["id"], req["id"])
        (self.folder / req["response_path"]).parent.mkdir(parents=True, exist_ok=True)
        (self.folder / req["response_path"]).write_text(json.dumps({"jobs": [{"title": "x", "budget": 300}]}), encoding="utf-8")
        out = bridge.ingest(self.folder, root=self.root)
        self.assertEqual(out["ingested"], [req["id"]])
        done = bridge.load_requests(self.folder)[0]
        self.assertEqual(done["status"], "done")
        self.assertEqual(bridge.response(self.folder, done)["jobs"][0]["budget"], 300)
        self.assertTrue((self.folder / "sources" / f"{done['source_id']}.meta.json").is_file())
        with self.assertRaisesRegex(ValueError, "unknown bridge tool"):
            bridge.plan(self.folder, "nope.tool", {}, purpose="p", stage="screen")

    def test_backlog_touch_and_freshness(self):
        record, existed = backlog.touch(self.cfg, {"name": "Shopify Migration", "theme": "t"}, "demand-test", root=self.root)
        self.assertFalse(existed)
        record, existed = backlog.touch(self.cfg, {"name": "Shopify Migration", "theme": "t"}, "demand-test", status="measured",
                                        numbers={"volume_sum": 500, "volume_recent": 480, "trend_yoy": -0.05,
                                                 "cpc_median": 4.0, "provider": "test"}, root=self.root)
        self.assertFalse(existed, "a repeat within the same run is not a refresh")
        record, existed = backlog.touch(self.cfg, {"name": "Shopify Migration", "theme": "t"}, "demand-later", root=self.root)
        self.assertTrue(existed)
        self.assertEqual(backlog.fresh_numbers(self.cfg, record["key"], root=self.root)["volume_sum"], 500)
        self.assertIsNone(backlog.fresh_numbers(self.cfg, "missing", root=self.root))

    def test_report_builds_cards_and_finalize_requires_narrative(self):
        cluster_row = {"cluster_id": "cl-shopify-migration", "theme": "t", "name": "Shopify migration", "job": "перенести магазин",
                       "product_shape": "tool", "intent": "commercial", "head_phrase": "shopify migration", "phrases": ["shopify migration", "shopify migration tool"],
                       "key": "shopify-migration", "backlog_status": "new",
                       "signals": {"money": {"upwork": {"status": "collected", "jobs": 4, "median_budget": 450}, "freelancer": {"status": "collected", "jobs": 1, "max_budget": 200}},
                                   "software": {"status": "collected", "listings": 5, "paid_with_users": 2, "gap": False, "source_ids": ["src-a"]},
                                   "reachable": {"status": "collected", "brands_top5_max": 1, "forums_present": True, "phrases": [{"phrase": "shopify migration", "top": [{"domain": "x.com", "kind": "brand"}]}], "source_ids": ["src-b"]},
                                   "hn": {"hits": 3}},
                       "screen": {"status": "passed", "checks": {"money": True, "reachable": True, "software": True}, "failed": []},
                       "numbers": {"status": "collected", "provider": "test", "volume_sum": 900, "volume_head": 500, "cpc_median": 5.0, "cpc_max": 9.0, "trend_slope": 0.1, "top_phrases": [{"phrase": "shopify migration", "volume": 500}], "source_ids": ["src-c"]},
                       "gate": {"status": "passed", "passed": True, "failed_rules": [], "unknown": [], "mode": "self_serve"}}
        for stage, name in (("gate", "gated.jsonl"),):
            write_stage_jsonl(self.folder, stage, name, [cluster_row])
        reviewed = dict(cluster_row, review={"status": "done", "errors": {}, "worker_ids": {}, "usage": {}},
                        interpret={"job": "Перенос каталога", "buyer": "владелец магазина", "trigger": "смена платформы", "product_shape": "tool",
                                   "payment_unit": "за прогон", "price_point": {"low": 19, "high": 49, "unit": "USD", "basis": "CPC"}, "incumbents": [{"name": "LitExtension"}],
                                   "cheapest_test": "страница", "claims": []},
                        skeptic={"findings": [{"severity": "material", "about": "volume", "text": "часть объёма брендовая"}], "claims": []},
                        judge={"decision_context": "investigate", "scorecard": {"demand": {"value": 3}}, "rationale": "Спрос есть, оплата не подтверждена.", "unresolved_objections": []})
        write_stage_jsonl(self.folder, "review", "reviewed.jsonl", [reviewed])
        finish_stage(self.folder, "review", {"selected": 1, "reviewed": 1})
        path = report.build(self.folder, self.cfg)
        text = path.read_text(encoding="utf-8")
        self.assertIn("### Shopify migration", text)
        self.assertIn("Upwork: 4 заказов", text)
        self.assertIn(report.PLACEHOLDER, text)
        with self.assertRaisesRegex(ValueError, "placeholders|decisions missing"):
            report.finalize(self.folder)
        from scripts.pipeline.review import decide, decide_auto
        with self.assertRaisesRegex(ValueError, "build_test requires"):
            decide(self.folder, self.cfg, "cl-shopify-migration", "build_test", "x", root=self.root)
        auto = decide_auto(self.folder, self.cfg, root=self.root)
        self.assertEqual(auto["cl-shopify-migration"]["status"], "investigate")
        # нарратив вручную: финализация проверяет PDF и его визуальную квитанцию
        text = text.replace(report.PLACEHOLDER + ": одним абзацем — есть ли кандидат для проверочной страницы, что показали цифры и деньги, куда направить следующий шаг.", "Итог: синтетическая проверка отчёта.")
        text = text.replace(report.PLACEHOLDER + ": что осталось неизвестным, какие кластеры отложены и почему, какие проверки стоят ноль денег.", "Дальше: ничего, это тест.")
        path.write_text(text, encoding="utf-8")
        report.render(self.folder)
        from scripts.report_pdf import record_pdf_review
        record_pdf_review(self.folder, "test", "Синтетическая квитанция для проверки контракта.")
        summary = report.finalize(self.folder)
        self.assertEqual(summary["status"], "complete")
        data = collect(self.root)
        self.assertEqual(data["runs"][0]["run_id"], "demand-test")
        self.assertEqual(data["runs"][0]["clusters"][0]["judge"]["decision_context"], "investigate")


if __name__ == "__main__":
    unittest.main()


class MarketplaceParsers(unittest.TestCase):
    def test_apify_paid_from_current_pricing_info(self):
        from unittest import mock
        from scripts.pipeline.connectors import marketplaces
        body = {"data": {"total": 2, "items": [
            {"title": "Paid Scraper", "name": "paid", "username": "u", "stats": {"totalUsers30Days": 17, "lastRunStartedAt": "2026-09-16T04:33:11Z"},
             "currentPricingInfo": {"pricingModel": "PAY_PER_EVENT"}},
            {"title": "Free Scraper", "name": "free", "username": "u", "stats": {"totalUsers30Days": 3},
             "currentPricingInfo": {"pricingModel": "FREE"}},
            {"title": "No Info", "name": "none", "username": "u", "stats": {}}]}}
        record = {"source_id": "src-x", "retrieval_status": "collected"}
        with mock.patch.object(marketplaces, "get_json", return_value=(record, body)):
            got = marketplaces.apify("run", "shopify")
        paid = {l["name"]: l["paid"] for l in got["listings"]}
        self.assertEqual(paid, {"Paid Scraper": True, "Free Scraper": False, "No Info": None})
        self.assertEqual(got["listings"][0]["extra"]["pricing_model"], "PAY_PER_EVENT")


class QuotaLedger(unittest.TestCase):
    def test_bridge_quota_error_defers_request_and_marks_day_exhausted(self):
        from scripts.pipeline import quota
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "backlog").mkdir()
            folder = root / "runs" / "r1"
            (folder / "sources").mkdir(parents=True)
            request = bridge.plan(folder, "ubersuggest.keyword_suggestions", {"keywords": ["x"], "language": "en"}, purpose="цифры кластера cl-x", stage="measure")
            (folder / request["response_path"]).write_text(json.dumps({"error": "HTTP 403: You have reached the daily reports limit: 3"}), encoding="utf-8")
            result = bridge.ingest(folder, root=root)
            self.assertEqual(result["quota_exhausted"], [request["id"]])
            self.assertEqual(bridge.load_requests(folder)[0]["status"], "deferred_quota")
            self.assertEqual(quota.left(root)[0], 0)
            self.assertFalse((folder / request["response_path"]).exists())


class GoogleAdsOAuthHelper(unittest.TestCase):
    def test_server_options_match_the_client_type(self):
        from scripts.google_ads_oauth import server_options
        self.assertEqual(server_options({"installed": {"client_id": "x"}}),
                         ({"host": "localhost", "port": 0, "redirect_uri_trailing_slash": True}, "desktop"))
        self.assertEqual(server_options({"web": {"redirect_uris": ["https://prefero.me/cb", "http://localhost:8080/"]}}),
                         ({"host": "localhost", "port": 8080, "redirect_uri_trailing_slash": True}, "web"))
        self.assertEqual(server_options({"web": {"redirect_uris": ["http://127.0.0.1:9000"]}}),
                         ({"host": "127.0.0.1", "port": 9000, "redirect_uri_trailing_slash": False}, "web"))
        self.assertEqual(server_options({"web": {"redirect_uris": ["https://prefero.me/cb"]}}), (None, "web"))


class TrendSeries(unittest.TestCase):
    def test_cluster_series_sums_periods_across_phrases(self):
        rows = [{"phrase": "a", "monthly": [{"period": "202601", "volume": 100}, {"period": "202602", "volume": 120}]},
                {"phrase": "b", "monthly": [{"period": "202601", "volume": 10}, {"period": "202602", "volume": 30}]},
                {"phrase": "c"}]
        self.assertEqual(measure.cluster_monthly(rows),
                         [{"period": "202601", "volume": 110}, {"period": "202602", "volume": 150}])
        self.assertIsNone(measure.cluster_monthly([{"phrase": "a"}]))

    def test_aggregate_trends_on_the_cluster_not_the_head_phrase(self):
        # head-фраза шумит (10 → 0), кластер в целом растёт
        rows = [{"phrase": "head", "volume": 10, "cpc": 3.0,
                 "monthly": [{"period": f"2026{m:02d}", "volume": v} for m, v in enumerate([10, 0, 10, 0, 0, 0], start=1)]},
                {"phrase": "big", "volume": 900, "cpc": 4.0,
                 "monthly": [{"period": f"2026{m:02d}", "volume": v} for m, v in enumerate([600, 700, 800, 900, 1000, 1100], start=1)]}]
        numbers = measure.aggregate(rows, "head", "google_ads", ["src-1"])
        self.assertEqual(numbers["monthly"][0], {"period": "202601", "volume": 610})
        self.assertGreater(numbers["trend_slope"], 0)

    def test_year_over_year_ignores_the_season_that_slope_mistakes_for_decline(self):
        """Один и тот же сезонный ряд без роста: наклон видит падение, год к году — ровно ноль."""
        season = [100, 95, 70, 50, 55, 60, 85, 80, 75, 70, 72, 65]   # год, начинающийся с сезонного пика
        self.assertLess(measure.trend_slope(season), 0)              # наклон: «спрос падает»
        self.assertEqual(measure.trend_yoy(season * 2), 0.0)         # год к году: ничего не изменилось
        self.assertAlmostEqual(measure.trend_yoy(season + [v // 2 for v in season]), -0.5, places=1)

    def test_year_over_year_needs_two_years(self):
        self.assertIsNone(measure.trend_yoy([{"volume": 10}] * 23))
        self.assertIsNone(measure.trend_yoy([{"volume": 10}] * 24, min_mean_volume=20))

    def test_recent_volume_is_the_last_year_not_the_whole_window(self):
        """Окно в 24 месяца занижает растущую нишу: судить надо по последнему году."""
        series = [{"period": f"2025{m:02d}", "volume": 100} for m in range(1, 13)]
        series += [{"period": f"2026{m:02d}", "volume": 300} for m in range(1, 13)]
        self.assertEqual(measure.volume_recent(series), 300)
        self.assertIsNone(measure.volume_recent(series[:6]))

    def test_cpc_stays_between_the_bid_bounds(self):
        rows = [{"phrase": "a", "volume": 100, "cpc_low": 2.0, "cpc_high": 10.0, "cpc": 6.0, "competition": 0.5}]
        numbers = measure.aggregate(rows, "a", "google_ads", ["src-1"])
        self.assertEqual((numbers["cpc_low_median"], numbers["cpc_median"], numbers["cpc_high_median"]), (2.0, 6.0, 10.0))

    def test_small_series_reports_unknown_trend_instead_of_noise(self):
        noisy = [{"period": f"2026{m:02d}", "volume": v} for m, v in enumerate([20, 20, 30, 10, 0, 0], start=1)]
        self.assertIsNotNone(measure.trend_slope(noisy))
        self.assertIsNone(measure.trend_slope(noisy, min_mean_volume=20))


class StaleDecisions(unittest.TestCase):
    def test_rerunning_review_drops_decisions_for_clusters_it_no_longer_covers(self):
        from scripts.pipeline import review as review_module
        from scripts.pipeline.run import load_manifest, save_manifest
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "runs" / "r1"
            folder.mkdir(parents=True)
            save_manifest(folder, {"run_id": "r1", "decisions": {"cl-a": {"status": "investigate"}, "cl-b": {"status": "dropped"}}})
            dropped = review_module.drop_stale_decisions(folder, {"cl-b"})
            self.assertEqual(dropped, ["cl-a"])
            self.assertEqual(set(load_manifest(folder)["decisions"]), {"cl-b"})
            self.assertEqual(review_module.drop_stale_decisions(folder, {"cl-b"}), [])


class TruncatedModelReply(unittest.TestCase):
    def test_truncated_json_is_reported_as_truncation_not_bad_format(self):
        from unittest import mock
        from scripts.pipeline import llmjson
        truncated = '```json\n{"clusters": [{"name": "a", "phrases": ["x"'
        answer = {"message": {"content": truncated}, "usage": {"completion_tokens": 8000}, "finish_reason": "length"}
        with mock.patch.object(llmjson, "complete", return_value=answer), \
             mock.patch.object(llmjson.sources, "archive", return_value={"source_id": "src-1"}):
            with self.assertRaises(llmjson.TruncatedReply):
                llmjson.ask_json("run", "cluster", [{"role": "user", "content": "x"}],
                                 model_choice={"provider": "deepseek", "model": "m"})

    def test_cluster_splits_the_batch_when_the_reply_is_truncated(self):
        from unittest import mock
        from scripts.pipeline import cluster as cluster_module
        phrases = [f"phrase {i}" for i in range(8)]
        calls = []

        def fake_ask(run_id, role, messages, **kwargs):
            chunk = json.loads(messages[-1]["content"].split("\n", 1)[1])
            calls.append(len(chunk))
            if len(chunk) > 4:
                raise cluster_module.TruncatedReply("too long")
            return ({"clusters": [{"name": f"job {len(calls)}", "job": "j", "product_shape": "tool",
                                   "intent": "commercial", "head_phrase": chunk[0], "phrases": chunk}]},
                    f"src-{len(calls)}", {"total_tokens": 1})

        config = {"cluster": {"phrases_per_call": 8, "min_phrases_per_call": 2}, "models": {"cluster": {"provider": "p", "model": "m"}}}
        with mock.patch.object(cluster_module, "ask_json", side_effect=fake_ask), \
             mock.patch.object(cluster_module, "_merge", side_effect=lambda r, c, *a: (c, "src-merge")):
            clusters, sources_used, _ = cluster_module.cluster_phrases("run", phrases, config, root=".")
        self.assertEqual(calls, [8, 4, 4])
        self.assertEqual(sum(len(c["phrases"]) for c in clusters), 8)


class ScreenAsksUpworkOnlyWhenItMatters(unittest.TestCase):
    CONFIG = {"screen": {"require": ["money", "reachable"], "upwork": {"min_jobs": 3, "min_budget": 200},
                         "serp": {"max_brands_top5": 2}}}

    def signals(self, *, upwork, freelancer_jobs, brands, software_paid=1):
        return {"money": {"upwork": upwork,
                          "freelancer": {"status": "collected", "jobs": freelancer_jobs, "max_budget": 900}},
                "software": {"status": "collected", "paid_with_users": software_paid, "gap": False},
                "reachable": {"status": "collected", "brands_top5_max": brands}}

    def test_freelancer_alone_satisfies_money_without_waiting_for_the_bridge(self):
        verdict = screen.evaluate(self.signals(upwork={"status": "not_requested"}, freelancer_jobs=5, brands=1), self.CONFIG)
        self.assertTrue(verdict["checks"]["money"])
        self.assertEqual(verdict["status"], "passed")

    def test_a_failed_required_check_decides_even_while_money_is_unknown(self):
        verdict = screen.evaluate(self.signals(upwork={"status": "pending_bridge"}, freelancer_jobs=0, brands=5), self.CONFIG)
        self.assertEqual(verdict["status"], "failed")
        self.assertEqual(verdict["failed"], ["reachable"])

    def test_money_stays_unknown_when_only_the_bridge_could_answer(self):
        verdict = screen.evaluate(self.signals(upwork={"status": "not_requested"}, freelancer_jobs=0, brands=1), self.CONFIG)
        self.assertIsNone(verdict["checks"]["money"])
        self.assertEqual(verdict["status"], "pending")


class BridgeCancelsUnneededRequests(unittest.TestCase):
    def test_requests_no_cluster_needs_anymore_stop_being_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "runs" / "r1"
            keep = bridge.plan(folder, "upwork.find_jobs", {"action": "search", "params": {"title": "keep"}},
                               purpose="p", stage="screen")
            drop = bridge.plan(folder, "upwork.find_jobs", {"action": "search", "params": {"title": "drop"}},
                               purpose="p", stage="screen")
            other = bridge.plan(folder, "ubersuggest.keyword_suggestions", {"keywords": ["x"]}, purpose="p", stage="measure")
            cancelled = bridge.cancel_unneeded(folder, "screen", {keep["id"]})
            self.assertEqual(cancelled, [drop["id"]])
            by_id = {r["id"]: r["status"] for r in bridge.load_requests(folder)}
            self.assertEqual(by_id[keep["id"]], "pending")
            self.assertEqual(by_id[drop["id"]], "not_needed")
            self.assertEqual(by_id[other["id"]], "pending")
            self.assertEqual(bridge.cancel_unneeded(folder, "screen", {keep["id"]}), [])


class VolumeConcentration(unittest.TestCase):
    def test_top_share_exposes_a_cluster_carried_by_one_phrase(self):
        rows = [{"phrase": "agent in ai", "volume": 49000, "cpc": 3.0},
                {"phrase": "agent tracing", "volume": 260, "cpc": 4.0},
                {"phrase": "agent tracing tool", "volume": 0, "cpc": None}]
        numbers = measure.aggregate(rows, "agent tracing", "google_ads", ["src-1"])
        self.assertEqual(numbers["volume_top_share"], 0.995)
        even = [{"phrase": "a", "volume": 100}, {"phrase": "b", "volume": 100}]
        self.assertEqual(measure.aggregate(even, "a", "google_ads", [])["volume_top_share"], 0.5)
        self.assertIsNone(measure.aggregate([{"phrase": "a", "volume": 0}], "a", "google_ads", [])["volume_top_share"])
