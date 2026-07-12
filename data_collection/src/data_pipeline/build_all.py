"""Module entry point equivalent to scripts/build_all_databases.py."""
from __future__ import annotations

from scripts.build_all_databases import build


def main() -> None:
    summary = build()
    print(summary)


if __name__ == "__main__":
    main()
