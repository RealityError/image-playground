import json
import sqlite3
from pathlib import Path
from typing import Any

from storage_paths import normalize_storage_path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "service.db"


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def migrate_storage_paths(connection: sqlite3.Connection) -> None:
    targets = (
        ("generation_images", "saved_path", {"generated"}),
        ("generation_images", "thumbnail_path", {"thumbnails"}),
        ("input_images", "saved_path", {"generated", "uploads"}),
    )
    for table, column, roots in targets:
        rows = connection.execute(
            f"""
            SELECT rowid AS row_id, {column} AS path_value
            FROM {table}
            WHERE {column} IS NOT NULL
              AND {column} <> ''
            """
        ).fetchall()
        updates: list[tuple[str, int]] = []
        for row in rows:
            path_value = row["path_value"]
            normalized = normalize_storage_path(path_value, roots)
            if normalized and normalized != path_value:
                updates.append((normalized, int(row["row_id"])))
        if updates:
            connection.executemany(
                f"UPDATE {table} SET {column} = ? WHERE rowid = ?",
                updates,
            )


def _count_query(connection: sqlite3.Connection, query: str, params: tuple[Any, ...]) -> int:
    row = connection.execute(query, params).fetchone()
    if row is None:
        return 0
    return int(row[0] or 0)


def _build_where(filters: list[str]) -> str:
    if not filters:
        return ""
    return "WHERE " + " AND ".join(filters)


def _owner_summary_base_query(where_clause: str) -> str:
    return f"""
        FROM generation_requests gr
        LEFT JOIN (
            SELECT job_id, SUM(COALESCE(size_bytes, 0)) AS total_size_bytes
            FROM generation_images
            GROUP BY job_id
        ) gis ON gis.job_id = gr.job_id
        LEFT JOIN owner_labels ol
            ON ol.owner_type = gr.owner_type AND ol.owner_id = gr.owner_id
        LEFT JOIN blocked_owners bo
            ON bo.owner_type = gr.owner_type AND bo.owner_id = gr.owner_id
        {where_clause}
    """


