# Public code and private data

The application is open; the owner's data is not. The founder profile, runs, the cluster backlog,
the journal, and dashboards live in a separate **private data repository**. The code sees them through ordinary
paths (`runs/`, `backlog/`, `config/founder-profile.yaml`) via symlinks, so no script
knows about the split.

## What counts as private

The list is in [`.private-paths`](../.private-paths). In the public repository these paths are written into
`.gitignore`, `workspace.py export` does not carry them into the public snapshot, and the pre-commit hook rejects
a commit that contains them anyway.

| Path | What is there |
|---|---|
| `config/founder-profile.yaml` | experience, contacts, budget, geography, attitude to sales |
| `runs/` | whole runs: raw source responses, reports, decisions |
| `backlog/` | the cluster backlog and quotas between runs |
| `reports/`, `tasks/*-<date>-*`, `tasks/standing-objective.md` | assignments and reports of specific runs |
| `docs/journal.md`, `docs/demand-dashboard.html`, `docs/pipeline-map.html`, `docs/snapshots/` | the journal and pages assembled from runs |
| `.privacy-denylist` | your identifiers for checking (see below) |

API keys never reach git: they live in environment variables or in `.env.local`.

## Claude Code cloud session: configure once

1. **Environment → Environment variables** (claude.ai/code → Environments → your environment). It already holds
   `DEEPSEEK_API_KEY`, `BRAVE_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Add:
   ```
   NICHE_MINER_DATA=/home/user/<data-repo-name>
   ```
   The cloud session places each selected repository in `/home/user/<name>`. You can check the path
   with `ls /home/user` in the session.
2. **At session start** select both repositories: the public code and the private data.
3. The SessionStart hook (`scripts/install_pkgs.sh`) installs dependencies itself, enables the pre-commit
   check (`git config core.hooksPath scripts/githooks`), and runs `workspace.py link`.

After that everything is as before: `/research <objective>`. The profile comes from the data repository, the keys from the environment.
Run artifacts are physically written to the data repository and committed there:

```
git -C "$NICHE_MINER_DATA" add -A && git -C "$NICHE_MINER_DATA" commit -m "Run <id>" && git -C "$NICHE_MINER_DATA" push
```

Code changes are committed to the public repository as usual; the data never gets there, because
for it those are ignored symlinks.

## Locally

```
git clone <public-repository> niche-miner
git clone <data-repository> niche-miner-data
cd niche-miner
echo "NICHE_MINER_DATA=../niche-miner-data" >> .env.local
git config core.hooksPath scripts/githooks
python3 scripts/workspace.py link
python3 scripts/workspace.py status
```

Without a data repository the application still works: copy
`config/founder-profile.example.yaml` to `config/founder-profile.yaml`, and runs will
land in the local ignored `runs/`.

## `scripts/workspace.py` commands

| Command | What it does |
|---|---|
| `status` | where the data is, which mode the repository is in, what is linked |
| `link [--dry-run]` | symlinks from `NICHE_MINER_DATA`; missing directories are created in the data repo; local data is untouched (`conflict`) |
| `check [--staged]` | private paths, key formats, secret environment variable values, `.privacy-denylist` entries |
| `export --out DIR [--commit --author "Name <id+user@users.noreply.github.com>"]` | a clean public snapshot in one commit |

**`.privacy-denylist`**: one line — one entry (case-insensitive), the `re:` prefix for
a regular expression. This holds the surname, nickname, email, `chat_id`, employers. The file is private:
it lives in the data repository and is linked like the profile. For the cloud, entries can also be passed
via the `NICHE_MINER_DENYLIST` variable, separated by `;`.

The check reports the file, line, and rule, but never the matched value itself. A line with
an obviously dummy value (a test fixture) can be marked with a `privacy-check: allow` comment.

**Modes.** A repository is considered public if its `.gitignore` ignores `runs`. There,
a private path in a commit is an error. In the old "everything in one" repository, private paths
were committed intentionally: `check` skips them and scans only application files.

## First publication

1. `python3 scripts/workspace.py export --out ../niche-miner-public`. The export copies files from git,
   appends `.gitignore`, runs `git init`, and runs `check`.
2. Review the snapshot: `git -C ../niche-miner-public status`, the README, config examples.
3. `export … --commit --author "Name <id+user@users.noreply.github.com>"`. There will be one commit:
   the history of the old repository with runs and the profile does not pass into the public one.
4. Create an empty repository on GitHub, **private** first, push the snapshot there, check
   it once more, and only then switch visibility to Public.

**The data repository.** The old repository is already structured as a data repository: the same paths in the same
places. It can stay private and be referenced in `NICHE_MINER_DATA` as is. The code in it simply
stops being used; it can be removed with a separate commit once the new scheme settles in.

## Telegram

The Bot API response is archived into `runs/<id>/sources/` without the `chat` and `from` objects: the run records
that a message was sent, but not whose account received it. This is noted in the source metadata
(`limitations`), because the archive stores a redacted response, not the original bytes.
Runs made before this change do contain those objects, which is why they stay in the private repository.
