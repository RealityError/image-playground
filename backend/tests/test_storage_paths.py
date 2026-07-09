import sqlite3
from pathlib import Path

from db import migrate_storage_paths
from storage_paths import normalize_storage_path, resolve_storage_path, storage_path_for_db


def test_storage_path_for_db_stores_generated_paths_relative() -> None:
    source_path = Path.cwd() / "generated" / "sample.png"

    assert storage_path_for_db(source_path, {"generated"}) == "generated/sample.png"


def test_normalize_legacy_absolute_path_under_backend_root() -> None:
    legacy_path = r"C:\Users\whitecat\Desktop\project\image-playground\backend\thumbnails\job_1.webp"

    assert normalize_storage_path(legacy_path, {"thumbnails"}) == "thumbnails/job_1.webp"


def test_reject_path_traversal_values() -> None:
    assert normalize_storage_path("../outside.png", {"generated"}) is None
    assert resolve_storage_path("../outside.png", {"generated"}) is None


def test_db_migration_normalizes_only_known_storage_roots() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE generation_images (saved_path TEXT, thumbnail_path TEXT);
        CREATE TABLE input_images (saved_path TEXT);
        """
    )
    legacy_root = r"C:\Users\whitecat\Desktop\project\image-playground"
    connection.execute(
        "INSERT INTO generation_images VALUES (?, ?)",
        (
            legacy_root + r"\backend\generated\job_1.png",
            legacy_root + r"\backend\thumbnails\job_1.webp",
        ),
    )
    connection.execute(
        "INSERT INTO generation_images VALUES (?, ?)",
        (
            legacy_root + r"\elsewhere\generated\bad.png",
            r"C:\outside\bad.webp",
        ),
    )
    connection.execute(
        "INSERT INTO input_images VALUES (?)",
        (legacy_root + r"\backend\generated\input.png",),
    )

    migrate_storage_paths(connection)

    rows = connection.execute("SELECT * FROM generation_images").fetchall()
    input_row = connection.execute("SELECT * FROM input_images").fetchone()

    assert rows[0]["saved_path"] == "generated/job_1.png"
    assert rows[0]["thumbnail_path"] == "thumbnails/job_1.webp"
    assert rows[1]["saved_path"].endswith(r"elsewhere\generated\bad.png")
    assert rows[1]["thumbnail_path"] == r"C:\outside\bad.webp"
    assert input_row["saved_path"] == "generated/input.png"