def init_db() -> None:
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS generation_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                scope TEXT NOT NULL,
                route TEXT NOT NULL,
                client_ip TEXT NOT NULL,
                user_agent TEXT,
                owner_type TEXT NOT NULL DEFAULT 'anonymous',
                owner_id TEXT NOT NULL DEFAULT '',
                prompt TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                elapsed_seconds REAL,
                image_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                operation TEXT NOT NULL DEFAULT 'generate',
                request_params_json TEXT,
                response_params_json TEXT,
                input_image_count INTEGER NOT NULL DEFAULT 0,
                mask_used INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT,
                deleted_by TEXT,
                deleted_reason TEXT,
                files_removed_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_generation_requests_created_at
                ON generation_requests(created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_generation_requests_scope
                ON generation_requests(scope, created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_generation_requests_ip
                ON generation_requests(client_ip, created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_generation_requests_owner
                ON generation_requests(scope, owner_type, owner_id, created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_generation_requests_status
                ON generation_requests(status, created_at DESC);

            CREATE TABLE IF NOT EXISTS generation_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                image_index INTEGER NOT NULL,
                url TEXT,
                saved_path TEXT,
                thumbnail_path TEXT,
                size_bytes INTEGER,
                source TEXT,
                created_at TEXT NOT NULL,
                deleted_at TEXT,
                deleted_by TEXT,
                deleted_reason TEXT,
                files_removed_at TEXT,
                FOREIGN KEY(job_id) REFERENCES generation_requests(job_id)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_generation_images_job_index
                ON generation_images(job_id, image_index);

            CREATE INDEX IF NOT EXISTS idx_generation_images_created_at
                ON generation_images(created_at DESC);

            CREATE TABLE IF NOT EXISTS auth_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                scope TEXT NOT NULL,
                event_type TEXT NOT NULL,
                success INTEGER NOT NULL DEFAULT 0,
                owner_type TEXT,
                owner_id TEXT,
                client_ip TEXT NOT NULL,
                user_agent TEXT,
                detail TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_auth_events_created_at
                ON auth_events(created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_auth_events_scope
                ON auth_events(scope, created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_auth_events_owner
                ON auth_events(owner_type, owner_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS owner_labels (
                owner_type TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                label TEXT,
                note TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(owner_type, owner_id)
            );

            CREATE TABLE IF NOT EXISTS blocked_owners (
                owner_type TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(owner_type, owner_id)
            );

            CREATE TABLE IF NOT EXISTS input_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                image_index INTEGER NOT NULL,
                image_type TEXT NOT NULL DEFAULT 'input',
                saved_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES generation_requests(job_id)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_input_images_job_index_type
                ON input_images(job_id, image_index, image_type);

            CREATE TABLE IF NOT EXISTS runtime_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS provider_profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                provider_type TEXT NOT NULL,
                base_url TEXT,
                api_key TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                default_model TEXT NOT NULL,
                models_json TEXT NOT NULL DEFAULT '[]',
                parameters_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

        ensure_column(connection, "generation_requests", "owner_type", "TEXT NOT NULL DEFAULT 'anonymous'")
        ensure_column(connection, "generation_requests", "owner_id", "TEXT NOT NULL DEFAULT ''")
        ensure_column(connection, "generation_requests", "operation", "TEXT NOT NULL DEFAULT 'generate'")
        ensure_column(connection, "generation_requests", "request_params_json", "TEXT")
        ensure_column(connection, "generation_requests", "response_params_json", "TEXT")
        ensure_column(connection, "generation_requests", "input_image_count", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(connection, "generation_requests", "mask_used", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(connection, "generation_requests", "deleted_at", "TEXT")
        ensure_column(connection, "generation_requests", "deleted_by", "TEXT")
        ensure_column(connection, "generation_requests", "deleted_reason", "TEXT")
        ensure_column(connection, "generation_requests", "files_removed_at", "TEXT")
        ensure_column(connection, "generation_requests", "provider_id", "TEXT")
        ensure_column(connection, "generation_requests", "provider_name_snapshot", "TEXT")
        ensure_column(connection, "generation_requests", "provider_type", "TEXT")
        ensure_column(connection, "generation_images", "thumbnail_path", "TEXT")
        ensure_column(connection, "generation_images", "deleted_at", "TEXT")
        ensure_column(connection, "generation_images", "deleted_by", "TEXT")
        ensure_column(connection, "generation_images", "deleted_reason", "TEXT")
        ensure_column(connection, "generation_images", "files_removed_at", "TEXT")
        migrate_storage_paths(connection)


def log_generation_started(
    *,
    job_id: str,
    created_at: str,
    scope: str,
    route: str,
    client_ip: str,
    user_agent: str,
    owner_type: str,
    owner_id: str,
    prompt: str,
    model: str,
    operation: str,
    request_params_json: str,
    input_image_count: int,
    mask_used: bool,
    provider_id: str | None = None,
    provider_name_snapshot: str | None = None,
    provider_type: str | None = None,
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO generation_requests (
                job_id, created_at, scope, route, client_ip, user_agent,
                owner_type, owner_id, prompt, model, status, operation,
                request_params_json, input_image_count, mask_used,
                provider_id, provider_name_snapshot, provider_type
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                created_at,
                scope,
                route,
                client_ip,
                user_agent,
                owner_type,
                owner_id,
                prompt,
                model,
                operation,
                request_params_json,
                input_image_count,
                1 if mask_used else 0,
                provider_id,
                provider_name_snapshot,
                provider_type,
            ),
        )


def log_generation_finished(
    *,
    job_id: str,
    completed_at: str,
    elapsed_seconds: float,
    images: list[dict[str, Any]],
    response_params_json: str | None = None,
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE generation_requests
            SET completed_at = ?, status = 'success', elapsed_seconds = ?, image_count = ?, response_params_json = ?
            WHERE job_id = ?
            """,
            (completed_at, elapsed_seconds, len(images), response_params_json, job_id),
        )
        connection.executemany(
            """
            INSERT INTO generation_images (
                job_id, image_index, url, saved_path, thumbnail_path, size_bytes, source, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    job_id,
                    int(image.get("index", 0) or 0),
                    image.get("url"),
                    image.get("saved_path"),
                    image.get("thumbnail_path"),
                    image.get("size_bytes"),
                    image.get("source"),
                    completed_at,
                )
                for image in images
            ],
        )


def log_generation_failed(
    *,
    job_id: str,
    completed_at: str,
    elapsed_seconds: float,
    error_message: str,
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE generation_requests
            SET completed_at = ?, status = 'failed', elapsed_seconds = ?, error_message = ?
            WHERE job_id = ?
            """,
            (completed_at, elapsed_seconds, error_message, job_id),
        )


def fail_running_generations(*, completed_at: str, error_message: str) -> int:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE generation_requests
            SET completed_at = ?, status = 'failed', elapsed_seconds = COALESCE(elapsed_seconds, 0), error_message = ?
            WHERE status = 'running'
            """,
            (completed_at, error_message),
        )
        return int(cursor.rowcount or 0)


def log_auth_event(
    *,
    created_at: str,
    scope: str,
    event_type: str,
    success: bool,
    client_ip: str,
    user_agent: str,
    owner_type: str | None = None,
    owner_id: str | None = None,
    detail: str | None = None,
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO auth_events (
                created_at, scope, event_type, success, owner_type,
                owner_id, client_ip, user_agent, detail
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                scope,
                event_type,
                1 if success else 0,
                owner_type,
                owner_id,
                client_ip,
                user_agent,
                detail,
            ),
        )


def is_owner_blocked(owner_type: str, owner_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT owner_type, owner_id, reason, created_at, updated_at
            FROM blocked_owners
            WHERE owner_type = ? AND owner_id = ?
            """,
            (owner_type, owner_id),
        ).fetchone()
    return dict(row) if row else None


def is_job_deleted(job_id: str) -> bool:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT deleted_at
            FROM generation_requests
            WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
    return bool(row and row["deleted_at"])


def list_history_images(owner_type: str, owner_id: str, offset: int, limit: int) -> tuple[list[dict[str, Any]], bool]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                gi.id,
                gi.job_id,
                gi.image_index,
                gi.url,
                gi.saved_path,
                gi.thumbnail_path,
                gi.size_bytes,
                gi.source,
                gr.created_at,
                gr.completed_at,
                gr.operation,
                gr.elapsed_seconds,
                gr.prompt,
                gr.provider_id,
                gr.provider_name_snapshot,
                gr.provider_type,
                gr.request_params_json,
                gr.response_params_json,
                gr.owner_type,
                gr.owner_id,
                gr.input_image_count,
                gr.mask_used
            FROM generation_images gi
            JOIN generation_requests gr ON gr.job_id = gi.job_id
            WHERE gr.status = 'success'
              AND gr.owner_type = ?
              AND gr.owner_id = ?
              AND gr.deleted_at IS NULL
              AND gi.deleted_at IS NULL
            ORDER BY gr.created_at DESC, gi.image_index ASC
            LIMIT ? OFFSET ?
            """,
            (owner_type, owner_id, limit + 1, offset),
        ).fetchall()

    items = [dict(row) for row in rows[:limit]]
    has_more = len(rows) > limit
    return items, has_more


def get_image_record(job_id: str, image_index: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                gi.job_id,
                gi.image_index,
                gi.url,
                gi.saved_path,
                gi.thumbnail_path,
                gi.size_bytes,
                gi.source,
                gi.created_at AS image_created_at,
                gr.created_at,
                gr.completed_at,
                gr.scope,
                gr.route,
                gr.owner_type,
                gr.owner_id,
                gr.status,
                gr.operation,
                gr.prompt,
                gr.deleted_at,
                gr.files_removed_at,
                gi.deleted_at AS image_deleted_at,
                gi.files_removed_at AS image_files_removed_at
            FROM generation_images gi
            JOIN generation_requests gr ON gr.job_id = gi.job_id
            WHERE gi.job_id = ? AND gi.image_index = ?
            """,
            (job_id, image_index),
        ).fetchone()
    return dict(row) if row else None


def update_image_thumbnail_path(job_id: str, image_index: int, thumbnail_path: str) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE generation_images
            SET thumbnail_path = ?
            WHERE job_id = ? AND image_index = ?
            """,
            (thumbnail_path, job_id, image_index),
        )


def list_admin_gallery_images(
    *,
    offset: int,
    limit: int,
    search: str | None = None,
    scope: str | None = None,
    operation: str | None = None,
    owner_type: str | None = None,
    owner_id: str | None = None,
    include_deleted: bool = False,
    deleted: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    filters = ["gr.status = 'success'", "gi.saved_path IS NOT NULL", "gi.saved_path <> ''"]
    params: list[Any] = []
    if deleted == "only":
        filters.append("(gr.deleted_at IS NOT NULL OR gi.deleted_at IS NOT NULL)")
    elif deleted != "include" and not include_deleted:
        filters.append("gr.deleted_at IS NULL")
        filters.append("gi.deleted_at IS NULL")

    if search:
        filters.append(
            "(gr.job_id LIKE ? OR gr.prompt LIKE ? OR gr.client_ip LIKE ? OR gr.owner_id LIKE ? OR COALESCE(ol.label, '') LIKE ? OR COALESCE(ol.note, '') LIKE ?)"
        )
        like = f"%{search}%"
        params.extend([like, like, like, like, like, like])
    if scope:
        filters.append("gr.scope = ?")
        params.append(scope)
    if operation:
        filters.append("gr.operation = ?")
        params.append(operation)
    if owner_type:
        filters.append("gr.owner_type = ?")
        params.append(owner_type)
    if owner_id:
        filters.append("gr.owner_id = ?")
        params.append(owner_id)

    where_clause = _build_where(filters)

    with get_connection() as connection:
        total = _count_query(
            connection,
            f"""
            SELECT COUNT(*)
            FROM generation_images gi
            JOIN generation_requests gr ON gr.job_id = gi.job_id
            LEFT JOIN owner_labels ol
                ON ol.owner_type = gr.owner_type AND ol.owner_id = gr.owner_id
            LEFT JOIN blocked_owners bo
                ON bo.owner_type = gr.owner_type AND bo.owner_id = gr.owner_id
            {where_clause}
            """,
            tuple(params),
        )
        rows = connection.execute(
            f"""
            SELECT
                gi.id,
                gi.job_id,
                gi.image_index,
                gi.saved_path,
                gi.thumbnail_path,
                gi.size_bytes,
                gi.source,
                gi.created_at AS image_created_at,
                gr.created_at,
                gr.completed_at,
                gr.scope,
                gr.route,
                gr.client_ip,
                gr.user_agent,
                gr.owner_type,
                gr.owner_id,
                gr.prompt,
                gr.provider_id,
                gr.provider_name_snapshot,
                gr.provider_type,
                gr.model,
                gr.elapsed_seconds,
                gr.image_count,
                gr.operation,
                gr.input_image_count,
                gr.mask_used,
                gr.deleted_at,
                gr.deleted_by,
                gr.deleted_reason,
                gr.files_removed_at,
                gi.deleted_at AS image_deleted_at,
                gi.deleted_by AS image_deleted_by,
                gi.deleted_reason AS image_deleted_reason,
                gi.files_removed_at AS image_files_removed_at,
                COALESCE(ol.label, '') AS owner_label,
                COALESCE(ol.note, '') AS owner_note,
                bo.reason AS blocked_reason
            FROM generation_images gi
            JOIN generation_requests gr ON gr.job_id = gi.job_id
            LEFT JOIN owner_labels ol
                ON ol.owner_type = gr.owner_type AND ol.owner_id = gr.owner_id
            LEFT JOIN blocked_owners bo
                ON bo.owner_type = gr.owner_type AND bo.owner_id = gr.owner_id
            {where_clause}
            ORDER BY gr.created_at DESC, gi.image_index ASC
            LIMIT ? OFFSET ?
            """,
            tuple(params + [limit, offset]),
        ).fetchall()

    return [dict(row) for row in rows], total


def get_admin_overview() -> dict[str, Any]:
    with get_connection() as connection:
        summary_row = connection.execute(
            """
            SELECT
                COUNT(*) AS total_jobs,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_jobs,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_jobs,
                SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running_jobs,
                SUM(CASE WHEN scope = 'web' THEN 1 ELSE 0 END) AS web_jobs,
                SUM(CASE WHEN scope = 'api' THEN 1 ELSE 0 END) AS api_jobs,
                SUM(COALESCE(image_count, 0)) AS total_images,
                SUM(COALESCE(gis.total_size_bytes, 0)) AS total_size_bytes
            FROM generation_requests gr
            LEFT JOIN (
                SELECT job_id, SUM(COALESCE(size_bytes, 0)) AS total_size_bytes
                FROM generation_images
                GROUP BY job_id
            ) gis ON gis.job_id = gr.job_id
            """
        ).fetchone()

        today_prefix = connection.execute(
            "SELECT strftime('%Y-%m-%d', 'now', 'localtime')"
        ).fetchone()[0]

        today_row = connection.execute(
            """
            SELECT
                COUNT(*) AS jobs_today,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_today,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_today
            FROM generation_requests
            WHERE created_at >= ?
            """,
            (f"{today_prefix}T00:00:00",),
        ).fetchone()

        owner_space_count = _count_query(
            connection,
            """
            SELECT COUNT(*)
            FROM (
                SELECT owner_type, owner_id
                FROM generation_requests
                WHERE owner_id <> ''
                GROUP BY owner_type, owner_id
            )
            """,
            (),
        )

        blocked_count = _count_query(
            connection,
            "SELECT COUNT(*) FROM blocked_owners",
            (),
        )

        auth_failures_24h = _count_query(
            connection,
            """
            SELECT COUNT(*)
            FROM auth_events
            WHERE success = 0
              AND created_at >= datetime('now', '-1 day', 'localtime')
            """,
            (),
        )

    summary = dict(summary_row or {})
    summary.update(dict(today_row or {}))
    summary["owner_spaces"] = owner_space_count
    summary["blocked_owner_spaces"] = blocked_count
    summary["auth_failures_24h"] = auth_failures_24h
    return {key: int(value or 0) for key, value in summary.items()}


def get_admin_dashboard() -> dict[str, Any]:
    with get_connection() as connection:
        overview = get_admin_overview()
        avg_elapsed_row = connection.execute(
            """
            SELECT AVG(elapsed_seconds) AS avg_elapsed_seconds
            FROM generation_requests
            WHERE status = 'success' AND elapsed_seconds IS NOT NULL
            """
        ).fetchone()
        recent_failures = connection.execute(
            """
            SELECT
                job_id,
                created_at,
                completed_at,
                scope,
                operation,
                owner_type,
                owner_id,
                prompt,
                error_message,
                elapsed_seconds,
                request_params_json
            FROM generation_requests
            WHERE status = 'failed'
            ORDER BY completed_at DESC, created_at DESC
            LIMIT 12
            """
        ).fetchall()
        size_rows = connection.execute(
            """
            SELECT
                json_extract(request_params_json, '$.size') AS size,
                COUNT(*) AS total_jobs,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_jobs,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_jobs,
                AVG(CASE WHEN status = 'success' THEN elapsed_seconds END) AS avg_elapsed_seconds
            FROM generation_requests
            WHERE request_params_json IS NOT NULL AND request_params_json <> ''
            GROUP BY json_extract(request_params_json, '$.size')
            ORDER BY total_jobs DESC
            LIMIT 10
            """
        ).fetchall()

    overview["avg_elapsed_seconds"] = round(float(avg_elapsed_row["avg_elapsed_seconds"] or 0), 2)
    return {
        "overview": overview,
        "recent_failures": [dict(row) for row in recent_failures],
        "size_stats": [dict(row) for row in size_rows],
    }


def list_admin_jobs(
    *,
    offset: int,
    limit: int,
    search: str | None = None,
    status: str | None = None,
    scope: str | None = None,
    operation: str | None = None,
    owner_type: str | None = None,
    owner_id: str | None = None,
    deleted: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    filters: list[str] = []
    params: list[Any] = []

    if search:
        filters.append(
            "(gr.job_id LIKE ? OR gr.prompt LIKE ? OR gr.client_ip LIKE ? OR COALESCE(gr.error_message, '') LIKE ? OR COALESCE(ol.label, '') LIKE ? OR gr.owner_id LIKE ?)"
        )
        like = f"%{search}%"
        params.extend([like, like, like, like, like, like])
    if status:
        filters.append("gr.status = ?")
        params.append(status)
    if scope:
        filters.append("gr.scope = ?")
        params.append(scope)
    if operation:
        filters.append("gr.operation = ?")
        params.append(operation)
    if owner_type:
        filters.append("gr.owner_type = ?")
        params.append(owner_type)
    if owner_id:
        filters.append("gr.owner_id = ?")
        params.append(owner_id)
    if deleted == "only":
        filters.append("gr.deleted_at IS NOT NULL")
    elif deleted != "include":
        filters.append("gr.deleted_at IS NULL")

    where_clause = _build_where(filters)

    with get_connection() as connection:
        total = _count_query(
            connection,
            f"""
            SELECT COUNT(*)
            FROM generation_requests gr
            LEFT JOIN owner_labels ol
                ON ol.owner_type = gr.owner_type AND ol.owner_id = gr.owner_id
            {where_clause}
            """,
            tuple(params),
        )

        rows = connection.execute(
            f"""
            SELECT
                gr.job_id,
                gr.created_at,
                gr.completed_at,
                gr.scope,
                gr.route,
                gr.client_ip,
                gr.user_agent,
                gr.owner_type,
                gr.owner_id,
                gr.prompt,
                gr.provider_id,
                gr.provider_name_snapshot,
                gr.provider_type,
                gr.model,
                gr.status,
                gr.elapsed_seconds,
                gr.image_count,
                gr.error_message,
                gr.operation,
                gr.request_params_json,
                gr.response_params_json,
                gr.input_image_count,
                gr.mask_used,
                gr.deleted_at,
                gr.deleted_by,
                gr.deleted_reason,
                gr.files_removed_at,
                COALESCE(SUM(gi.size_bytes), 0) AS total_size_bytes,
                COALESCE(ol.label, '') AS owner_label,
                COALESCE(ol.note, '') AS owner_note,
                bo.reason AS blocked_reason
            FROM generation_requests gr
            LEFT JOIN generation_images gi ON gi.job_id = gr.job_id
            LEFT JOIN owner_labels ol
                ON ol.owner_type = gr.owner_type AND ol.owner_id = gr.owner_id
            LEFT JOIN blocked_owners bo
                ON bo.owner_type = gr.owner_type AND bo.owner_id = gr.owner_id
            {where_clause}
            GROUP BY gr.job_id
            ORDER BY gr.created_at DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params + [limit, offset]),
        ).fetchall()

    return [dict(row) for row in rows], total


def get_admin_job_detail(job_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                gr.job_id,
                gr.created_at,
                gr.completed_at,
                gr.scope,
                gr.route,
                gr.client_ip,
                gr.user_agent,
                gr.owner_type,
                gr.owner_id,
                gr.prompt,
                gr.provider_id,
                gr.provider_name_snapshot,
                gr.provider_type,
                gr.model,
                gr.status,
                gr.elapsed_seconds,
                gr.image_count,
                gr.error_message,
                gr.operation,
                gr.request_params_json,
                gr.response_params_json,
                gr.input_image_count,
                gr.mask_used,
                gr.deleted_at,
                gr.deleted_by,
                gr.deleted_reason,
                gr.files_removed_at,
                COALESCE(ol.label, '') AS owner_label,
                COALESCE(ol.note, '') AS owner_note,
                bo.reason AS blocked_reason
            FROM generation_requests gr
            LEFT JOIN owner_labels ol
                ON ol.owner_type = gr.owner_type AND ol.owner_id = gr.owner_id
            LEFT JOIN blocked_owners bo
                ON bo.owner_type = gr.owner_type AND bo.owner_id = gr.owner_id
            WHERE gr.job_id = ?
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            return None

        images = connection.execute(
            """
            SELECT
                job_id,
                image_index,
                url,
                saved_path,
                thumbnail_path,
                size_bytes,
                source,
                created_at,
                deleted_at,
                deleted_by,
                deleted_reason,
                files_removed_at
            FROM generation_images
            WHERE job_id = ?
            ORDER BY image_index ASC
            """,
            (job_id,),
        ).fetchall()

    payload = dict(row)
    payload["images"] = [dict(image) for image in images]
    return payload


def get_owner_job_detail(job_id: str, owner_type: str, owner_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                job_id,
                created_at,
                completed_at,
                scope,
                route,
                client_ip,
                owner_type,
                owner_id,
                prompt,
                provider_id,
                provider_name_snapshot,
                provider_type,
                model,
                status,
                elapsed_seconds,
                image_count,
                error_message,
                operation,
                request_params_json,
                response_params_json,
                input_image_count,
                mask_used,
                deleted_at,
                deleted_by,
                deleted_reason,
                files_removed_at
            FROM generation_requests
            WHERE job_id = ? AND scope = 'web' AND owner_type = ? AND owner_id = ?
            """,
            (job_id, owner_type, owner_id),
        ).fetchone()
        if row is None:
            return None

        images = connection.execute(
            """
            SELECT
                job_id,
                image_index,
                url,
                saved_path,
                thumbnail_path,
                size_bytes,
                source,
                created_at,
                deleted_at,
                deleted_by,
                deleted_reason,
                files_removed_at
            FROM generation_images
            WHERE job_id = ?
            ORDER BY image_index ASC
            """,
            (job_id,),
        ).fetchall()

    payload = dict(row)
    payload["images"] = [dict(image) for image in images]
    return payload


def list_admin_owners(
    *,
    offset: int,
    limit: int,
    search: str | None = None,
    owner_type: str | None = None,
    blocked_only: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    filters: list[str] = []
    params: list[Any] = []

    if search:
        filters.append("(gr.owner_id LIKE ? OR COALESCE(ol.label, '') LIKE ? OR COALESCE(ol.note, '') LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])
    if owner_type:
        filters.append("gr.owner_type = ?")
        params.append(owner_type)
    if blocked_only:
        filters.append("bo.owner_id IS NOT NULL")

    where_clause = _build_where(filters)
    base_query = _owner_summary_base_query(where_clause)

    with get_connection() as connection:
        total = _count_query(
            connection,
            f"""
            SELECT COUNT(*)
            FROM (
                SELECT gr.owner_type, gr.owner_id
                {base_query}
                GROUP BY gr.owner_type, gr.owner_id
            )
            """,
            tuple(params),
        )

        rows = connection.execute(
            f"""
            SELECT
                gr.owner_type,
                gr.owner_id,
                COALESCE(ol.label, '') AS label,
                COALESCE(ol.note, '') AS note,
                bo.reason AS blocked_reason,
                COUNT(*) AS job_count,
                SUM(CASE WHEN gr.status = 'success' THEN 1 ELSE 0 END) AS success_jobs,
                SUM(CASE WHEN gr.status = 'failed' THEN 1 ELSE 0 END) AS failed_jobs,
                SUM(COALESCE(gr.image_count, 0)) AS image_count,
                SUM(COALESCE(gis.total_size_bytes, 0)) AS total_size_bytes,
                MAX(gr.created_at) AS last_created_at,
                MIN(gr.created_at) AS first_created_at
            {base_query}
            GROUP BY gr.owner_type, gr.owner_id
            ORDER BY last_created_at DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params + [limit, offset]),
        ).fetchall()

    return [dict(row) for row in rows], total


def lookup_owner(owner_type: str, owner_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                gr.owner_type,
                gr.owner_id,
                COALESCE(ol.label, '') AS label,
                COALESCE(ol.note, '') AS note,
                bo.reason AS blocked_reason,
                COUNT(*) AS job_count,
                SUM(CASE WHEN gr.status = 'success' THEN 1 ELSE 0 END) AS success_jobs,
                SUM(CASE WHEN gr.status = 'failed' THEN 1 ELSE 0 END) AS failed_jobs,
                SUM(COALESCE(gr.image_count, 0)) AS image_count,
                SUM(COALESCE(gis.total_size_bytes, 0)) AS total_size_bytes,
                MAX(gr.created_at) AS last_created_at,
                MIN(gr.created_at) AS first_created_at
            FROM generation_requests gr
            LEFT JOIN (
                SELECT job_id, SUM(COALESCE(size_bytes, 0)) AS total_size_bytes
                FROM generation_images
                GROUP BY job_id
            ) gis ON gis.job_id = gr.job_id
            LEFT JOIN owner_labels ol
                ON ol.owner_type = gr.owner_type AND ol.owner_id = gr.owner_id
            LEFT JOIN blocked_owners bo
                ON bo.owner_type = gr.owner_type AND bo.owner_id = gr.owner_id
            WHERE gr.owner_type = ? AND gr.owner_id = ?
            GROUP BY gr.owner_type, gr.owner_id
            """,
            (owner_type, owner_id),
        ).fetchone()

        if row is not None:
            return dict(row)

        label_row = connection.execute(
            """
            SELECT
                ol.owner_type,
                ol.owner_id,
                COALESCE(ol.label, '') AS label,
                COALESCE(ol.note, '') AS note,
                bo.reason AS blocked_reason
            FROM owner_labels ol
            LEFT JOIN blocked_owners bo
                ON bo.owner_type = ol.owner_type AND bo.owner_id = ol.owner_id
            WHERE ol.owner_type = ? AND ol.owner_id = ?
            """,
            (owner_type, owner_id),
        ).fetchone()
        if label_row is not None:
            payload = dict(label_row)
            payload.update(
                {
                    "job_count": 0,
                    "success_jobs": 0,
                    "failed_jobs": 0,
                    "image_count": 0,
                    "total_size_bytes": 0,
                    "last_created_at": None,
                    "first_created_at": None,
                }
            )
            return payload

        blocked_row = connection.execute(
            """
            SELECT owner_type, owner_id, reason
            FROM blocked_owners
            WHERE owner_type = ? AND owner_id = ?
            """,
            (owner_type, owner_id),
        ).fetchone()
        if blocked_row is not None:
            payload = dict(blocked_row)
            payload.update(
                {
                    "label": "",
                    "note": "",
                    "job_count": 0,
                    "success_jobs": 0,
                    "failed_jobs": 0,
                    "image_count": 0,
                    "total_size_bytes": 0,
                    "last_created_at": None,
                    "first_created_at": None,
                    "blocked_reason": payload.pop("reason"),
                }
            )
            return payload

    return None


def set_owner_label(
    *,
    owner_type: str,
    owner_id: str,
    label: str,
    note: str,
    updated_at: str,
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO owner_labels (owner_type, owner_id, label, note, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(owner_type, owner_id)
            DO UPDATE SET
                label = excluded.label,
                note = excluded.note,
                updated_at = excluded.updated_at
            """,
            (owner_type, owner_id, label, note, updated_at),
        )


def set_owner_block(
    *,
    owner_type: str,
    owner_id: str,
    blocked: bool,
    reason: str,
    timestamp: str,
) -> None:
    with get_connection() as connection:
        if blocked:
            connection.execute(
                """
                INSERT INTO blocked_owners (owner_type, owner_id, reason, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(owner_type, owner_id)
                DO UPDATE SET
                    reason = excluded.reason,
                    updated_at = excluded.updated_at
                """,
                (owner_type, owner_id, reason, timestamp, timestamp),
            )
        else:
            connection.execute(
                """
                DELETE FROM blocked_owners
                WHERE owner_type = ? AND owner_id = ?
                """,
                (owner_type, owner_id),
            )


def list_auth_events(
    *,
    offset: int,
    limit: int,
    scope: str | None = None,
    success: bool | None = None,
    search: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    filters: list[str] = []
    params: list[Any] = []

    if scope:
        filters.append("scope = ?")
        params.append(scope)
    if success is not None:
        filters.append("success = ?")
        params.append(1 if success else 0)
    if search:
        filters.append("(event_type LIKE ? OR COALESCE(owner_id, '') LIKE ? OR client_ip LIKE ? OR COALESCE(detail, '') LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like, like])

    where_clause = _build_where(filters)

    with get_connection() as connection:
        total = _count_query(
            connection,
            f"SELECT COUNT(*) FROM auth_events {where_clause}",
            tuple(params),
        )
        rows = connection.execute(
            f"""
            SELECT
                id,
                created_at,
                scope,
                event_type,
                success,
                owner_type,
                owner_id,
                client_ip,
                user_agent,
                detail
            FROM auth_events
            {where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params + [limit, offset]),
        ).fetchall()
    return [dict(row) for row in rows], total


def _file_paths_from_rows(rows: list[sqlite3.Row]) -> list[str]:
    return [
        str(path)
        for row in rows
        for path in (row["saved_path"], row["thumbnail_path"])
        if path
    ]


def soft_delete_job(
    job_id: str,
    *,
    deleted_at: str,
    deleted_by: str,
    deleted_reason: str,
) -> tuple[list[str], bool]:
    with get_connection() as connection:
        job = connection.execute(
            """
            SELECT job_id
            FROM generation_requests
            WHERE job_id = ? AND deleted_at IS NULL
            """,
            (job_id,),
        ).fetchone()
        if job is None:
            return [], False

        rows = connection.execute(
            """
            SELECT saved_path, thumbnail_path
            FROM generation_images
            WHERE job_id = ?
              AND deleted_at IS NULL
              AND saved_path IS NOT NULL
              AND saved_path <> ''
            """,
            (job_id,),
        ).fetchall()
        saved_paths = _file_paths_from_rows(rows)

        connection.execute(
            """
            UPDATE generation_requests
            SET deleted_at = ?,
                deleted_by = ?,
                deleted_reason = ?,
                files_removed_at = ?
            WHERE job_id = ?
            """,
            (deleted_at, deleted_by, deleted_reason, deleted_at, job_id),
        )
        connection.execute(
            """
            UPDATE generation_images
            SET deleted_at = ?,
                deleted_by = ?,
                deleted_reason = ?,
                files_removed_at = ?
            WHERE job_id = ? AND deleted_at IS NULL
            """,
            (deleted_at, deleted_by, deleted_reason, deleted_at, job_id),
        )

    return saved_paths, True


def soft_delete_image(
    job_id: str,
    image_index: int,
    *,
    deleted_at: str,
    deleted_by: str,
    deleted_reason: str,
) -> tuple[list[str], bool]:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT saved_path, thumbnail_path
            FROM generation_images
            WHERE job_id = ?
              AND image_index = ?
              AND deleted_at IS NULL
            """,
            (job_id, image_index),
        ).fetchone()
        if row is None:
            return [], False

        saved_paths = _file_paths_from_rows([row])
        connection.execute(
            """
            UPDATE generation_images
            SET deleted_at = ?,
                deleted_by = ?,
                deleted_reason = ?,
                files_removed_at = ?
            WHERE job_id = ? AND image_index = ?
            """,
            (deleted_at, deleted_by, deleted_reason, deleted_at, job_id, image_index),
        )

    return saved_paths, True


def soft_delete_owner_jobs(
    owner_type: str,
    owner_id: str,
    *,
    deleted_at: str,
    deleted_by: str,
    deleted_reason: str,
) -> tuple[list[str], int]:
    with get_connection() as connection:
        job_rows = connection.execute(
            """
            SELECT job_id
            FROM generation_requests
            WHERE owner_type = ? AND owner_id = ? AND deleted_at IS NULL
            """,
            (owner_type, owner_id),
        ).fetchall()
        job_ids = [str(row["job_id"]) for row in job_rows]
        if not job_ids:
            return [], 0

        placeholders = ",".join("?" for _ in job_ids)
        path_rows = connection.execute(
            f"""
            SELECT saved_path, thumbnail_path
            FROM generation_images
            WHERE job_id IN ({placeholders})
              AND deleted_at IS NULL
              AND saved_path IS NOT NULL
              AND saved_path <> ''
            """,
            tuple(job_ids),
        ).fetchall()
        saved_paths = _file_paths_from_rows(path_rows)

        connection.execute(
            f"""
            UPDATE generation_requests
            SET deleted_at = ?,
                deleted_by = ?,
                deleted_reason = ?,
                files_removed_at = ?
            WHERE job_id IN ({placeholders}) AND deleted_at IS NULL
            """,
            tuple([deleted_at, deleted_by, deleted_reason, deleted_at, *job_ids]),
        )
        connection.execute(
            f"""
            UPDATE generation_images
            SET deleted_at = ?,
                deleted_by = ?,
                deleted_reason = ?,
                files_removed_at = ?
            WHERE job_id IN ({placeholders}) AND deleted_at IS NULL
            """,
            tuple([deleted_at, deleted_by, deleted_reason, deleted_at, *job_ids]),
        )

    return saved_paths, len(job_ids)


def delete_job(job_id: str) -> tuple[list[str], bool]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT saved_path, thumbnail_path
            FROM generation_images
            WHERE job_id = ? AND saved_path IS NOT NULL AND saved_path <> ''
            """,
            (job_id,),
        ).fetchall()
        saved_paths = _file_paths_from_rows(rows)

        connection.execute("DELETE FROM generation_images WHERE job_id = ?", (job_id,))
        deleted = connection.execute(
            "DELETE FROM generation_requests WHERE job_id = ?",
            (job_id,),
        ).rowcount > 0

    return saved_paths, deleted


def soft_delete_owner_job(
    job_id: str,
    owner_type: str,
    owner_id: str,
    *,
    deleted_at: str,
    deleted_by: str,
    deleted_reason: str,
) -> tuple[list[str], bool]:
    with get_connection() as connection:
        job = connection.execute(
            """
            SELECT job_id
            FROM generation_requests
            WHERE job_id = ?
              AND scope = 'web'
              AND owner_type = ?
              AND owner_id = ?
              AND deleted_at IS NULL
            """,
            (job_id, owner_type, owner_id),
        ).fetchone()
        if job is None:
            return [], False

        rows = connection.execute(
            """
            SELECT saved_path, thumbnail_path
            FROM generation_images
            WHERE job_id = ?
              AND deleted_at IS NULL
              AND saved_path IS NOT NULL
              AND saved_path <> ''
            """,
            (job_id,),
        ).fetchall()
        saved_paths = [
            str(path)
            for row in rows
            for path in (row["saved_path"], row["thumbnail_path"])
            if path
        ]

        connection.execute(
            """
            UPDATE generation_requests
            SET deleted_at = ?,
                deleted_by = ?,
                deleted_reason = ?,
                files_removed_at = ?
            WHERE job_id = ?
            """,
            (deleted_at, deleted_by, deleted_reason, deleted_at, job_id),
        )
        connection.execute(
            """
            UPDATE generation_images
            SET deleted_at = ?,
                deleted_by = ?,
                deleted_reason = ?,
                files_removed_at = ?
            WHERE job_id = ? AND deleted_at IS NULL
            """,
            (deleted_at, deleted_by, deleted_reason, deleted_at, job_id),
        )

    return saved_paths, True


def delete_owner_jobs(owner_type: str, owner_id: str) -> tuple[list[str], int]:
    with get_connection() as connection:
        job_rows = connection.execute(
            """
            SELECT job_id
            FROM generation_requests
            WHERE owner_type = ? AND owner_id = ?
            """,
            (owner_type, owner_id),
        ).fetchall()
        job_ids = [str(row["job_id"]) for row in job_rows]
        if not job_ids:
            return [], 0

        placeholders = ",".join("?" for _ in job_ids)
        path_rows = connection.execute(
            f"""
            SELECT saved_path, thumbnail_path
            FROM generation_images
            WHERE job_id IN ({placeholders})
              AND saved_path IS NOT NULL
              AND saved_path <> ''
            """,
            tuple(job_ids),
        ).fetchall()
        saved_paths = [
            str(path)
            for row in path_rows
            for path in (row["saved_path"], row["thumbnail_path"])
            if path
        ]

        connection.execute(
            f"DELETE FROM generation_images WHERE job_id IN ({placeholders})",
            tuple(job_ids),
        )
        deleted_count = connection.execute(
            f"DELETE FROM generation_requests WHERE job_id IN ({placeholders})",
            tuple(job_ids),
        ).rowcount

    return saved_paths, int(deleted_count)


def save_input_image(
    *,
    job_id: str,
    image_index: int,
    image_type: str,
    saved_path: str,
    created_at: str,
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO input_images (job_id, image_index, image_type, saved_path, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (job_id, image_index, image_type, saved_path, created_at),
        )


def get_input_images(job_id: str) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM input_images WHERE job_id = ? ORDER BY image_type, image_index",
            (job_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_input_image(job_id: str, image_index: int, image_type: str = "input") -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM input_images WHERE job_id = ? AND image_index = ? AND image_type = ?",
            (job_id, image_index, image_type),
        ).fetchone()
        return dict(row) if row else None


def delete_input_images_for_jobs(job_ids: list[str]) -> list[str]:
    if not job_ids:
        return []
    with get_connection() as connection:
        placeholders = ",".join("?" * len(job_ids))
        rows = connection.execute(
            f"SELECT saved_path FROM input_images WHERE job_id IN ({placeholders})",
            tuple(job_ids),
        ).fetchall()
        paths = [row["saved_path"] for row in rows if row["saved_path"]]
        connection.execute(
            f"DELETE FROM input_images WHERE job_id IN ({placeholders})",
            tuple(job_ids),
        )
    return paths


# ===== Provider Profiles =====

def _safe_json_loads(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except Exception:
        return fallback


def _api_key_preview(api_key: str | None) -> str:
    if not api_key:
        return ""
    tail = api_key[-4:] if len(api_key) >= 4 else api_key
    return f"...{tail}"


def _serialize_provider_row(row: sqlite3.Row, *, include_secret: bool = False) -> dict[str, Any]:
    payload = dict(row)
    api_key = str(payload.pop("api_key", "") or "")
    payload["enabled"] = bool(payload.get("enabled"))
    payload["models"] = _safe_json_loads(payload.pop("models_json", "[]"), [])
    payload["parameters"] = _safe_json_loads(payload.pop("parameters_json", "{}"), {})
    payload["api_key_configured"] = bool(api_key)
    payload["api_key_preview"] = _api_key_preview(api_key)
    if include_secret:
        payload["api_key"] = api_key
    return payload


def list_provider_profiles(*, include_disabled: bool = True, include_secret: bool = False) -> list[dict[str, Any]]:
    where = "" if include_disabled else "WHERE enabled = 1"
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM provider_profiles
            {where}
            ORDER BY enabled DESC, name COLLATE NOCASE ASC, id ASC
            """
        ).fetchall()
    return [_serialize_provider_row(row, include_secret=include_secret) for row in rows]


def get_provider_profile(provider_id: str, *, include_secret: bool = False) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM provider_profiles WHERE id = ?",
            (provider_id,),
        ).fetchone()
    if row is None:
        return None
    return _serialize_provider_row(row, include_secret=include_secret)


def get_provider_profile_secret(provider_id: str) -> dict[str, Any] | None:
    return get_provider_profile(provider_id, include_secret=True)


def upsert_provider_profile(profile: dict[str, Any], *, updated_at: str) -> dict[str, Any]:
    provider_id = str(profile.get("id") or "").strip()
    name = str(profile.get("name") or "").strip()
    provider_type = str(profile.get("provider_type") or "openai-compatible").strip()
    default_model = str(profile.get("default_model") or "").strip()
    if not provider_id:
        raise ValueError("provider id is required")
    if not name:
        raise ValueError("provider name is required")
    if provider_type != "openai-compatible":
        raise ValueError("only openai-compatible provider_type is supported")
    if not default_model:
        raise ValueError("default_model is required")

    base_url = str(profile.get("base_url") or "").strip()
    enabled = 1 if bool(profile.get("enabled", True)) else 0
    models = profile.get("models")
    if not isinstance(models, list):
        models = []
    normalized_models = [str(item).strip() for item in models if str(item).strip()]
    if default_model not in normalized_models:
        normalized_models.insert(0, default_model)
    parameters = profile.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}
    normalized_parameters: dict[str, list[str]] = {}
    for key, values in parameters.items():
        clean_key = str(key).strip()
        if not clean_key:
            continue
        if isinstance(values, list):
            normalized_parameters[clean_key] = [str(value).strip() for value in values if str(value).strip()]
        elif values is True:
            normalized_parameters[clean_key] = []

    new_api_key = profile.get("api_key")
    clear_api_key = bool(profile.get("clear_api_key"))

    with get_connection() as connection:
        existing = connection.execute(
            "SELECT api_key, created_at FROM provider_profiles WHERE id = ?",
            (provider_id,),
        ).fetchone()
        if clear_api_key:
            api_key = ""
        elif new_api_key is not None:
            api_key = str(new_api_key).strip()
        elif existing is not None:
            api_key = str(existing["api_key"] or "")
        else:
            api_key = ""
        created_at = str(existing["created_at"]) if existing is not None else updated_at
        connection.execute(
            """
            INSERT INTO provider_profiles (
                id, name, provider_type, base_url, api_key, enabled,
                default_model, models_json, parameters_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                provider_type = excluded.provider_type,
                base_url = excluded.base_url,
                api_key = excluded.api_key,
                enabled = excluded.enabled,
                default_model = excluded.default_model,
                models_json = excluded.models_json,
                parameters_json = excluded.parameters_json,
                updated_at = excluded.updated_at
            """,
            (
                provider_id,
                name,
                provider_type,
                base_url,
                api_key,
                enabled,
                default_model,
                json.dumps(normalized_models, ensure_ascii=False),
                json.dumps(normalized_parameters, ensure_ascii=False),
                created_at,
                updated_at,
            ),
        )

    saved = get_provider_profile(provider_id)
    if saved is None:
        raise RuntimeError("failed to save provider profile")
    return saved


def delete_provider_profile(provider_id: str) -> bool:
    with get_connection() as connection:
        cursor = connection.execute("DELETE FROM provider_profiles WHERE id = ?", (provider_id,))
        return bool(cursor.rowcount)


# ===== Runtime Config =====

def get_runtime_config(key: str, default: str = "") -> str:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT value FROM runtime_config WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default


def get_all_runtime_config() -> dict[str, str]:
    with get_connection() as connection:
        rows = connection.execute("SELECT key, value FROM runtime_config").fetchall()
        return {row["key"]: row["value"] for row in rows}


def set_runtime_config(key: str, value: str) -> None:
    from datetime import datetime
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO runtime_config (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, now),
        )
