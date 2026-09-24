"""Exact public file inventory shared by scanner and exporter (no directory globs)."""
from pathlib import Path
import re
import stat
import subprocess

MANIFEST = "PUBLISH_MANIFEST.txt"
# Defense in depth: these legacy/private locations can never be exported.
FORBIDDEN_PARTS = {".git", ".env", "profile.local"}
PRIVATE_ROOTS = {"backups", "state", "outputs", "legacy", "building_reviews", "neighborhood_reviews",
                 "user_profile.md", "data", "rules", "config", "research", ".vscode", "agents",
                 "prompts", "templates", "agents.md", "agent_behavior.md", "project_context.md",
                 "system_rules.md", "workflow.md"}


def validate_name(name):
    parts = name.casefold().split("/")
    if (not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", name)
            or parts[0] in PRIVATE_ROOTS
            or any(p in {".", ".."} or p in FORBIDDEN_PARTS or p.startswith(".env.") for p in parts)):
        raise ValueError("unsafe manifest path")
    return name


def regular_file(root, name):
    path = Path(root) / name
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError("symlink forbidden")
    try:
        st = path.stat()
    except OSError:
        raise ValueError("missing/unreadable manifest file") from None
    if not stat.S_ISREG(st.st_mode) or not st.st_mode & 0o444:
        raise ValueError("nonregular/unreadable manifest file")
    return path


def manifest_paths(root):
    manifest = regular_file(root, MANIFEST)
    if manifest.stat().st_size > 1_000_000:
        raise ValueError("manifest oversized")
    names = manifest.read_text(encoding="utf-8").splitlines()
    for name in names:
        validate_name(name)
    if MANIFEST not in names or len(names) != len(set(names)):
        raise ValueError("manifest must include itself exactly once; duplicates forbidden")
    return sorted(names)


def tracked_names(root):
    result = subprocess.run(["git", "-C", str(root), "ls-files", "--stage", "-z"],
                            capture_output=True, check=True)
    names = set()
    for entry in result.stdout.decode("utf-8").split("\0"):
        if not entry:
            continue
        meta, name = entry.split("\t", 1)
        mode, _, stage = meta.split()
        if stage != "0":
            raise ValueError("unmerged index")
        if mode in {"100644", "100755"}:
            names.add(name)
    return names


def check_public_tree(root):
    files = selected_files(root)
    # Include all index entries, not just regular files, to catch added symlinks/submodules.
    tracked = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                             check=True, capture_output=True).stdout.decode().split("\0")
    untracked = subprocess.run(["git", "-C", str(root), "ls-files", "--others", "--exclude-standard", "-z"],
                               check=True, capture_output=True).stdout.decode().split("\0")
    if (set(tracked) | set(untracked)) - {""} != set(manifest_paths(root)):
        raise ValueError("unexpected files outside public manifest")
    return files


def selected_files(root):
    names = manifest_paths(root)
    tracked = tracked_names(root)
    if not set(names) <= tracked:
        raise ValueError("manifest includes untracked/nonregular index entry")
    return [regular_file(root, name) for name in names]
