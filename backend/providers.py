from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException


DEFAULT_PROVIDER_PARAMETERS: dict[str, list[str]] = {
    "n": [],
    "size": [
        "auto",
        "1024x1024",
        "1536x1024",
        "1024x1536",
        "2048x2048",
        "2048x1152",
        "1152x2048",
        "3840x3840",
        "3840x2160",
        "2160x3840",
    ],
    "quality": ["auto", "low", "medium", "high", "standard", "hd"],
    "background": ["auto", "transparent", "opaque"],
    "output_format": ["png", "jpeg", "webp"],
    "output_compression": [],
    "partial_images": [],
    "response_format": ["url", "b64_json"],
    "moderation": ["auto", "low"],
    "style": ["vivid", "natural"],
    "user": [],
    "image": [],
    "mask": [],
}

INTERNAL_PROVIDER_PARAM = "_provider_profile"


@dataclass(frozen=True)
class ProviderProfile:
    id: str
    name: str
    provider_type: str
    base_url: str
    api_key: str
    enabled: bool
    default_model: str
    models: list[str]
    parameters: dict[str, list[str]]

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "ProviderProfile":
        models = data.get("models")
        if not isinstance(models, list):
            models = []
        parameters = data.get("parameters")
        if not isinstance(parameters, dict):
            parameters = DEFAULT_PROVIDER_PARAMETERS
        normalized_parameters: dict[str, list[str]] = {}
        for key, values in parameters.items():
            if isinstance(values, list):
                normalized_parameters[str(key)] = [str(value) for value in values]
            elif values is True:
                normalized_parameters[str(key)] = []
        return cls(
            id=str(data.get("id") or "default"),
            name=str(data.get("name") or "默认线路"),
            provider_type=str(data.get("provider_type") or "openai-compatible"),
            base_url=str(data.get("base_url") or ""),
            api_key=str(data.get("api_key") or ""),
            enabled=bool(data.get("enabled", True)),
            default_model=str(data.get("default_model") or "gpt-image-2"),
            models=[str(model) for model in models if str(model).strip()],
            parameters=normalized_parameters or dict(DEFAULT_PROVIDER_PARAMETERS),
        )

    def public_snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "provider_type": self.provider_type,
            "default_model": self.default_model,
            "models": self.models,
            "parameters": self.parameters,
        }


def build_provider_request(profile: ProviderProfile, request_params: dict[str, Any]) -> dict[str, Any]:
    if not profile.enabled:
        raise HTTPException(status_code=400, detail=f"Provider is disabled: {profile.id}.")
    if profile.provider_type != "openai-compatible":
        raise HTTPException(status_code=400, detail=f"Unsupported provider_type: {profile.provider_type}.")

    params = dict(request_params)
    requested_model = str(params.get("model") or profile.default_model).strip()
    if not requested_model:
        raise HTTPException(status_code=400, detail="model is required.")
    if profile.models and requested_model not in profile.models:
        raise HTTPException(status_code=400, detail=f"Unsupported model for provider {profile.id}: {requested_model}.")
    params["model"] = requested_model

    supported = profile.parameters or {}
    if supported:
        for key, value in params.items():
            if key in {"prompt", "model"}:
                continue
            if key not in supported:
                raise HTTPException(status_code=400, detail=f"Unsupported parameter for provider {profile.id}: {key}.")
            allowed_values = supported.get(key) or []
            if allowed_values and value not in allowed_values:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported value for provider {profile.id}: {key}={value}.",
                )

    params[INTERNAL_PROVIDER_PARAM] = profile
    return params


def public_request_params(request_params: dict[str, Any]) -> dict[str, Any]:
    provider = request_params.get(INTERNAL_PROVIDER_PARAM)
    clean = {
        key: value
        for key, value in request_params.items()
        if key != INTERNAL_PROVIDER_PARAM
    }
    if isinstance(provider, ProviderProfile):
        clean["provider_id"] = provider.id
        clean["provider_name"] = provider.name
        clean["provider_type"] = provider.provider_type
    return clean


def upstream_request_params(request_params: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in request_params.items()
        if key != INTERNAL_PROVIDER_PARAM
    }


def provider_from_request_params(request_params: dict[str, Any]) -> ProviderProfile | None:
    provider = request_params.get(INTERNAL_PROVIDER_PARAM)
    return provider if isinstance(provider, ProviderProfile) else None
