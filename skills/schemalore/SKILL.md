---
name: schemalore
description: Document SQL Server database objects in an SSDT project. Writes per-schema docs (Markdown and JSON) and completes procedure, function and view headers, asking only what the code can't tell. Run it when a branch's SQL changes are done, or when the doc gate blocks a commit.
argument-hint: "schema-name | . | (blank for branch changes)"
---

# schemalore

Documentation for an SSDT database project, written from the objects themselves. Most of what a
reader needs is already in the code: names, types, keys, parameters, grants. Your job is the rest:
what a schema is for, what a status value means, why a column exists. Find those gaps, close the
ones a query can close, ask a person only what neither the code nor the data can tell you, and write
it all down where the next reader (person or model) will look.

The project keeps its objects in two SSDT folders, `AppDb_MSG/` and `AppDb_MSG_data/`, one
subfolder per schema. Its conventions are in `CLAUDE.md` at the repository root; follow them in
everything you write.

## 1. Settle the scope

| Argument | What you document |
|---|---|
| none | the `.sql` files this branch changes. If `.githooks/sql-doc-gate.sh` exists, its `--files` output is the list (the same files the commit gate checks); otherwise `git diff --name-only $(git merge-base origin/<base> HEAD) -- '*.sql'`, where `<base>` is the branch PRs go into |
| a schema name | every object under `AppDb_MSG/<schema>/` and `AppDb_MSG_data/<schema>/` |
| `.` | every schema in both folders |

Then ask one question and wait for the answer:

```
Document <scope>? (y / n / another scope)
  <what that covers: "4 files changed on this branch: 3 in sales, 1 in billing", "all of sales: 41 objects">
```

On `n`, stop. On another scope, settle that one and ask again. The question is there because a hook
or a habit may start this skill when nobody wants a few hundred files of new prose. When the person
typed an explicit scope themselves, say "Documenting <scope>." and go on without waiting.

## 2. Read the objects

Read every file in scope. Note for each one its schema, its kind (table, procedure, view, function,
user-defined type, trigger, other) and what the code shows outright: columns and types, keys,
parameters and defaults, grants, the header if it has one. When the scope is a whole schema and
`hooks/gen_doc_skeleton.py` is available, running it on the schema first gives you that inventory
as files with `TODO` markers to fill.

## 3. Find the gaps

An object is fully documented when all of these hold. List every one that doesn't, per object:

- **Header** (procedures, functions, views): the standard block below, with every field filled.
  The Usage line is a call someone could run, with realistic values.
- **Grant** (procedures): a `GRANT EXECUTE` to the roles that call it.
- **Comments** (views): a short comment on each non-obvious join or filter.
- **Schema docs:** `docs/schemas/<schema>/overview.md`, plus `tables.md`, `procedures.md`,
  `views.md` and `functions.md` for each kind the schema has, each with its `.json` twin, and each
  saying what the object is today.

```sql
-- =============================================
-- Author: <full name>
-- Created date: <YYYY-MM-DD>
-- Description: <one line: what it does>
-- Usage: EXEC <schema>.<Name> @Param1 = <value>, @Param2 = <value>
-- =============================================
```

`hooks/add_header.py` adds the block with the Author and Created date taken from the commit that
added the file, and leaves Description and Usage as `TODO` for you.

Skip what the code already says. `CreatedDate`, `ModifiedAt`, `Id` and `Uid` need no description;
an object whose docs are complete and current needs nothing.

## 4. Close the gaps

Sort the gaps into two kinds and send both in one message.

**What the data can answer**: which values a status column really holds, how many rows a table
has and how old they are, what a typical row looks like. Write a read-only query for each and ask
the person to run it, on a development copy where one is representative, and to say which database
they used. Every query MUST:

- be a `SELECT` and nothing else: NEVER `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `TRUNCATE` or any
  DDL, even commented out;
- return a bounded result: an aggregate, a `GROUP BY` ordered by count, or `TOP (n)`. NEVER an
  unbounded `SELECT *` from a table;
- name objects as `schema.object`, without brackets, as `CLAUDE.md` asks;
- get its facts about the schema from the `.sql` files, not from system catalog queries.

Number them `Q1`, `Q2` and so on.

**What only a person can answer**: purpose, ownership, business meaning, why something is the way
it is. Ask only what is genuinely unclear from the code and not already covered by a query, number
the questions, group them by object in schema order, and point at the query that answers part of a
question ("Q2 shows the values; what does 3 mean?").

```
I found gaps in 5 objects.

