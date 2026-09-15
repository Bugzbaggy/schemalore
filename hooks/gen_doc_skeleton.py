#!/usr/bin/env python3
"""
sql-documenter doc-skeleton generator (example)

Walks AppDb_MSG/<schema>/ (and AppDb_MSG_data/<schema>/) and emits the file tree
prescribed by .claude/skills/sql-documenter/SKILL.md Phase 4a:

    docs/schemas/<schema>/
    ├── overview.md      / overview.json
    ├── tables.md        / tables.json
    ├── procedures.md    / procedures.json
    ├── views.md         / views.json     (if any)
    └── functions.md     / functions.json (if any)

Deterministic content (parsed from SQL):
    - Object inventory (counts, names)
    - Procedure / function parameter list (name, type, default, required)
    - Table columns (name, type, nullable, default, PK / FK constraints)
    - GRANT EXECUTE role list
    - Per-object header fields (Author / Created date / Description / Usage)
      that ARE present -- if not, marked TODO

LLM-required content is left as `<!-- TODO: <what to fill> -->` in markdown
and `"_todo": "<...>"` in JSON.  Run `/sql-documenter <schema>` after this
script to fill in semantic descriptions, enum value meanings, schema
purpose, etc.

Idempotent: if the docs/<schema>/*.md exists already, this script will
NOT overwrite it -- pass --force to regenerate.

Usage:
    gen_doc_skeleton.py <schema> [--force] [--repo-root <path>]
    gen_doc_skeleton.py --all [--force]            # all schemas under AppDb_MSG/

Requires: Python 3.8+, stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT_ROOTS = ("AppDb_MSG", "AppDb_MSG_data")
EXCLUDE_SCHEMA_NAMES = {
	"bin", "obj", "Properties", "Scripts", "Security", "Storage",
	"Migration", "Archive", "deploy", "external-refs",
}

OBJECT_DIRS = {
	"tables": "Tables",
	"procedures": "Stored Procedures",
	"views": "Views",
	"functions": "Functions",
	"triggers": "Triggers",
	"types": "User Defined Types",
}


# ---------------------------------------------------------------------------
# SQL parsing helpers
# ---------------------------------------------------------------------------

_HEADER_FIELD_RE = re.compile(
	r"^\s*--\s*(?P<key>Author|Created?\s+date|Description|Usage)\s*:\s*(?P<value>.*?)\s*$",
	re.IGNORECASE,
)


def parse_header(text: str) -> Dict[str, str]:
	fields: Dict[str, str] = {}
	for raw in text.splitlines():
		stripped = raw.strip()
		if not stripped:
			continue
		if not stripped.startswith("--"):
			break
		m = _HEADER_FIELD_RE.match(raw)
		if not m:
			continue
		key = re.sub(r"\s+", " ", m.group("key").strip().lower())
		if key == "create date":
			key = "created date"
		fields[key] = m.group("value").strip()
	return fields


_CREATE_PROC_RE = re.compile(
	r"\bCREATE\s+(?:PROCEDURE|PROC)\s+\[?(?P<schema>\w+)\]?\s*\.\s*\[?(?P<name>\w+)\]?"
	r"\s*(?P<paramblock>(?:[^A]|A(?!S))*?)\s+AS\b",
	re.IGNORECASE | re.DOTALL,
)
_CREATE_FUNC_RE = re.compile(
	r"\bCREATE\s+FUNCTION\s+\[?(?P<schema>\w+)\]?\s*\.\s*\[?(?P<name>\w+)\]?"
	r"\s*\((?P<paramblock>.*?)\)\s+RETURNS\b",
	re.IGNORECASE | re.DOTALL,
)
_CREATE_VIEW_RE = re.compile(
	r"\bCREATE\s+VIEW\s+\[?(?P<schema>\w+)\]?\s*\.\s*\[?(?P<name>\w+)\]?",
	re.IGNORECASE,
)
_CREATE_TABLE_RE = re.compile(
	r"\bCREATE\s+TABLE\s+\[?(?P<schema>\w+)\]?\s*\.\s*\[?(?P<name>\w+)\]?"
	r"\s*\((?P<body>.*)$",
	re.IGNORECASE | re.DOTALL,
)
_GRANT_EXEC_RE = re.compile(
	r"GRANT\s+EXECUTE\s+ON\s+(?:OBJECT::)?[\w\[\]\.]+\s+TO\s+\[?(?P<role>\w+)\]?",
	re.IGNORECASE,
)
_PARAM_RE = re.compile(
	r"@(?P<name>\w+)\s+(?P<type>[\w\(\)\d, ]+?)"
	r"(?:\s*=\s*(?P<default>[^,\n]+?))?"
	r"(?:\s*(?:OUTPUT|OUT|READONLY))?"
	r"\s*(?:,|$)",
	re.IGNORECASE,
)


def parse_procedure_or_function(text: str, kind: str) -> Optional[dict]:
	rx = _CREATE_PROC_RE if kind == "procedure" else _CREATE_FUNC_RE
	m = rx.search(text)
	if not m:
		return None
	schema = m.group("schema")
	name = m.group("name")
	paramblock = m.group("paramblock") or ""
	# Strip line comments from the param block before parsing.
	paramblock = re.sub(r"--.*", "", paramblock)
	# For procedures, the param block is everything between the proc name and
	# AS; for functions it's between ( and ) before RETURNS. Both can be empty.
	params = []
	for pm in _PARAM_RE.finditer(paramblock):
		default = pm.group("default")
		if default is not None:
			default = default.strip()
		params.append({
			"name": "@" + pm.group("name"),
			"type": pm.group("type").strip(),
			"default": default,
			"required": default is None,
			"description": None,
			"_todo": "describe this parameter",
		})
	roles = sorted(set(g.group("role") for g in _GRANT_EXEC_RE.finditer(text)))
	header = parse_header(text)
	return {
		"schema": schema,
		"name": name,
		"description": header.get("description") or None,
		"usage": header.get("usage") or None,
		"author": header.get("author") or None,
		"createdDate": header.get("created date") or None,
		"parameters": params,
		"grantedTo": roles,
		"_todo": _proc_func_todos(header, params, roles, kind),
	}


def _proc_func_todos(header: dict, params: list, roles: list, kind: str) -> List[str]:
	todos: List[str] = []
	if not header.get("description"):
		todos.append("write a one-line Description in the header")
	if not header.get("usage"):
		todos.append("add a Usage example with realistic parameter values")
	if not header.get("author"):
		todos.append("set Author full name (use git blame as starting point)")
	if not header.get("created date"):
		todos.append("set Created date (use git log first-commit date)")
	if any(p.get("description") is None for p in params):
		todos.append("describe each parameter")
	if kind == "procedure" and not roles:
		todos.append("add at least one GRANT EXECUTE statement")
	return todos


def parse_view(text: str) -> Optional[dict]:
	m = _CREATE_VIEW_RE.search(text)
	if not m:
		return None
	header = parse_header(text)
	return {
		"schema": m.group("schema"),
		"name": m.group("name"),
		"description": header.get("description") or None,
		"_todo": ["describe view purpose", "list source tables and join semantics"],
	}


_COLUMN_RE = re.compile(
	r"^\s*\[?(?P<name>\w+)\]?\s+(?P<type>[\w\(\)\d, ]+?)"
	r"(?:\s+COLLATE\s+\w+)?"
	r"(?P<nullable>\s+NOT\s+NULL|\s+NULL)?"
	r"(?:\s+IDENTITY\([^)]+\))?"
	r"(?:\s+CONSTRAINT\s+\w+\s+DEFAULT\s*\((?P<default>[^)]+)\))?"
	r"(?:\s+DEFAULT\s*\((?P<default2>[^)]+)\))?"
	r"\s*[,)]?\s*$",
	re.IGNORECASE,
)
_PK_RE = re.compile(
	r"CONSTRAINT\s+\w+\s+PRIMARY\s+KEY[^\(]*\(([^)]+)\)",
	re.IGNORECASE,
)
_FK_RE = re.compile(
	r"CONSTRAINT\s+\w+\s+FOREIGN\s+KEY\s*\(([^)]+)\)\s+REFERENCES\s+([\w\[\]\.]+)\s*\(([^)]+)\)",
	re.IGNORECASE,
)


def parse_table(text: str) -> Optional[dict]:
	m = _CREATE_TABLE_RE.search(text)
	if not m:
		return None
	schema, name = m.group("schema"), m.group("name")
	body = m.group("body")

	# Naive extraction: split by top-level commas (best-effort).
	cols: List[dict] = []
	depth = 0
	buf = ""
	pieces: List[str] = []
	for ch in body:
		if ch == "(":
			depth += 1
		elif ch == ")":
			if depth == 0:
				break
			depth -= 1
		if ch == "," and depth == 0:
			pieces.append(buf)
			buf = ""
			continue
		buf += ch
	if buf.strip():
		pieces.append(buf)

	for piece in pieces:
		piece = piece.strip()
		if not piece:
			continue
		if re.match(r"\s*CONSTRAINT\b", piece, re.IGNORECASE):
			continue
		mm = _COLUMN_RE.match(piece)
		if not mm:
			continue
		nullable = mm.group("nullable") or ""
		default = mm.group("default") or mm.group("default2")
		cols.append({
			"name": mm.group("name"),
			"type": mm.group("type").strip(),
			"nullable": "NOT NULL" not in nullable.upper(),
			"default": default.strip() if default else None,
			"description": None,
			"_todo": "describe this column" if mm.group("name").lower() not in
				{"id", "createdat", "createddate", "modifiedat", "modifieddate", "uid"}
				else None,
		})

	pk = []
	pkm = _PK_RE.search(body)
	if pkm:
		pk = [c.strip().strip("[]") for c in pkm.group(1).split(",")]

	fks = []
	for fkm in _FK_RE.finditer(body):
		fks.append({
			"column": fkm.group(1).strip().strip("[]"),
			"references": fkm.group(2).replace("[", "").replace("]", ""),
		})

	return {
		"schema": schema,
		"name": name,
		"description": None,
		"columns": cols,
		"primaryKey": pk,
		"foreignKeys": fks,
		"_todo": ["describe table purpose", "fill in column descriptions and enum values"],
	}


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

def list_object_files(repo_root: Path, schema: str, kind_dir: str) -> List[Path]:
	"""Find all .sql files under {project}/{schema}/{kind_dir}/."""
	results: List[Path] = []
	for proj in PROJECT_ROOTS:
		root = repo_root / proj / schema / kind_dir
		if root.is_dir():
			results.extend(sorted(root.glob("*.sql")))
	return results


def list_schemas(repo_root: Path) -> List[str]:
	schemas: set = set()
	for proj in PROJECT_ROOTS:
		root = repo_root / proj
		if not root.is_dir():
			continue
		for d in root.iterdir():
			if d.is_dir() and d.name not in EXCLUDE_SCHEMA_NAMES:
				schemas.add(d.name)
	return sorted(schemas)


def categorize_proc(name: str) -> str:
	if name.startswith("job_") or name.startswith("Job_"):
		return "job"
	if name.endswith("_Ops") or name.endswith("_ByOps") or name.endswith("_Admin"):
		return "ops"
	return "runtime"


# ---------------------------------------------------------------------------
# Markdown / JSON emitters
# ---------------------------------------------------------------------------

TODAY = date.today().isoformat()


def write_overview(out_dir: Path, schema: str, counts: Dict[str, int], force: bool) -> str:
	md_path = out_dir / "overview.md"
	json_path = out_dir / "overview.json"
	if md_path.exists() and not force:
		return f"  skip overview.md (exists)"

	md = f"""---
