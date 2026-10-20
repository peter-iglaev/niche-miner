"""Offline checks for the public/private split. Synthetic repositories only."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scripts import workspace

MANIFEST = "config/founder-profile.yaml\nruns/\nbacklog/\ntasks/*-20[0-9][0-9]-*\n.privacy-denylist\n"


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for name in [n for n in os.environ if workspace.SECRET_NAME.search(n) or n.startswith("NICHE_MINER_")]:
            del os.environ[name]
        os.environ.update(GIT_COMMITTER_NAME="Dev", GIT_COMMITTER_EMAIL="dev@example.com")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.code, self.data = base / "code", base / "data"
        (self.code / "scripts").mkdir(parents=True)
        (self.code / ".private-paths").write_text(MANIFEST, encoding="utf-8")
        (self.code / "scripts" / "app.py").write_text("print('app')\n", encoding="utf-8")
        (self.data / "config").mkdir(parents=True)
        (self.data / "config" / "founder-profile.yaml").write_text("name: Jane Roe\n", encoding="utf-8")
        (self.data / "runs" / "old-run").mkdir(parents=True)
        (self.data / "tasks").mkdir()
        (self.data / "tasks" / "mining-2026-09-11.md").write_text("brief\n", encoding="utf-8")
        (self.data / ".privacy-denylist").write_text("# owner\nJane Roe\nre:jroe[0-9]+\n", encoding="utf-8")

    def init_git(self, root, public):
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "user.email", "dev@example.com")
        git(root, "config", "user.name", "Dev")
        if public:
            (root / ".gitignore").write_text("".join("/" + p + "\n" for p, _ in workspace.entries(root)))

    def test_link_symlinks_data_and_creates_missing_directories(self):
        result = workspace.link(self.code, self.data)
        states = {r["path"]: r["state"] for r in result["paths"]}
        self.assertEqual(states["runs/"], "created")
        self.assertEqual(states["backlog/"], "created")
        self.assertEqual(states["config/founder-profile.yaml"], "created")
        self.assertEqual(states["tasks/mining-2026-09-11.md"], "created")
        self.assertTrue((self.data / "backlog").is_dir())
        self.assertTrue((self.code / "runs").is_symlink())
        self.assertTrue((self.code / "runs" / "old-run").is_dir())
        (self.code / "runs" / "new-run").mkdir()
        self.assertTrue((self.data / "runs" / "new-run").is_dir())
        again = workspace.link(self.code, self.data)
        self.assertEqual({r["state"] for r in again["paths"]}, {"linked"})

    def test_link_never_overwrites_local_data(self):
        (self.code / "runs" / "local-run").mkdir(parents=True)
        result = workspace.link(self.code, self.data)
        self.assertEqual(result["conflicts"], ["runs/"])
        self.assertFalse((self.code / "runs").is_symlink())
        self.assertTrue((self.code / "runs" / "local-run").is_dir())

    def test_link_refuses_the_repository_itself(self):
        with self.assertRaisesRegex(ValueError, "itself"):
            workspace.link(self.code, self.code)

    def test_public_repository_rejects_private_paths_and_identifiers(self):
        self.init_git(self.code, public=True)
        workspace.link(self.code, self.data)
        os.environ["NICHE_MINER_DATA"] = str(self.data)
        (self.code / "scripts" / "notes.py").write_text("AUTHOR = 'jane roe'\nID = 'jroe42'\n", encoding="utf-8")
        git(self.code, "add", "-A")
        git(self.code, "add", "-f", "backlog")  # a forced add of a private path
        (self.data / "backlog" / "clusters.jsonl").write_text("{}\n", encoding="utf-8")
        git(self.code, "add", "-f", "config/founder-profile.yaml")
        report = workspace.check(self.code, staged=True)
        self.assertTrue(report["public_mode"])
        rules = {(e["file"], e.get("line"), e["rule"]) for e in report["errors"]}
        self.assertIn(("scripts/notes.py", 1, ".privacy-denylist entry 1"), rules)
        self.assertIn(("scripts/notes.py", 2, ".privacy-denylist entry 2"), rules)
        self.assertIn(("config/founder-profile.yaml", None, "private path listed in .private-paths"), rules)
        self.assertNotIn("Jane", json.dumps(report))

    def test_a_noreply_address_carrying_a_denylisted_login_is_accepted(self):
        self.init_git(self.code, public=True)
        (self.code / ".privacy-denylist").write_text("jroe\n", encoding="utf-8")
        git(self.code, "add", "-A")
        git(self.code, "config", "user.email", "jroe@example.com")
        self.assertIn("git config user.email", [e["file"] for e in workspace.check(self.code, staged=True)["errors"]])
        git(self.code, "config", "user.email", "42+jroe@users.noreply.github.com")
        self.assertTrue(workspace.check(self.code, staged=True)["valid"])

    def test_secrets_are_found_by_format_and_by_environment_value(self):
        self.init_git(self.code, public=True)
        os.environ["SOME_SERVICE_API_KEY"] = "custom-secret-value-123"
        (self.code / "scripts" / "cfg.py").write_text(
            "A = 'custom-secret-value-123'\n"
            "B = '1234567890:AA" + "x" * 33 + "'\n"
            "C = 'fixture 1234567890:AA" + "y" * 33 + "'  # privacy-check: allow\n", encoding="utf-8")
        git(self.code, "add", "-A")
        report = workspace.check(self.code, staged=True)
        rules = {(e["line"], e["rule"]) for e in report["errors"]}
        self.assertEqual(rules, {(1, "value of $SOME_SERVICE_API_KEY"), (2, "telegram bot token")})
        self.assertNotIn("custom-secret-value-123", json.dumps(report))

    def test_all_in_one_repository_skips_its_tracked_private_data(self):
        self.init_git(self.code, public=False)
        (self.code / "runs" / "r").mkdir(parents=True)
        (self.code / "runs" / "r" / "tg.json").write_text('{"username": "jroe42"}', encoding="utf-8")
        (self.code / ".privacy-denylist").write_text("re:jroe[0-9]+\n", encoding="utf-8")
        git(self.code, "add", "-A")
        report = workspace.check(self.code, staged=True)
        self.assertFalse(report["public_mode"])
        self.assertTrue(report["valid"], report["errors"])
        self.assertEqual(report["skipped_private"], 2)

    def test_export_builds_a_clean_repository_without_private_paths(self):
        self.init_git(self.code, public=False)
        (self.code / "runs" / "r").mkdir(parents=True)
        (self.code / "runs" / "r" / "manifest.json").write_text("{}", encoding="utf-8")
        (self.code / "config").mkdir()
        (self.code / "config" / "founder-profile.yaml").write_text("name: Jane Roe\n", encoding="utf-8")
        (self.code / "config" / "demand.yaml").write_text("mode: self_serve\n", encoding="utf-8")
        (self.code / ".privacy-denylist").write_text("Jane Roe\n", encoding="utf-8")
        git(self.code, "add", "-A")
        git(self.code, "commit", "-q", "-m", "all in one")
        out = Path(self._tmp.name) / "public"
        result = workspace.export(out, root=self.code, commit=True, author="Dev <dev@example.com>")
        self.assertTrue(result["check"]["valid"], result["check"]["errors"])
        self.assertTrue(result["committed"])
        files = subprocess.run(["git", "-C", str(out), "ls-files"], capture_output=True, text=True).stdout.split()
        self.assertEqual(sorted(files), [".gitignore", ".private-paths", "config/demand.yaml", "scripts/app.py"])
        self.assertIn("/runs\n", (out / ".gitignore").read_text())
        log = subprocess.run(["git", "-C", str(out), "log", "--format=%H"], capture_output=True, text=True).stdout
        self.assertEqual(len(log.split()), 1)
        self.assertTrue(workspace.public_mode(out))

    def test_export_does_not_commit_when_the_check_fails(self):
        self.init_git(self.code, public=False)
        (self.code / ".privacy-denylist").write_text("Jane Roe\n", encoding="utf-8")
        (self.code / "scripts" / "about.md").write_text("Built by Jane Roe\n", encoding="utf-8")
        git(self.code, "add", "-A")
        git(self.code, "commit", "-q", "-m", "all in one")
        result = workspace.export(Path(self._tmp.name) / "public", root=self.code, commit=True)
        self.assertFalse(result["check"]["valid"])
        self.assertFalse(result["committed"])


if __name__ == "__main__":
    unittest.main()
