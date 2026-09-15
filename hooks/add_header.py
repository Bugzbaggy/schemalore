#!/usr/bin/env python3
"""
Header autofill for sql-documenter (example)

For an SP / Function / View file with a missing or partial header block,
inject the standard CLAUDE.md header skeleton:

    -- =============================================
    -- Author: <git blame full name>
    -- Created date: <first-commit date YYYY-MM-DD>
    -- Description: <TODO -- one-line summary>
    -- Usage: <TODO -- realistic EXEC / SELECT example>
    -- =============================================

Only writes fields that are missing -- existing fields are preserved
verbatim.  The Description / Usage lines are inserted as TODO markers
(unless the file already had a value); /sql-documenter or the human
developer fills those in.

Idempotent.  Refuses to modify files outside */Stored Procedures/,
*/Functions/, */Views/.

Usage:
    add_header.py <file> [<file> ...]            # patch in place
    add_header.py --check <file> [<file> ...]    # exit 1 if any file
                                                  # would change
    add_header.py --dry-run <file>               # print what would
                                                  # change, don't write

Requires: Python 3.8+, stdlib only.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

VALID_PARENTS = ("Stored Procedures", "Functions", "Views")

_FIELD_RE = re.compile(
	r"^\s*--\s*(?P<key>Author|Created?\s+date|Description|Usage)\s*:\s*(?P<value>.*?)\s*$",
	re.IGNORECASE,
)
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_HEADER_BANNER_RE = re.compile(r"^\s*--\s*=+\s*$")


def is_valid_kind(path: Path) -> bool:
	parts = path.parts
	return any(p in VALID_PARENTS for p in parts)


def parse_existing_header(text: str) -> Tuple[Dict[str, str], int]:
	"""Return (fields, header_end_line_idx). header_end_line_idx is the
	index of the line that ends the header block (== the line containing
	the closing `=====` banner, or 0 if there is no header)."""
	fields: Dict[str, str] = {}
	end_idx = 0
	saw_open_banner = False
	for i, raw in enumerate(text.splitlines()):
		stripped = raw.strip()
		if not stripped:
			if saw_open_banner:
				continue
			else:
				continue
		if _HEADER_BANNER_RE.match(raw):
			if not saw_open_banner:
				saw_open_banner = True
				continue
			# closing banner
			end_idx = i
			break
		if not stripped.startswith("--"):
			# code reached
			break
		m = _FIELD_RE.match(raw)
		if not m:
			continue
		key = re.sub(r"\s+", " ", m.group("key").strip().lower())
		if key == "create date":
			key = "created date"
		fields[key] = m.group("value").strip()
	return fields, end_idx


def git_blame_author(path: Path) -> Optional[str]:
	"""Return the most recent contributor's full name, or None."""
	try:
		out = subprocess.run(
			["git", "log", "--diff-filter=A", "--follow",
			 "--pretty=format:%an", "--", str(path)],
			capture_output=True, text=True, check=True,
		)
	except subprocess.CalledProcessError:
		return None
	# The last line of `git log --reverse` would be "first author"; without
	# --reverse we get "newest first".  The intent here is the original
	# author, so we want the last entry in the log.
	lines = [l for l in out.stdout.splitlines() if l.strip()]
	if not lines:
		return None
	return lines[-1]


def git_first_commit_date(path: Path) -> Optional[str]:
	"""Return ISO date (YYYY-MM-DD) of the first commit that added this file."""
	try:
		out = subprocess.run(
			["git", "log", "--diff-filter=A", "--follow",
			 "--pretty=format:%ad", "--date=short", "--", str(path)],
			capture_output=True, text=True, check=True,
		)
	except subprocess.CalledProcessError:
		return None
	lines = [l for l in out.stdout.splitlines() if l.strip()]
	if not lines:
		return None
	return lines[-1]


def build_header(existing: Dict[str, str], path: Path) -> str:
	author = existing.get("author") or git_blame_author(path) or "<TODO -- engineer full name>"
	created = existing.get("created date") or git_first_commit_date(path) or "<TODO -- YYYY-MM-DD>"
	description = existing.get("description") or "<TODO -- one-line summary>"
	usage = existing.get("usage") or "<TODO -- realistic EXEC or SELECT example>"
	lines = [
		"-- =============================================",
		f"-- Author: {author}",
		f"-- Created date: {created}",
		f"-- Description: {description}",
		f"-- Usage: {usage}",
		"-- =============================================",
	]
	return "\n".join(lines) + "\n"


def patch_file(path: Path) -> Tuple[bool, str]:
	"""Return (changed, summary)."""
	if not is_valid_kind(path):
		return False, f"skip {path} (not under a Stored Procedures/Functions/Views directory)"

	original = path.read_text(encoding="utf-8-sig", errors="replace")
	fields, end_idx = parse_existing_header(original)

	required = {"author", "created date", "description", "usage"}
	missing = required - set(fields.keys())
	if not missing:
		return False, f"OK   {path} (header complete)"

	new_header = build_header(fields, path)

	# Replace the existing header banner block (if any) with the new one;
	# otherwise, prepend.
	lines = original.splitlines(keepends=True)
	if end_idx > 0:
		# Replace from start through end_idx (inclusive of closing banner).
		body_start = end_idx + 1
		new_text = new_header + "".join(lines[body_start:])
	else:
		new_text = new_header + original
	if not new_text.endswith("\n"):
		new_text += "\n"
	path.write_text(new_text, encoding="utf-8")
	added = ", ".join(sorted(missing))
	return True, f"PATCH {path} (added: {added})"


def main() -> int:
	parser = argparse.ArgumentParser(prog="add_header")
	parser.add_argument("paths", nargs="+")
	parser.add_argument("--check", action="store_true",
						help="Exit 1 if any file would change; do not write.")
	parser.add_argument("--dry-run", action="store_true",
						help="Print what would change without writing.")
	args = parser.parse_args()

	any_change = False
	for raw in args.paths:
		path = Path(raw)
		if not path.exists():
			print(f"MISS {path} (file not found)")
			continue
		if args.check or args.dry_run:
			# Compute would-change without writing.
			if not is_valid_kind(path):
				print(f"skip {path} (not under SP/Function/View)")
				continue
			text = path.read_text(encoding="utf-8-sig", errors="replace")
			fields, _ = parse_existing_header(text)
			missing = {"author", "created date", "description", "usage"} - set(fields.keys())
			if missing:
				any_change = True
				print(f"WOULD-PATCH {path} (missing: {', '.join(sorted(missing))})")
			else:
				print(f"OK    {path} (header complete)")
			continue

		changed, summary = patch_file(path)
		if changed:
			any_change = True
		print(summary)

	if args.check and any_change:
		return 1
	return 0


if __name__ == "__main__":
	try:
		sys.exit(main())
	except Exception as exc:  # pragma: no cover
		print(f"[add-header] crashed: {exc}", file=sys.stderr)
		sys.exit(1)