schema: {schema}
full_name: <!-- TODO: full name of this schema -->
last_updated: {TODAY}
object_count:
  tables: {counts.get('tables', 0)}
  procedures: {counts.get('procedures', 0)}
  views: {counts.get('views', 0)}
  functions: {counts.get('functions', 0)}
---

# <!-- TODO: full name --> (`{schema}`)

## Purpose

<!-- TODO: one or two sentences on what this schema handles -->

## Data Flow

<!-- TODO: how data moves through the schema -- write path vs read path -->

## Key Tables

<!-- TODO: brief list of the most important tables and their role -->

## Dependencies

<!-- TODO: which other schemas this one interacts with and how -->

## Access Roles

<!-- TODO: which database roles have access (e.g., role_app_{schema}) -->
"""
	md_path.write_text(md, encoding="utf-8")

	js = {
		"schema": schema,
		"fullName": None,
		"lastUpdated": TODAY,
		"_todo": "fill purpose, dataFlow, keyTables, dependencies, accessRoles",
		"objectCount": {
			"tables": counts.get("tables", 0),
			"procedures": counts.get("procedures", 0),
			"views": counts.get("views", 0),
			"functions": counts.get("functions", 0),
		},
		"purpose": None,
		"dataFlow": {"writePath": None, "readPath": None},
		"keyTables": [],
		"dependencies": [],
		"accessRoles": [],
	}
	json_path.write_text(json.dumps(js, indent=2), encoding="utf-8")
	return f"  wrote overview.md + overview.json"


def write_procedures(out_dir: Path, schema: str, items: List[dict], force: bool) -> str:
	if not items:
		return "  no procedures"
	md_path = out_dir / "procedures.md"
	json_path = out_dir / "procedures.json"
	if md_path.exists() and not force:
		return "  skip procedures.md (exists)"

	groups: Dict[str, List[dict]] = {"runtime": [], "job": [], "ops": []}
	for it in items:
		groups[categorize_proc(it["name"])].append(it)

	body = [f"---\nschema: {schema}\ntype: procedures\ncount: {len(items)}\nlast_updated: {TODAY}\n---\n",
			f"# Stored Procedures — `{schema}`\n",
			"## Contents\n"]

	def _toc(label: str, key: str):
		if not groups[key]:
			return
		body.append(f"### {label}")
		for it in groups[key]:
			desc = it.get("description") or "<!-- TODO: one-line description -->"
			body.append(f"- [{it['name']}](#{it['name'].lower()}) — {desc}")
		body.append("")

	_toc("Runtime", "runtime")
	_toc("Jobs", "job")
	_toc("Operations / Admin", "ops")
	body.append("---\n")

	def _section(label: str, key: str):
		if not groups[key]:
			return
		body.append(f"## {label}\n")
		for it in groups[key]:
			body.append(f"### {it['name']}")
			body.append(it.get("description") or "<!-- TODO: one-line description -->")
			body.append("")
			body.append("**Usage:**")
			body.append("```sql")
			body.append(it.get("usage") or f"-- TODO: realistic EXEC for {schema}.{it['name']}")
			body.append("```")
			body.append("")
			if it["parameters"]:
				body.append("**Parameters:**\n")
				body.append("| Parameter | Type | Default | Description |")
				body.append("|-----------|------|---------|-------------|")
				for p in it["parameters"]:
					default = p["default"] if p["default"] is not None else "_(required)_"
					desc = p.get("description") or "<!-- TODO -->"
					body.append(f"| {p['name']} | {p['type']} | {default} | {desc} |")
				body.append("")
			if it["grantedTo"]:
				body.append(f"**Granted to:** {', '.join(it['grantedTo'])}")
			else:
				body.append("**Granted to:** _<!-- TODO: missing GRANT EXECUTE -->_")
			body.append("\n---\n")

	_section("Runtime", "runtime")
	_section("Jobs", "job")
	_section("Operations / Admin", "ops")

	md_path.write_text("\n".join(body), encoding="utf-8")
	json_path.write_text(json.dumps({
		"schema": schema, "lastUpdated": TODAY,
		"procedures": [{**it, "category": categorize_proc(it["name"])} for it in items],
	}, indent=2), encoding="utf-8")
	return f"  wrote procedures.md + procedures.json ({len(items)} entries)"


def write_views(out_dir: Path, schema: str, items: List[dict], force: bool) -> str:
	if not items:
		return ""
	md_path = out_dir / "views.md"
	json_path = out_dir / "views.json"
	if md_path.exists() and not force:
		return "  skip views.md (exists)"

	body = [f"---\nschema: {schema}\ntype: views\ncount: {len(items)}\nlast_updated: {TODAY}\n---\n",
			f"# Views — `{schema}`\n"]
	for it in items:
		body.append(f"## {it['name']}")
		body.append(it.get("description") or "<!-- TODO: describe view purpose -->")
		body.append("\n**Source tables:** <!-- TODO: list source tables and join semantics -->\n")
		body.append("---\n")
	md_path.write_text("\n".join(body), encoding="utf-8")
	json_path.write_text(json.dumps({
		"schema": schema, "lastUpdated": TODAY, "views": items,
	}, indent=2), encoding="utf-8")
	return f"  wrote views.md + views.json ({len(items)} entries)"


def write_functions(out_dir: Path, schema: str, items: List[dict], force: bool) -> str:
	if not items:
		return ""
	md_path = out_dir / "functions.md"
	json_path = out_dir / "functions.json"
	if md_path.exists() and not force:
		return "  skip functions.md (exists)"

	body = [f"---\nschema: {schema}\ntype: functions\ncount: {len(items)}\nlast_updated: {TODAY}\n---\n",
			f"# Functions — `{schema}`\n"]
	for it in items:
		body.append(f"## {it['name']}")
		body.append(it.get("description") or "<!-- TODO: describe function purpose -->")
		body.append("")
		if it["parameters"]:
			body.append("**Parameters:**")
			body.append("| Parameter | Type | Default |")
			body.append("|-----------|------|---------|")
			for p in it["parameters"]:
				default = p["default"] if p["default"] is not None else "_(required)_"
				body.append(f"| {p['name']} | {p['type']} | {default} |")
		body.append("\n---\n")
	md_path.write_text("\n".join(body), encoding="utf-8")
	json_path.write_text(json.dumps({
		"schema": schema, "lastUpdated": TODAY, "functions": items,
	}, indent=2), encoding="utf-8")
	return f"  wrote functions.md + functions.json ({len(items)} entries)"


def write_tables(out_dir: Path, schema: str, items: List[dict], force: bool) -> str:
	if not items:
		return ""
	md_path = out_dir / "tables.md"
	json_path = out_dir / "tables.json"
	if md_path.exists() and not force:
		return "  skip tables.md (exists)"

	body = [f"---\nschema: {schema}\ntype: tables\ncount: {len(items)}\nlast_updated: {TODAY}\n---\n",
			f"# Tables — `{schema}`\n"]
	for it in items:
		body.append(f"## {it['name']}")
		body.append("<!-- TODO: one-line purpose -->\n")
		if it["columns"]:
			body.append("| Column | Type | Nullable | Default | Description |")
			body.append("|--------|------|----------|---------|-------------|")
			for c in it["columns"]:
				body.append(
					f"| {c['name']} | {c['type']} | "
					f"{'YES' if c['nullable'] else 'NO'} | "
					f"{c['default'] or '—'} | "
					f"{c.get('description') or ('<!-- TODO -->' if c.get('_todo') else '')} |"
				)
			body.append("")
		if it["primaryKey"]:
			body.append(f"**Primary key:** {', '.join(it['primaryKey'])}")
		if it["foreignKeys"]:
			body.append("\n**Foreign keys:**")
			for fk in it["foreignKeys"]:
				body.append(f"- `{fk['column']}` -> `{fk['references']}`")
		body.append("\n---\n")
	md_path.write_text("\n".join(body), encoding="utf-8")
	json_path.write_text(json.dumps({
		"schema": schema, "lastUpdated": TODAY, "tables": items,
	}, indent=2), encoding="utf-8")
	return f"  wrote tables.md + tables.json ({len(items)} entries)"


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def repo_root_or(default: Optional[str]) -> Path:
	if default:
		return Path(default).resolve()
	try:
		out = subprocess.run(
			["git", "rev-parse", "--show-toplevel"],
			capture_output=True, text=True, check=True,
		)
		return Path(out.stdout.strip())
	except Exception:
		return Path.cwd()


def generate_for_schema(repo_root: Path, schema: str, force: bool) -> List[str]:
	out_dir = repo_root / "docs" / "schemas" / schema
	out_dir.mkdir(parents=True, exist_ok=True)
	log: List[str] = [f"\n[{schema}] generating skeleton in {out_dir.relative_to(repo_root)}"]

	tables: List[dict] = []
	for f in list_object_files(repo_root, schema, "Tables"):
		t = parse_table(f.read_text(encoding="utf-8-sig", errors="replace"))
		if t:
			tables.append(t)

	# Dedupe by object name within a schema (an object may appear under
	# both AppDb_MSG and AppDb_MSG_data via a synonym; the docs reflect a
	# single logical object).
	def _dedupe(items: List[dict]) -> List[dict]:
		seen: set = set()
		out: List[dict] = []
		for it in items:
			if it["name"] in seen:
				continue
			seen.add(it["name"])
			out.append(it)
		return out

	procedures: List[dict] = []
	for f in list_object_files(repo_root, schema, "Stored Procedures"):
		p = parse_procedure_or_function(
			f.read_text(encoding="utf-8-sig", errors="replace"), "procedure"
		)
		if p:
			procedures.append(p)
	procedures = _dedupe(procedures)

	views: List[dict] = []
	for f in list_object_files(repo_root, schema, "Views"):
		v = parse_view(f.read_text(encoding="utf-8-sig", errors="replace"))
		if v:
			views.append(v)
	views = _dedupe(views)

	functions: List[dict] = []
	for f in list_object_files(repo_root, schema, "Functions"):
		fn = parse_procedure_or_function(
			f.read_text(encoding="utf-8-sig", errors="replace"), "function"
		)
		if fn:
			functions.append(fn)
	functions = _dedupe(functions)

	counts = {
		"tables": len(tables), "procedures": len(procedures),
		"views": len(views), "functions": len(functions),
	}

	log.append(write_overview(out_dir, schema, counts, force))
	if tables:
		log.append(write_tables(out_dir, schema, tables, force))
	if procedures:
		log.append(write_procedures(out_dir, schema, procedures, force))
	if views:
		log.append(write_views(out_dir, schema, views, force))
	if functions:
		log.append(write_functions(out_dir, schema, functions, force))

	return log


def main() -> int:
	parser = argparse.ArgumentParser(prog="gen_doc_skeleton")
	parser.add_argument("schema", nargs="?", help="Schema name to scaffold")
	parser.add_argument("--all", action="store_true", help="Scaffold every schema under AppDb_MSG/")
	parser.add_argument("--force", action="store_true",
						help="Overwrite existing markdown / JSON files")
	parser.add_argument("--repo-root")
	args = parser.parse_args()

	repo_root = repo_root_or(args.repo_root)

	if not args.schema and not args.all:
		parser.error("supply a schema name or --all")
		return 2

	schemas = [args.schema] if args.schema else list_schemas(repo_root)
	for s in schemas:
		for line in generate_for_schema(repo_root, s, args.force):
			print(line)
	return 0


if __name__ == "__main__":
	try:
		sys.exit(main())
	except Exception as exc:  # pragma: no cover
		print(f"[gen-skeleton] crashed: {exc}", file=sys.stderr)
		sys.exit(1)
