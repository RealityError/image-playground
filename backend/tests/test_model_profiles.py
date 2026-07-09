from pathlib import Path

import pytest
from fastapi import HTTPException

import db


@pytest.fixture()
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "service.db")
    db.init_db()
    return db


def test_model_profile_binds_upstream_and_masks_upstream_key(temp_db) -> None:
    temp_db.upsert_provider_profile(
        {
            "id": "toltol",
            "name": "Toltol 上游",
            "provider_type": "openai-compatible",
            "base_url": "https://toltol.me/v1",
            "api_key": "sk-secret-ad19",
            "enabled": True,
            "default_model": "gpt-image-2",
            "models": ["gpt-image-2"],
            "parameters": {},
        },
        updated_at="2026-07-09T12:00:00",
    )

    profile = temp_db.upsert_model_profile(
        {
            "id": "toltol-gpt-image-2",
            "provider_id": "toltol",
            "model": "gpt-image-2",
            "name": "GPT Image 2",
            "enabled": True,
            "default": True,
            "parameter_template": "openai-gpt-image",
            "parameters": {"size": ["auto", "1024x1024"], "quality": ["auto", "high"]},
        },
        updated_at="2026-07-09T12:01:00",
    )

    assert profile["provider_id"] == "toltol"
    assert profile["model"] == "gpt-image-2"
    assert profile["parameter_template"] == "openai-gpt-image"
    assert "api_key" not in profile

    public_models = temp_db.list_model_profiles(include_disabled=False)
    assert public_models[0]["provider_name"] == "Toltol 上游"
    assert public_models[0]["provider_api_key_configured"] is True
    assert "sk-secret-ad19" not in str(public_models)


def test_model_parameter_validation_uses_model_profile_template() -> None:
    from providers import ModelProfile, ProviderProfile, build_provider_request

    provider = ProviderProfile(
        id="toltol",
        name="Toltol 上游",
        provider_type="openai-compatible",
        base_url="https://toltol.me/v1",
        api_key="sk-test",
        enabled=True,
        default_model="gpt-image-2",
        models=[],
        parameters={},
    )
    model_profile = ModelProfile(
        id="toltol-gemini-flash",
        provider_id="toltol",
        provider_name="Toltol 上游",
        model="gemini-3.1-flash-image",
        name="Gemini 3.1 Flash Image",
        enabled=True,
        default=False,
        parameter_template="gemini-image",
        parameters={"size": ["1024x1024"], "response_format": ["url"]},
    )

    with pytest.raises(HTTPException) as exc:
        build_provider_request(
            provider,
            {"prompt": "hello", "model": "gemini-3.1-flash-image", "quality": "high", "size": "1024x1024"},
            model_profile=model_profile,
        )

    assert exc.value.status_code == 400
    assert "quality" in str(exc.value.detail)
