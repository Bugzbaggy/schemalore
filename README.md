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
/schemalore              # document objects changed on this branch vs dev
/schemalore core         # document every object in the 'core' schema
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
   isn't inferable ("what is the business purpose of `ThresholdAmount`?").
4. **Generate** — writes the artifacts and summarises what it created, updated,
   and logged as gaps.
5. **Re-arm the gate** — runs `bash .githooks/sql-doc-gate.sh --mark-done` so
   the next commit goes through.

## The pre-commit gate

The skill pairs with a gate that blocks a commit while the branch's `.sql`
changes (against `dev`, including staged and working-tree edits) have not been
documented. `/schemalore` records a fingerprint of those changes as its last
step; any later `.sql` change re-arms the gate. Install it from the root of
your database project, where `/path/to/schemalore` is a clone of this
repository:

```bash
mkdir -p .githooks
cp /path/to/schemalore/hooks/sql-doc-gate.sh .githooks/
printf '#!/usr/bin/env bash\nexec bash .githooks/sql-doc-gate.sh\n' > .githooks/pre-commit
chmod +x .githooks/pre-commit
git config core.hooksPath .githooks
```

Git runs `.githooks/pre-commit`; the skill's last step calls
`.githooks/sql-doc-gate.sh` by that path, so keep both names. The gate does not
run on `dev` itself, on a detached HEAD, or during a merge, and it ignores
`.sql` files under `tests/` and `local-test/`. If the docs are already accurate,
mark the changes yourself with `bash .githooks/sql-doc-gate.sh --mark-done`; to
bypass once (discouraged), use `git commit --no-verify`.

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

`SKILL.md` refers to two SSDT project folders and a base branch (`dev`). Change
those to match your layout — they're near the top of the file under **Scope**,
and the folder names recur in the `"project"` fields of the JSON templates. The
hooks hard-code the same values: `BASE_BRANCH` in `hooks/sql-doc-gate.sh`,
`PROJECT_ROOTS` in `hooks/gen_doc_skeleton.py`, and the folder names in
`hooks/validate_sp_header.py`.

The skill also expects a `CLAUDE.md` at the project root: it follows the
conventions there (no brackets around object names, tabs for indentation) and
adds its **Schema Documentation** index before the `## Author Guidelines`
section.

## License

[MIT](LICENSE)
