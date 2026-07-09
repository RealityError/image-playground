import importlib
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

import db


@pytest.fixture()
def app_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OWNER_SECRET", "owner-secret-for-tests")
    monkeypatch.setenv("COOKIE_SIGNING_SECRET", "cookie-secret-for-tests")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-password-for-tests")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "service.db")
    sys.modules.pop("app", None)
    module = importlib.import_module("app")
    return module


def test_build_generate_params_uses_selected_provider_model(app_module) -> None:
    db.upsert_provider_profile(
        {
            "id": "strict",
            "name": "严格上游",
            "provider_type": "openai-compatible",
            "base_url": "",
            "api_key": "sk-strict",
            "enabled": True,
            "default_model": "gpt-image-2",
            "models": ["gpt-image-2", "custom-image"],
            "parameters": {"size": ["1024x1024"], "quality": ["high"]},
        },
        updated_at="2026-07-09T12:00:00",
    )
    db.upsert_model_profile(
        {
            "id": "strict-custom-image",
            "provider_id": "strict",
            "model": "custom-image",
            "name": "Custom Image",
            "enabled": True,
            "default": True,
            "parameter_template": "custom",
            "parameters": {"size": ["1024x1024"], "quality": ["high"]},
        },
        updated_at="2026-07-09T12:01:00",
    )

    prompt, request_params = app_module.build_generate_params(
        app_module.GenerateRequest(
            prompt="hello",
            model_profile_id="strict-custom-image",
            size="1024x1024",
            quality="high",
        )
    )

    assert prompt == "hello"
    public_params = app_module.public_request_params(request_params)
    assert public_params["model"] == "custom-image"
    assert public_params["provider_id"] == "strict"
    assert public_params["provider_name"] == "严格上游"
    assert public_params["model_profile_id"] == "strict-custom-image"
    assert public_params["parameter_template"] == "custom"


def test_build_generate_params_rejects_unsupported_provider_parameter(app_module) -> None:
    db.upsert_provider_profile(
        {
            "id": "strict",
            "name": "严格上游",
            "provider_type": "openai-compatible",
            "base_url": "",
            "api_key": "sk-strict",
            "enabled": True,
            "default_model": "gpt-image-2",
            "models": ["gpt-image-2"],
            "parameters": {"size": ["1024x1024"]},
        },
        updated_at="2026-07-09T12:00:00",
    )
    db.upsert_model_profile(
        {
            "id": "strict-gpt-image-2",
            "provider_id": "strict",
            "model": "gpt-image-2",
            "name": "GPT Image 2",
            "enabled": True,
            "default": True,
            "parameter_template": "custom",
            "parameters": {"size": ["1024x1024"]},
        },
        updated_at="2026-07-09T12:01:00",
    )

    with pytest.raises(HTTPException) as exc:
        app_module.build_generate_params(
            app_module.GenerateRequest(
                prompt="hello",
                model_profile_id="strict-gpt-image-2",
                size="1024x1024",
                quality="high",
            )
        )

    assert exc.value.status_code == 400
    assert "quality" in str(exc.value.detail)


def test_default_provider_requires_backend_managed_provider(app_module) -> None:
    db.upsert_provider_profile(
        {
            "id": "empty",
            "name": "未配置上游",
            "provider_type": "openai-compatible",
            "base_url": "",
            "enabled": True,
            "default_model": "custom-image",
            "models": ["custom-image"],
            "parameters": {"quality": ["high"]},
        },
        updated_at="2026-07-09T12:00:00",
    )

    with pytest.raises(HTTPException) as exc:
        app_module.build_generate_params(
            app_module.GenerateRequest(prompt="hello", quality="medium")
        )

    assert exc.value.status_code == 500
    assert "No enabled model" in str(exc.value.detail)
