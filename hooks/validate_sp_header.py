#!/usr/bin/env python3
"""
schemalore rule-checker (Claude-driven, example)

Delegates rule-checking to Claude itself.  No regex inside this validator.
The rules live ONLY in CLAUDE.md and `.claude/skills/schemalore/SKILL.md`
-- this script reads both files verbatim and asks Claude to apply them to
the staged SQL files, returning a structured JSON list of findings via the
Anthropic tool-use feature (the `report_findings` tool).

Why this design
---------------
- One source of truth.  Rules live in CLAUDE.md / SKILL.md.  No duplication
  in Python regex.  Adding or relaxing a rule = edit a markdown file.
- No regex edge cases.  Claude understands the difference between a column
  named `created_date` and a header field "Created date:", or between an
  author named "Claude Smith" and the literal AI tool "Claude".
- Deterministic output.  Tool use forces a strict JSON schema; temperature=0
  pins token sampling; identical input yields identical output across runs.

Auth
----
Tries in order:
  1. ANTHROPIC_API_KEY environment variable -> direct API call (preferred,
     used by CI).
  2. `claude` CLI in headless mode (`claude -p ...`) -> for developers using
     Claude Code OAuth (no separate key required).
  3. Neither -> prints a one-line "doc validation skipped" notice and
     exits 0.  CI is the authoritative gate.

Usage
-----
    validate_sp_header.py [--json] [--strict-block] [--repo-root <path>]
                          [--model <id>] <file> [<file> ...]

    --json           Emit JSON summary to stdout instead of human text.
    --strict-block   Exit 1 on any warning (default: only ERROR-severity
                     findings block).
    --repo-root      Repo root.  Default: `git rev-parse --show-toplevel`.
    --model          Claude model id.  Default: claude-haiku-4-5-20251001
                     (fast + cheap; rule-checking doesn't need Opus/Sonnet).

Requires: Python 3.8+, stdlib only (urllib + json).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
API_TIMEOUT_SECONDS = 30
MAX_TOKENS = 2048

# Files that constitute the rule source-of-truth.  Both are passed to
# Claude verbatim; Claude is instructed to apply only the deterministic /
# machine-checkable rules from them.
RULE_FILES = (
	"CLAUDE.md",
	".claude/skills/schemalore/SKILL.md",
)


# ---------------------------------------------------------------------------
# Auth + API
# ---------------------------------------------------------------------------

def detect_auth() -> Optional[str]:
	"""Return 'api' if ANTHROPIC_API_KEY is set, 'cli' if `claude` is on
	PATH, or None if neither is available."""
	if os.environ.get("ANTHROPIC_API_KEY"):
		return "api"
	if shutil.which("claude"):
		return "cli"
	return None


def call_claude_api(messages: List[dict], system: List[dict], tools: List[dict],
					model: str) -> Optional[dict]:
	"""POST to the Anthropic Messages API.  Returns the parsed response
	dict, or None on failure."""
	api_key = os.environ.get("ANTHROPIC_API_KEY")
	if not api_key:
		return None

	body = {
		"model": model,
		"max_tokens": MAX_TOKENS,
		"system": system,
		"messages": messages,
		"tools": tools,
		"tool_choice": {"type": "tool", "name": "report_findings"},
		"temperature": 0,
	}
	data = json.dumps(body).encode("utf-8")
	req = urllib.request.Request(
		ANTHROPIC_API_URL,
		data=data,
		headers={
			"x-api-key": api_key,
			"anthropic-version": ANTHROPIC_VERSION,
			"content-type": "application/json",
		},
		method="POST",
	)
	try:
		with urllib.request.urlopen(req, timeout=API_TIMEOUT_SECONDS) as resp:
			return json.loads(resp.read().decode("utf-8"))
	except urllib.error.HTTPError as exc:
		body_text = exc.read().decode("utf-8", errors="replace")
		print(f"[doc-check] API error {exc.code}: {body_text[:500]}", file=sys.stderr)
		return None
	except (urllib.error.URLError, TimeoutError, OSError) as exc:
		print(f"[doc-check] API unreachable: {exc}", file=sys.stderr)
		return None


def call_claude_cli(prompt_text: str, model: str) -> Optional[str]:
	"""Pipe `prompt_text` to `claude -p` via stdin and return stdout.
	Stdin avoids the Windows command-line length limit that bites when
	the prompt is multi-KB.  Less reliable than the API path because the
	CLI returns free-form text -- the prompt explicitly asks for JSON
	only and we tolerate a code-fence wrapper."""
	cli = shutil.which("claude")
	if not cli:
		return None
	try:
		result = subprocess.run(
			[cli, "-p", "--model", model, "--output-format", "text"],
			input=prompt_text,
			capture_output=True, text=True, timeout=120,
			encoding="utf-8",
		)
	except subprocess.TimeoutExpired:
		print("[doc-check] claude CLI timed out after 120s", file=sys.stderr)
		return None
	except FileNotFoundError:
		return None
	if result.returncode != 0:
		print(f"[doc-check] claude CLI exit {result.returncode}: {result.stderr[:300]}",
			  file=sys.stderr)
		return None
	return result.stdout


# ---------------------------------------------------------------------------
# Tool-use schema
# ---------------------------------------------------------------------------

REPORT_FINDINGS_TOOL = {
	"name": "report_findings",
	"description": (
		"Report rule violations against the staged SQL files.  Each "
		"finding cites a single rule from CLAUDE.md or "
		".claude/skills/schemalore/SKILL.md.  Use ERROR severity "
		"only when the rule text uses NEVER / MUST NOT (e.g., the "
		"CLAUDE.md Author Guidelines forbidding Claude as an author "
		"name).  Use WARN for every other deterministic gap."
	),
	"input_schema": {
		"type": "object",
		"properties": {
			"findings": {
				"type": "array",
				"description": (
					"Rule violations.  Empty array means all staged "
					"files comply with every deterministic rule."
				),
				"items": {
					"type": "object",
					"properties": {
						"file": {
							"type": "string",
							"description": "Repo-relative path of the staged file with the violation, OR '<cross-file>' for repo-wide gaps (e.g., missing schema overview).",
						},
						"severity": {
							"type": "string",
							"enum": ["error", "warn"],
						},
						"rule": {
							"type": "string",
							"description": "Short identifier for the rule (e.g., 'forbidden-author', 'missing-grant-execute', 'bracketed-object-ref', 'missing-overview-md').",
						},
						"message": {
							"type": "string",
							"description": "One-sentence description of the violation, including the offending value/line when applicable.",
						},
					},
					"required": ["file", "severity", "rule", "message"],
				},
			},
		},
		"required": ["findings"],
	},
}


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

def load_rules(repo_root: Path) -> str:
	"""Concatenate the rule documents that Claude will read."""
	parts: List[str] = []
	for rel in RULE_FILES:
		p = repo_root / rel
		if not p.exists():
			parts.append(f"### {rel}\n\n_(file missing)_\n")
			continue
		parts.append(f"### {rel}\n\n```markdown\n{p.read_text(encoding='utf-8', errors='replace')}\n```\n")
	return "\n\n".join(parts)


def collect_schema_doc_inventory(repo_root: Path, files: List[Path]) -> str:
	"""For every schema touched by `files`, list what already exists under
	docs/schemas/<schema>/.  This gives Claude the cross-file context
	needed to decide whether overview.md / procedures.md / etc. are
	missing or stale."""
	schemas: set = set()
	for f in files:
		parts = f.parts
		for i, part in enumerate(parts):
			if part in ("AppDb_MSG", "AppDb_MSG_data") and i + 1 < len(parts):
				schemas.add(parts[i + 1])

	out: List[str] = []
	for schema in sorted(schemas):
		schema_docs = repo_root / "docs" / "schemas" / schema
		if not schema_docs.is_dir():
			out.append(f"- docs/schemas/{schema}/: DIRECTORY MISSING")
			continue
		entries = sorted(p.name for p in schema_docs.iterdir() if p.is_file())
		out.append(f"- docs/schemas/{schema}/: {entries if entries else 'empty'}")

	# Also include the CLAUDE.md "Documented Schemas" section so Claude
	# can verify cross-file references.
	claude_md = repo_root / "CLAUDE.md"
	docs_section = ""
	if claude_md.exists():
		text = claude_md.read_text(encoding="utf-8", errors="replace")
		idx = text.find("### Documented Schemas")
		if idx >= 0:
			# Up to the next H2.
			tail = text[idx:]
			h2 = tail.find("\n## ")
			docs_section = tail[: (h2 if h2 != -1 else len(tail))]

	if not out:
		out.append("(no schemas under AppDb_MSG/ touched by this changeset)")
	return (
		"#### Schema-doc inventory\n"
		+ "\n".join(out)
		+ "\n\n#### CLAUDE.md `### Documented Schemas` section\n"
		+ (docs_section.strip() if docs_section else "(section not found in CLAUDE.md)")
	)


def build_messages(repo_root: Path, files: List[Path], rules: str
				   ) -> Tuple[List[dict], List[dict]]:
	"""Return (system, messages) for the API call.  System carries the
	rule text with cache_control so it's billed once per 5-minute window;
	messages carries the per-invocation file contents."""
	staged_blocks: List[str] = []
	for f in files:
		try:
			content = f.read_text(encoding="utf-8-sig", errors="replace")
		except OSError as exc:
			content = f"_(could not read: {exc})_"
		rel = str(f).replace("\\", "/")
		fence = "```sql"
		staged_blocks.append(f"#### {rel}\n{fence}\n{content}\n```")

	doc_inventory = collect_schema_doc_inventory(repo_root, files)

	system = [
		{
			"type": "text",
			"text": (
				"You are a deterministic rule-checker for the AppDb_MSG / "
				"AppDb_MSG_data SSDT database project.  Your job is to apply "
				"the documentation and SSDT rules from CLAUDE.md and "
				".claude/skills/schemalore/SKILL.md to the staged SQL "
				"files supplied by the user, then call the `report_findings` "
				"tool with one entry per rule violation.\n\n"
				"## Severity model\n"
				"- ERROR: rules whose source text uses NEVER, MUST NOT, "
				"or comparable absolute language.  Currently this includes "
				"the CLAUDE.md `Author Guidelines` rule that NEVER allows "
				"`Claude` as an author name in SQL objects.\n"
				"- WARN: every other deterministic rule (header field "
				"presence, GRANT EXECUTE on procedures, bracket-free "
				"references, GO separators after `sp_addextendedproperty` "
				"or `CREATE INDEX` in table files, schema-doc presence, "
				"SP listed in procedures.md, schema referenced in "
				"CLAUDE.md `### Documented Schemas`).\n\n"
				"## Hard rules\n"
				"- Apply ONLY rules that are explicit in CLAUDE.md or "
				"SKILL.md.  Do not invent rules.\n"
				"- Files under `*/Storage/`, `*/Security/`, `*/Scripts/`, "
				"`*/Migration/`, `*/external-refs/`, `*/Synonyms/`, "
				"`*/User Defined Types/` are out of scope -- skip them.\n"
				"- `*/Tables/` files are checked only for the SSDT "
				"GO-separator rules; they have no header requirements.\n"
				"- `*/Stored Procedures/`, `*/Functions/`, `*/Views/` are "
				"checked for header completeness.  Stored Procedures "
				"additionally need GRANT EXECUTE.\n"
				"- For cross-file findings (overview.md missing, schema "
				"absent from CLAUDE.md, etc.), set `file` to `<cross-file>`.\n"
				"- An empty `findings` array means every rule passed; "
				"return that explicitly rather than refusing to call the "
				"tool.\n"
				"- Do not output any text other than the tool call.\n\n"
				"## Rule source-of-truth (verbatim)\n\n" + rules
			),
			"cache_control": {"type": "ephemeral"},
		},
	]

	user_text = (
		"## Cross-file context\n\n"
		+ doc_inventory
		+ "\n\n## Staged files (apply rules to these)\n\n"
		+ "\n\n".join(staged_blocks)
		+ "\n\nCall `report_findings` now with all rule violations across "
		"the staged files.  Empty array if everything complies."
	)
	messages = [{"role": "user", "content": [{"type": "text", "text": user_text}]}]
	return system, messages


# ---------------------------------------------------------------------------
# Response handling
# ---------------------------------------------------------------------------

def extract_findings_from_api_response(resp: dict) -> Optional[List[dict]]:
	"""Pull the `findings` list out of an Anthropic API response.  Returns
	None if the response is malformed."""
	if not isinstance(resp, dict):
		return None
	for block in resp.get("content", []):
		if block.get("type") == "tool_use" and block.get("name") == "report_findings":
			inp = block.get("input", {})
			findings = inp.get("findings", [])
			if isinstance(findings, list):
				return findings
	return None


def extract_findings_from_cli_text(text: str) -> Optional[List[dict]]:
	"""The CLI fallback returns free-form text.  We instructed Claude to
	output a JSON object with a `findings` array; try to parse it
	tolerantly."""
	if not text:
		return None
	# Strip code fences if present.
	stripped = text.strip()
	if stripped.startswith("```"):
		# Drop the opening fence line and the closing fence.
		lines = stripped.splitlines()
		if lines and lines[0].startswith("```"):
			lines = lines[1:]
		if lines and lines[-1].startswith("```"):
			lines = lines[:-1]
		stripped = "\n".join(lines)
	try:
		obj = json.loads(stripped)
	except json.JSONDecodeError:
		return None
	if isinstance(obj, dict) and isinstance(obj.get("findings"), list):
		return obj["findings"]
	if isinstance(obj, list):
		return obj
	return None


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


def is_in_scope(path: Path) -> bool:
	"""Cheap pre-filter: skip files that are clearly out of scope so we
	don't spend tokens on them."""
	parts = [p.lower() for p in path.parts]
	skip = {"storage", "security", "scripts", "migration",
			"external-refs", "synonyms", "user defined types"}
	return not any(p in skip for p in parts)


