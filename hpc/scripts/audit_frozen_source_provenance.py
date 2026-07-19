"""Compare frozen HPC source-package inputs with the current local sources.

This is a standard-library, read-only audit.  It reproduces the runtime
``source_sha256`` over the files that define Urban Cup contexts and verifies
that the same bytes are present in the Phase 1 source ZIP.
"""

from __future__ import annotations

import argparse
import hashlib
import zipfile
from pathlib import Path


LOCAL_EXECUTION_FILES = (
    "urban_100k_partitioned.py",
    "urban_github_50_agents.py",
    "urban_router.py",
)


def selected_paths(project_root: Path, reference_root: Path) -> list[Path]:
    local_paths = [
        project_root / "experiments" / name for name in LOCAL_EXECUTION_FILES
    ]
    reference_paths = sorted((reference_root / "custom").rglob("*.py"))
    for suffix in ("*.json", "*.yaml", "*.yml"):
        reference_paths.extend(
            sorted((reference_root / "config").rglob(suffix))
        )
    return [*local_paths, *sorted(set(reference_paths))]


def source_name(path: Path, reference_root: Path) -> str:
    try:
        return path.relative_to(reference_root).as_posix()
    except ValueError:
        return path.name


def archive_name(path: Path, reference_root: Path) -> str:
    try:
        relative = path.relative_to(reference_root).as_posix()
        return f"UrbanCup_2026_Jenny-Yoyo-reference/{relative}"
    except ValueError:
        return f"AgentSociety-local/experiments/{path.name}"


def audit(
    project_root: Path, reference_root: Path, source_zip: Path
) -> tuple[str, str, list[str], int]:
    current_digest = hashlib.sha256()
    archive_digest = hashlib.sha256()
    differences: list[str] = []
    paths = selected_paths(project_root, reference_root)
    with zipfile.ZipFile(source_zip) as archive:
        archive_names = set(archive.namelist())
        for path in paths:
            relative_name = source_name(path, reference_root)
            current_bytes = path.read_bytes()
            current_digest.update(relative_name.encode("utf-8"))
            current_digest.update(current_bytes)

            member_name = archive_name(path, reference_root)
            if member_name not in archive_names:
                differences.append(f"MISSING:{member_name}")
                continue
            archive_bytes = archive.read(member_name)
            archive_digest.update(relative_name.encode("utf-8"))
            archive_digest.update(archive_bytes)
            if archive_bytes != current_bytes:
                differences.append(f"CONTENT:{member_name}")
    return (
        current_digest.hexdigest(),
        archive_digest.hexdigest(),
        differences,
        len(paths),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--reference-root", required=True)
    parser.add_argument("--source-zip", required=True)
    parser.add_argument("--expected-source-sha256")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    current, archived, differences, file_count = audit(
        Path(args.project_root).resolve(),
        Path(args.reference_root).resolve(),
        Path(args.source_zip).resolve(),
    )
    print(f"CRITICAL_FILE_COUNT={file_count}")
    print(f"DIFFERENCE_COUNT={len(differences)}")
    for difference in differences:
        print(difference)
    print(f"CURRENT_SOURCE_SHA256={current}")
    print(f"ARCHIVE_SOURCE_SHA256={archived}")
    expected = args.expected_source_sha256
    passed = not differences and current == archived
    if expected is not None:
        passed = passed and current == expected and archived == expected
    print(f"FROZEN_SOURCE_AUDIT={'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
