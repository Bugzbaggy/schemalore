# sql-documenter

A [Claude Code](https://claude.com/claude-code) skill that documents SQL Server
objects in an SSDT database project — schema docs, `MS_Description` extended
properties, and stored-procedure headers — plus the pre-commit gate that keeps
them from rotting.

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

```bash
/plugin install sql-documenter@Bugzbaggy
```

## Use

```
/sql-documenter              # document objects changed on this branch vs dev
/sql-documenter cp           # document every object in the 'cp' schema
/sql-documenter .            # document the whole database
```

The default scope is the branch diff, which is the one you want almost always:
it documents what you just changed, while you still remember why.

## What it produces

| Artifact | Where it lands |
|---|---|
| Schema documentation | `docs/schemas/<schema>/*.md` |
| `MS_Description` extended properties | `.sql` scripts alongside the objects |
| Stored-procedure headers | Inserted into the procedure files |
| Gap report | Which objects are still undocumented and why |

## How it works

1. **Confirm intent** — it states the scope it inferred and waits, so you don't
   get 400 files of generated prose you didn't ask for.
2. **Analyse** — reads the objects, infers purpose from names, types, foreign
   keys, and usage.
3. **Ask** — targeted questions only where intent genuinely isn't inferable
   ("is `StatusCode` an enum? what are the values?").
4. **Generate** — writes the artifacts.

## The pre-commit gate

The skill pairs with a gate that fails a commit when a new or modified object
has no documentation:

```bash
cp hooks/sql-doc-gate.sh .githooks/
git config core.hooksPath .githooks
```

| Script | Role |
|---|---|
| `hooks/sql-doc-gate.sh` | Blocks commits containing undocumented objects |
| `hooks/validate_sp_header.py` | Validates procedure header structure |
| `hooks/gen_doc_skeleton.py` | Emits a documentation skeleton to fill in |
| `hooks/add_header.py` | Inserts a standard header into a procedure file |

Generation without a gate produces one good week of documentation. The gate is
what makes it stick.

## Adapting it

`SKILL.md` refers to project paths (`AppDb/`, `AppDb_data/`) and a base branch
(`dev`). Change those to match your layout — they're near the top of the file
under **Scope**.

## License

[MIT](LICENSE)
