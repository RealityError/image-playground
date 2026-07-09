import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

import db


@pytest.fixture()
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "service.db")
    db.init_db()
    return db


def test_provider_profile_masks_api_key(temp_db) -> None:
    profile = temp_db.upsert_provider_profile(
        {
            "id": "openai-main",
            "name": "OpenAI 主线路",
            "provider_type": "openai-compatible",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-test-secret-1234",
            "enabled": True,
            "default_model": "gpt-image-2",
            "models": ["gpt-image-2"],
            "parameters": {"quality": ["auto", "high"], "size": ["auto", "1024x1024"]},
        },
        updated_at="2026-07-09T12:00:00",
    )

    assert profile["api_key_configured"] is True
    assert profile["api_key_preview"] == "...1234"
    assert "api_key" not in profile
    assert "sk-test-secret-1234" not in json.dumps(profile, ensure_ascii=False)


def test_provider_profile_keeps_existing_api_key_when_omitted(temp_db) -> None:
    temp_db.upsert_provider_profile(
        {
            "id": "openai-main",
            "name": "OpenAI 主线路",
            "provider_type": "openai-compatible",
            "base_url": "",
            "api_key": "sk-first-0001",
            "enabled": True,
            "default_model": "gpt-image-2",
            "models": ["gpt-image-2"],
            "parameters": {},
        },
        updated_at="2026-07-09T12:00:00",
    )

    profile = temp_db.upsert_provider_profile(
        {
            "id": "openai-main",
            "name": "OpenAI 新名称",
            "provider_type": "openai-compatible",
            "base_url": "",
            "enabled": False,
            "default_model": "gpt-image-2",
            "models": ["gpt-image-2"],
            "parameters": {},
        },
        updated_at="2026-07-09T12:01:00",
    )

    raw = temp_db.get_provider_profile_secret("openai-main")
    assert raw and raw["api_key"] == "sk-first-0001"
    assert profile["api_key_preview"] == "...0001"
    assert profile["name"] == "OpenAI 新名称"
    assert profile["enabled"] is False


def test_log_generation_started_records_provider_snapshot(temp_db) -> None:
    temp_db.log_generation_started(
        job_id="job-provider",
        created_at="2026-07-09T12:00:00",
        scope="web",
        route="/web/generate",
        client_ip="127.0.0.1",
        user_agent="pytest",
        owner_type="passphrase",
        owner_id="owner",
        prompt="hello",
        model="gpt-image-2",
        operation="generate",
        request_params_json="{}",
        input_image_count=0,
        mask_used=False,
        provider_id="openai-main",
        provider_name_snapshot="OpenAI 主线路",
        provider_type="openai-compatible",
    )

    with sqlite3.connect(temp_db.DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT provider_id, provider_name_snapshot, provider_type, model FROM generation_requests WHERE job_id = ?",
            ("job-provider",),
        ).fetchone()

    assert row["provider_id"] == "openai-main"
    assert row["provider_name_snapshot"] == "OpenAI 主线路"
    assert row["provider_type"] == "openai-compatible"
    assert row["model"] == "gpt-image-2"


def test_provider_parameter_validation_rejects_unsupported_values() -> None:
    from providers import ProviderProfile, build_provider_request

    profile = ProviderProfile(
        id="strict",
        name="严格线路",
        provider_type="openai-compatible",
        base_url="",
        api_key="sk-test",
        enabled=True,
        default_model="gpt-image-2",
        models=["gpt-image-2"],
        parameters={"quality": ["auto", "high"], "size": ["auto", "1024x1024"]},
    )

    with pytest.raises(HTTPException) as exc:
        build_provider_request(profile, {"prompt": "hello", "quality": "medium", "size": "1024x1024"})

    assert exc.value.status_code == 400
    assert "quality" in str(exc.value.detail)
