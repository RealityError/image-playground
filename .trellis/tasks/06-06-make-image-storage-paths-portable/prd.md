# Make Image Storage Paths Portable

## Goal

Store generated image, thumbnail, and uploaded input image locations in a project-portable form so the SQLite database can move with the project directory without breaking admin gallery or history image loading.

## Requirements

* New image records must store project-relative storage paths instead of machine-specific absolute paths.
* Existing absolute-path records must continue to load after the project is moved.
* Existing absolute-path records should be migrated to relative paths automatically and safely.
* Path resolution must remain constrained to the backend storage directories.
* Admin, web, and API image routes must keep their existing authorization behavior.

## Acceptance Criteria

* [ ] A database created under one project path can serve images after the project folder moves.
* [ ] Active existing records with files under `backend/generated`, `backend/thumbnails`, and `backend/uploads` resolve successfully.
* [ ] Records outside allowed storage directories are rejected.
* [ ] New generated images and thumbnails are saved to the database as relative paths.
* [ ] Backend syntax/import checks pass.

## Definition of Done

* Backend code updated and verified.
* Current local database migrated or compatible.
* Relevant quality checks run.
* Rollback path noted for local database changes.

## Technical Approach

Introduce centralized storage path helpers in `backend/app.py`:

* Convert paths under backend storage roots to relative strings before writing them to the database.
* Resolve relative paths against the current backend directory at read time.
* Normalize legacy absolute paths by extracting the storage-root-relative suffix when they point at known storage roots.
* Migrate existing SQLite path fields at startup through `db.py`, guarded to the known storage roots.

## Decision (ADR-lite)

**Context**: The current database stores absolute filesystem paths, which broke admin image loading after the project moved from `C:\Users\whitecat\Desktop\project\gpt-image-playground` to `D:\project\gpt-image-playground`.

**Decision**: Store project-relative storage paths and keep a legacy absolute-path compatibility layer.

**Consequences**: The database becomes portable across local project moves. The resolver remains responsible for enforcing storage boundaries. Very old rows whose files are genuinely missing will still fail with 404, which is correct.

## Out of Scope

* Moving images to object storage.
* Reworking thumbnail generation semantics.
* Changing frontend gallery UI.
* Recovering files that no longer exist.

## Technical Notes

* Primary files: `backend/app.py`, `backend/db.py`.
* Existing DB fields: `generation_images.saved_path`, `generation_images.thumbnail_path`, `input_images.saved_path`.
* Existing allowed roots: `backend/generated`, `backend/thumbnails`, `backend/uploads`.
