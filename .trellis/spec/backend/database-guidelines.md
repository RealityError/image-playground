# Database Guidelines

> Database patterns and conventions for this project.

---

## Overview

<!--
Document your project's database conventions here.

Questions to answer:
- What ORM/query library do you use?
- How are migrations managed?
- What are the naming conventions for tables/columns?
- How do you handle transactions?
-->

(To be filled by the team)

---

## Query Patterns

<!-- How should queries be written? Batch operations? -->

(To be filled by the team)

---

## Migrations

<!-- How to create and run migrations -->

### Scenario: Portable storage paths

#### 1. Scope / Trigger

- Trigger: Any backend code that persists generated image, thumbnail, upload, or other project-local file paths.
- Reason: SQLite databases are expected to move with the project directory. Machine-specific absolute paths break image loading after migration.

#### 2. Signatures

- DB fields:
  - `generation_images.saved_path`
  - `generation_images.thumbnail_path`
  - `input_images.saved_path`
- Helper module:
  - `storage_path_for_db(path, allowed_roots) -> str`
  - `resolve_storage_path(value, allowed_roots) -> Path | None`
  - `normalize_storage_path(value, allowed_roots) -> str | None`

#### 3. Contracts

- Store paths as storage-root-relative strings, for example:
  - `generated/<filename>.png`
  - `thumbnails/<job_id>_<index>.webp`
  - `uploads/<filename>.png`
- Resolve paths at runtime against the current `backend/` directory.
- Keep path values constrained to known roots: `generated`, `thumbnails`, and `uploads`.
- Startup migration may normalize legacy absolute paths only when they point into a known storage root.

#### 4. Validation & Error Matrix

- Empty path -> route returns 404.
- Path outside allowed storage roots -> route returns 403 or skips deletion.
- Path inside allowed root but missing on disk -> route returns 404.
- Legacy absolute path inside a known storage root -> normalize to relative storage path.

#### 5. Good/Base/Bad Cases

- Good: `generated/20260529_abc_1.png` resolves after moving the project directory.
- Base: `D:\project\gpt-image-playground\backend\generated\20260529_abc_1.png` is normalized to `generated/20260529_abc_1.png`.
- Bad: `C:\outside\secret.png` is not normalized and must not be served.

#### 6. Tests Required

- Assert new persisted image paths are relative.
- Assert legacy absolute paths under known roots normalize to relative paths.
- Assert path traversal values such as `../outside.png` are rejected.
- Assert at least one authenticated image/thumbnail route serves an existing relative-path record.

#### 7. Wrong vs Correct

Wrong:

```python
save_input_image(saved_path=str(dest))
```

Correct:

```python
save_input_image(saved_path=storage_path_for_db(dest, {"generated"}))
```

---

## Naming Conventions

<!-- Table names, column names, index names -->

(To be filled by the team)

---

## Common Mistakes

<!-- Database-related mistakes your team has made -->

### Common Mistake: Persisting absolute local paths

**Symptom**: Existing gallery records stop loading after the repository is moved to another directory or drive.

**Cause**: The database stores machine-specific absolute paths instead of portable storage keys.

**Fix**: Normalize fields through `storage_path_for_db()` before saving and `resolve_storage_path()` before reading.

**Prevention**: Before adding any file path column, decide whether the path is project-local. If yes, store a root-relative storage path, not an absolute path.
