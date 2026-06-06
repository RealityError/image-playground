from pathlib import Path
from typing import Iterable

BASE_DIR = Path(__file__).resolve().parent
GENERATED_DIR = BASE_DIR / "generated"
THUMBNAIL_DIR = BASE_DIR / "thumbnails"
UPLOAD_DIR = BASE_DIR / "uploads"

STORAGE_ROOTS: dict[str, Path] = {
    "generated": GENERATED_DIR,
    "thumbnails": THUMBNAIL_DIR,
    "uploads": UPLOAD_DIR,
}


def _allowed_roots(allowed_roots: Iterable[str] | None = None) -> set[str]:
    if allowed_roots is None:
        return set(STORAGE_ROOTS)
    roots = {str(root).strip().strip("/\\").lower() for root in allowed_roots}
    unknown = roots - set(STORAGE_ROOTS)
    if unknown:
        raise ValueError(f"Unknown storage roots: {', '.join(sorted(unknown))}")
    return roots


def _clean_parts(value: str) -> list[str] | None:
    parts = [part for part in value.replace("\\", "/").split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        return None
    return parts


def normalize_storage_path(value: str | Path | None, allowed_roots: Iterable[str] | None = None) -> str | None:
    text = str(value).strip() if value is not None else ""
    if not text:
        return None

    allowed = _allowed_roots(allowed_roots)
    candidate = Path(text)
    if candidate.is_absolute():
        resolved = candidate.resolve(strict=False)
        for root_name in sorted(allowed):
            root = STORAGE_ROOTS[root_name].resolve(strict=False)
            try:
                relative = resolved.relative_to(root)
            except ValueError:
                continue
            if not relative.parts:
                return None
            return f"{root_name}/{relative.as_posix()}"

    parts = _clean_parts(text)
    if not parts:
        return None

    first = parts[0].lower()
    if first in allowed:
        if len(parts) == 1:
            return None
        return "/".join([first, *parts[1:]])

    lowered = [part.lower() for part in parts]
    for index in range(len(lowered) - 1):
        root_name = lowered[index + 1]
        if lowered[index] == "backend" and root_name in allowed:
            if index + 2 >= len(parts):
                return None
            return "/".join([root_name, *parts[index + 2:]])

    return None


def storage_path_for_db(path: str | Path, allowed_roots: Iterable[str] | None = None) -> str:
    normalized = normalize_storage_path(path, allowed_roots)
    if normalized is None:
        raise ValueError(f"Path is outside configured storage roots: {path}")
    return normalized


def resolve_storage_path(value: str | Path | None, allowed_roots: Iterable[str] | None = None) -> Path | None:
    normalized = normalize_storage_path(value, allowed_roots)
    if normalized is None:
        return None
    root_name, *relative_parts = normalized.split("/")
    if not relative_parts:
        return None
    root = STORAGE_ROOTS[root_name].resolve(strict=False)
    path = STORAGE_ROOTS[root_name].joinpath(*relative_parts).resolve(strict=False)
    if root not in path.parents:
        return None
    return path