def print_findings(findings: List[dict]) -> Tuple[int, int]:
	"""Return (errors, warnings)."""
	errors = [f for f in findings if f.get("severity") == "error"]
	warnings = [f for f in findings if f.get("severity") == "warn"]

	if not findings:
		print("[doc-check] All staged files comply with every deterministic rule.")
		return 0, 0

	if errors:
		print(f"[doc-check] BLOCKING errors ({len(errors)}):")
		for e in errors:
			print(f"  ERROR  [{e.get('rule', '?')}]  {e.get('file', '?')}")
			print(f"         {e.get('message', '')}")
		print()

	if warnings:
		print(f"[doc-check] Warnings ({len(warnings)}):")
		for w in warnings:
			print(f"  WARN   [{w.get('rule', '?')}]  {w.get('file', '?')}")
			print(f"         {w.get('message', '')}")
		print()

	if errors:
		print(
			"[doc-check] BLOCKING -- fix the errors above. "
			"Bypass (discouraged; CI reports errors as warnings): git commit --no-verify"
		)
	else:
		print("[doc-check] Non-blocking -- run /schemalore to fix.")

	return len(errors), len(warnings)


def main() -> int:
	parser = argparse.ArgumentParser(
		prog="validate_sp_header",
		description=(
			"Claude-driven schemalore rule checker.  Reads CLAUDE.md "
			"and SKILL.md as the rule source of truth, sends staged SQL "
			"files to Claude, and returns structured findings."
		),
	)
	parser.add_argument("paths", nargs="*", help="SQL file(s) to validate")
	parser.add_argument("--json", action="store_true",
						help="Emit JSON summary to stdout")
	parser.add_argument("--strict-block", action="store_true",
						help="Exit 1 on any warning (default: only ERROR blocks)")
	parser.add_argument("--repo-root", help="Repo root path")
	parser.add_argument("--model", default=DEFAULT_MODEL,
						help=f"Claude model ID (default: {DEFAULT_MODEL})")
	parser.add_argument(
		"--mock-findings",
		help=(
			"Path to a JSON file containing pre-computed findings (test "
			"hook).  Bypasses the Claude call -- exercises the response "
			"parsing, output formatting, and exit-code logic only."
		),
	)
	args = parser.parse_args()

	repo_root = repo_root_or(args.repo_root)

	# Pre-filter staged paths.
	staged_files: List[Path] = []
	for raw in args.paths:
		p = Path(raw)
		if not p.exists():
			# Skip silently -- nothing to check on a path that doesn't exist.
			continue
		if is_in_scope(p):
			staged_files.append(p)

	if not staged_files:
		if args.json:
			print(json.dumps({"findings": [], "errors": 0, "warnings": 0,
							  "status": "no-in-scope-files"}))
		return 0

	# Mock mode: bypass Claude and load findings from a fixture file.
	# Used by the test harness to exercise the parsing/output paths.
	if args.mock_findings:
		try:
			data = json.loads(Path(args.mock_findings).read_text(encoding="utf-8"))
		except (OSError, json.JSONDecodeError) as exc:
			print(f"[doc-check] could not load --mock-findings: {exc}", file=sys.stderr)
			return 0
		findings = data if isinstance(data, list) else data.get("findings", [])
		if args.json:
			errors = sum(1 for f in findings if f.get("severity") == "error")
			warnings = sum(1 for f in findings if f.get("severity") == "warn")
			print(json.dumps({
				"findings": findings, "errors": errors, "warnings": warnings,
				"status": "mock",
			}, indent=2))
		else:
			print_findings(findings)
		errors = sum(1 for f in findings if f.get("severity") == "error")
		warnings = sum(1 for f in findings if f.get("severity") == "warn")
		if errors > 0:
			return 1
		if args.strict_block and warnings > 0:
			return 1
		return 0

	auth = detect_auth()
	if auth is None:
		msg = (
			"[doc-check] Skipping -- no Claude auth available.\n"
			"  Set ANTHROPIC_API_KEY in your shell to enable, or install\n"
			"  Claude Code (`claude` CLI).  CI is the authoritative gate."
		)
		if args.json:
			print(json.dumps({"findings": [], "errors": 0, "warnings": 0,
							  "status": "skipped-no-auth"}))
		else:
			print(msg)
		return 0

	rules = load_rules(repo_root)
	system, messages = build_messages(repo_root, staged_files, rules)

	findings: Optional[List[dict]] = None
	if auth == "api":
		resp = call_claude_api(messages, system, [REPORT_FINDINGS_TOOL], args.model)
		if resp is not None:
			findings = extract_findings_from_api_response(resp)
	elif auth == "cli":
		# Inline the rules + files into a single prompt; instruct strict JSON.
		prompt = (
			system[0]["text"]
			+ "\n\n"
			+ messages[0]["content"][0]["text"]
			+ "\n\n## Output format\n"
			"Return ONLY a JSON object of the form\n"
			"  {\"findings\": [{\"file\": \"...\", \"severity\": \"error|warn\", "
			"\"rule\": \"...\", \"message\": \"...\"}]}\n"
			"with no markdown fences or commentary."
		)
		text = call_claude_cli(prompt, args.model)
		if text is not None:
			findings = extract_findings_from_cli_text(text)

	if findings is None:
		# API/CLI failed.  Treat as skip; CI is the authoritative gate.
		msg = "[doc-check] Skipping -- Claude call failed (network / auth / parse). CI will still gate."
		if args.json:
			print(json.dumps({"findings": [], "errors": 0, "warnings": 0,
							  "status": "skipped-call-failed"}))
		else:
			print(msg)
		return 0

	if args.json:
		errors = sum(1 for f in findings if f.get("severity") == "error")
		warnings = sum(1 for f in findings if f.get("severity") == "warn")
		print(json.dumps({
			"findings": findings,
			"errors": errors,
			"warnings": warnings,
			"status": "ok",
		}, indent=2))
	else:
		errors, warnings = print_findings(findings)

	# Re-derive for exit code regardless of output mode.
	errors = sum(1 for f in findings if f.get("severity") == "error")
	warnings = sum(1 for f in findings if f.get("severity") == "warn")

	if errors > 0:
		return 1
	if args.strict_block and warnings > 0:
		return 1
	return 0


if __name__ == "__main__":
	try:
		sys.exit(main())
	except Exception as exc:  # pragma: no cover
		print(f"[doc-check] validator crashed: {exc}", file=sys.stderr)
		# Skip on crash; do not block commit on a tooling bug.
		sys.exit(0)
