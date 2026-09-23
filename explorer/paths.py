"""Mapping ROOT-relative request paths onto disk, safely."""
import os
from pathlib import Path

from flask import abort

from .config import ROOT


def contained(child: Path, root: Path) -> bool:
    """True if child is root itself or lies underneath it.

    A bare str.startswith() accepts a sibling whose name merely extends the
    root's -- 'C:/code/app-EVIL'.startswith('C:/code/app') is
    True -- so the separator has to take part in the comparison.
    """
    child_s, root_s = str(child), str(root)
    return child_s == root_s or child_s.startswith(root_s + os.sep)


def has_git_component(rel: Path) -> bool:
    """True if any path segment names the .git directory.

    Windows ignores trailing dots and spaces, so '.git.' reaches the same
    directory as '.git' and has to be caught here too.
    """
    return any(part.rstrip(". ").lower() == ".git" for part in rel.parts)


def resolve_rel(rel: str) -> Path:
    """Map a ROOT-relative request path onto disk. Aborts on escape attempts."""
    norm = rel.replace("\\", "/")
    if ".." in norm.split("/"):
        abort(400)
    full = (ROOT / norm).resolve() if norm else ROOT
    if not contained(full, ROOT):
        abort(403)
    return full


def to_rel(full: Path) -> str:
    return full.relative_to(ROOT).as_posix()
