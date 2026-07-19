"""Scan experiment text artifacts for credential material without echoing it."""

from __future__ import annotations

import argparse
import json
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path


TEXT_SUFFIXES = {
    ".csv", ".json", ".jsonl", ".log", ".md", ".py", ".ps1", ".sh",
    ".txt", ".yaml", ".yml",
}
GENERIC_KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9]{20,}")
SKIP_PARTS = {".git", ".venv", "__pycache__"}
BINARY_SUFFIXES = {".pdf", ".xlsx"}


def contains_credential(content: str, api_key: str) -> bool:
    return bool((api_key and api_key in content) or GENERIC_KEY_PATTERN.search(content))


def scan_file(path: Path, api_key: str) -> bool:
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return contains_credential(
            path.read_text(encoding="utf-8", errors="ignore"), api_key
        )
    raw = path.read_bytes()
    if api_key and api_key.encode("utf-8") in raw:
        return True
    if suffix == ".xlsx":
        with zipfile.ZipFile(path) as workbook:
            for member in workbook.infolist():
                if member.is_dir():
                    continue
                content = workbook.read(member).decode("utf-8", errors="ignore")
                if contains_credential(content, api_key):
                    return True
        return False
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader

            text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
            return contains_credential(text, api_key)
        except (ImportError, OSError, ValueError):
            return bool(GENERIC_KEY_PATTERN.search(raw.decode("latin-1", errors="ignore")))
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    api_key = os.getenv("AGENTSOCIETY_LLM_API_KEY", "")
    matches: list[str] = []
    scanned = 0
    for root in args.roots:
        candidates = [root] if root.is_file() else root.rglob("*")
        for path in candidates:
            if (
                not path.is_file()
                or path.suffix.lower() not in TEXT_SUFFIXES | BINARY_SUFFIXES
                or any(part in SKIP_PARTS for part in path.parts)
            ):
                continue
            scanned += 1
            try:
                credential_found = scan_file(path, api_key)
            except (OSError, zipfile.BadZipFile):
                continue
            if credential_found:
                matches.append(str(path.resolve()))
    report = {
        "scanned_at_utc": datetime.now(timezone.utc).isoformat(),
        "files_scanned": scanned,
        "credential_matches": len(matches),
        "matched_paths": matches,
        "credential_value_echoed": False,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(report, ensure_ascii=True))
    if matches:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
