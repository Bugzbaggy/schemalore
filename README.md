# schemalore

A [Claude Code](https://claude.com/claude-code) skill that documents SQL Server
objects in an SSDT database project — schema docs and procedure, function, and
view headers — plus the pre-commit gate that keeps them from rotting.

[![Claude Code skill](https://img.shields.io/badge/Claude%20Code-skill-8A63D2)](https://claude.com/claude-code)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## The problem

Database documentation decays faster than any other kind, for a structural
reason: the person who knows why a column exists is writing the migration, and
the documentation lives somewhere else entirely — a wiki, a comment block, an
extended property nobody queries.

This skill closes that gap by generating documentation **from the objects
themselves**, asking you only what it genuinely cannot infer, and then gating
commits so an undocumented object can't land.

## Install

Copy the skill into your database project's `.claude/skills/` folder:

```bash
git clone https://github.com/Bugzbaggy/schemalore.git
mkdir -p /path/to/your-db-project/.claude/skills
cp -r schemalore/skills/schemalore /path/to/your-db-project/.claude/skills/
```

Keep that path: `hooks/validate_sp_header.py` reads the rules from
`.claude/skills/schemalore/SKILL.md`.

## Use

```
/schemalore              # document the objects this branch changes
/schemalore sales        # document every object in the 'sales' schema
/schemalore .            # document the whole database
```

The default scope is the branch diff, which is the one you want almost always:
it documents what you just changed, while you still remember why.

## What it produces

| Artifact | Where it lands |
|---|---|
| Schema documentation (Markdown + JSON) | `docs/schemas/<schema>/*.md` and `*.json` |
| Procedure, function, and view headers | Inserted into the object files |
| Schema-docs index | The **Schema Documentation** section of the project's `CLAUDE.md` |
| Gap report | `docs/schemas/<schema>/gaps.md` — unanswered questions and unrun probe queries |

## How it works

1. **Confirm intent** — it states the scope it inferred and waits, so you don't
   get 400 files of generated prose you didn't ask for. (Given an explicit
   scope, it may just announce it and proceed.)
2. **Analyse** — reads the objects, infers purpose from names, types, foreign
   keys, and usage, and lists the documentation gaps.
3. **Ask** — read-only probe queries for you to run (value distributions, row
   counts, sample rows), plus targeted questions only where intent genuinely
   isn't inferable ("what starts the count for `GraceDays`?").
4. **Generate** — writes the artifacts and summarises what it created, updated,
   and logged as gaps.
5. **Re-arm the gate** — runs `bash .githooks/sql-doc-gate.sh --mark-done` so
   the next commit goes through.

## The pre-commit gate

The skill pairs with a gate that blocks a commit while the branch's `.sql`
changes (against the base branch, including staged and working-tree edits) have
not been documented. `/schemalore` marks those changes as documented as its last
step; any later `.sql` change blocks again. The mark is a hash of the changed
files' content, kept per branch under `.git/sql-doc-gate/`, so a rebase keeps it
and marking one branch leaves the others alone. Install it from the root of your
database project, where `/path/to/schemalore` is a clone of this repository:

```bash
mkdir -p .githooks
cp /path/to/schemalore/hooks/sql-doc-gate.sh .githooks/
printf '#!/usr/bin/env bash\nexec bash .githooks/sql-doc-gate.sh\n' > .githooks/pre-commit
chmod +x .githooks/pre-commit
git config core.hooksPath .githooks
```

Git runs `.githooks/pre-commit`; the skill calls `.githooks/sql-doc-gate.sh` by
that path (`--files` for its default scope, `--mark-done` at the end), so keep
both names. The base branch is the first of `git config schemalore.base`,
`base_branch` in a [sql-ready](https://github.com/Bugzbaggy/sql-ready)
`.sql-ready.conf`, `origin/HEAD`'s branch, and `main`;
`bash .githooks/sql-doc-gate.sh --base` prints the one in use. The gate does not
run on the base branch itself, on a detached HEAD, or during a merge. It ignores
`.sql` files under `tests/` and `local-test/`, the `skip_paths` of a
`.sql-ready.conf`, and anything listed in `git config schemalore.exclude`
(space-separated; a folder ends with `/`). If the docs are already accurate,
mark the changes yourself with `bash .githooks/sql-doc-gate.sh --mark-done`; to
bypass once, use `git commit --no-verify`.

| Script | Role |
|---|---|
| `hooks/sql-doc-gate.sh` | The gate: blocks a commit until the branch's `.sql` changes are marked documented |
| `hooks/validate_sp_header.py` | Asks Claude to check SQL files against the rules in `CLAUDE.md` and `SKILL.md` (headers, `GRANT EXECUTE`, schema docs); only ERROR findings fail it unless `--strict-block` |
| `hooks/gen_doc_skeleton.py` | Writes a documentation skeleton under `docs/schemas/<schema>/` from the SQL files, with TODOs to fill in |
| `hooks/add_header.py` | Inserts or completes the standard header in procedure, function, and view files |

Only `sql-doc-gate.sh` is wired up by the commands above; the three Python
scripts are standalone (Python 3.8+, standard library only).

Generation without a gate produces one good week of documentation. The gate is
what makes it stick.

## Adapting it

`SKILL.md` names two SSDT project folders, `AppDb_MSG/` and `AppDb_MSG_data/`.
Change them to match your layout: they appear in its introduction, in **1. Settle
the scope**, and in the JSON `project` field under **5. Write**. The Python hooks
hard-code the same folders: `PROJECT_ROOTS` in `hooks/gen_doc_skeleton.py` and
the folder names in `hooks/validate_sp_header.py`. The base branch needs no
edit; set `git config schemalore.base <branch>` if `origin/HEAD` isn't it.

The skill also expects a `CLAUDE.md` at the project root: it follows the
conventions there (no brackets around object names, tabs for indentation) and
adds its **Schema Documentation** index before the `## Author Guidelines`
section.

## License

[MIT](LICENSE)