Queries (read-only; please run them and paste the results, and say which database):
  Q1  sales.Invoice.PaymentState: the values in use
      SELECT PaymentState, COUNT(*) AS Rows FROM sales.Invoice GROUP BY PaymentState ORDER BY Rows DESC;
  Q2  sales.Invoice: size and age
      SELECT COUNT(*) AS Rows, MIN(CreatedDate) AS Oldest, MAX(CreatedDate) AS Newest FROM sales.Invoice;

Questions:
  1. sales.Invoice.GraceDays: what starts the count, and what happens when it runs out?
  2. sales.Invoice_Reminder_Send: what values does a real caller pass?
  3. billing: in one line, what is this schema responsible for?

Answer what you can; "skip" writes the docs with what we have.
```

Whatever stays unanswered, including queries nobody ran, goes into `docs/schemas/<schema>/gaps.md`:
the object, the question or query, and today's date, so the next run can pick it up.

## 5. Write

**Schema docs**, in `docs/schemas/<schema>/`. Every file comes as Markdown for people and a JSON
twin with the same base name for tools; the JSON MUST parse. Write only the kinds the schema has
(no empty `functions.md`). The layout is what `hooks/gen_doc_skeleton.py` writes:

| File | Frontmatter | Body |
|---|---|---|
| `overview` | `schema`, `full_name`, `last_updated`, `object_count` (tables, procedures, views, functions) | Purpose, Data Flow (the write path and the read path), Key Tables, Dependencies (other schemas), Access Roles |
| `tables` | `schema`, `type: tables`, `count`, `last_updated` | one section per table: its purpose; a column table (Column, Type, Nullable, Default, Description); primary key; foreign keys; enum meanings |
| `procedures` | the same, `type: procedures` | a contents list grouped as Runtime, Jobs (`job_` prefix) and Operations / Admin (`_Ops`, `_ByOps` or `_Admin` suffix, or granted only to an ops role); per procedure the description, a Usage example, a parameter table (Parameter, Type, Default, Description) and **Granted to** |
| `views`, `functions` | the same, with their `type` | per object its purpose, plus source tables for a view and parameters for a function |

In JSON the keys are camelCase (`fullName`, `lastUpdated`, `primaryKey`, `foreignKeys`,
`grantedTo`), each table and procedure records its `project` (`AppDb_MSG` or `AppDb_MSG_data`), a
procedure records its `category` (`runtime`, `job` or `ops`), enum meanings go in `enums` keyed by
column, and a self-explanatory column's `description` is `null`.

When a file already exists, merge into it: keep what is right, correct what is stale, add what is
missing. Keep it short; this is reference material, and much of its readership is a model working on
the code. Where you could only infer something from the code, say so: `<!-- REVIEW: inferred from
code, not confirmed -->` in Markdown, `"_review": true` in JSON.

**The index in `CLAUDE.md`.** `CLAUDE.md` MUST list every documented schema. Its
`## Schema Documentation` section says where the docs live and what each file holds, and ends with
a `### Documented Schemas` list with one link per schema,
`- [<schema> — <full name>](docs/schemas/<schema>/overview.md)`. Add a schema the first time you
document it, never twice. If the section doesn't exist yet, create it just before
`## Author Guidelines`.

**Headers.** Complete the header of every procedure, function and view in scope that lacks one or
has empty fields. Never change a field that is already right. Take Author and Created date from the
commit that added the file (`git log --diff-filter=A --format='%an %as' -- <file>`) when they are
missing, and ask when that commit says nothing useful.

## 6. Report

List what changed, in four groups: docs created or updated (file, and what changed in it), the
`CLAUDE.md` index, headers completed (object, and which fields), and what went to `gaps.md`.

## 7. Mark the docs done

Last of all, and every time:

```bash
bash .githooks/sql-doc-gate.sh --mark-done
```

The commit gate records the branch's current `.sql` changes as documented and lets the next commit
through. Any later `.sql` change blocks again until this skill runs again (or someone marks the docs
current by hand, which is for when they really are).

## Rules

- Follow `CLAUDE.md`: object names without brackets, tabs for indentation, and its other
  conventions.
- NEVER write "Claude" as an author, in a header or anywhere else; the author is a person.
- The queries are for the person to run, against the database they choose. NEVER write one that
  changes data or schema.
- Don't document what is already fully and correctly documented.
- Every unanswered question ends up in `gaps.md`, dated; none is dropped.
