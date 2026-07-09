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
INTERNAL_MODEL_PROFILE_PARAM = "_model_profile"

MODEL_PARAMETER_TEMPLATES: dict[str, dict[str, list[str]]] = {
    "openai-gpt-image": {
        "n": [],
        "size": DEFAULT_PROVIDER_PARAMETERS["size"],
        "quality": ["auto", "low", "medium", "high", "standard", "hd"],
        "response_format": ["url", "b64_json"],
        "background": ["auto", "transparent", "opaque"],
        "output_format": ["png", "jpeg", "webp"],
        "output_compression": [],
        "partial_images": [],
        "moderation": ["auto", "low"],
        "user": [],
        "image": [],
        "mask": [],
    },
    "gemini-image": {
        "n": [],
        "size": ["1024x1024", "1536x1024", "1024x1536", "2048x2048", "2048x1152", "1152x2048"],
        "response_format": ["url", "b64_json"],
        "user": [],
        "image": [],
        "mask": [],
    },
    "custom": {},
}


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
            name=str(data.get("name") or "默认上游"),
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


@dataclass(frozen=True)
class ModelProfile:
    id: str
    provider_id: str
    provider_name: str
    model: str
    name: str
    enabled: bool
    default: bool
    parameter_template: str
    parameters: dict[str, list[str]]

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "ModelProfile":
        parameters = data.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {}
        normalized_parameters: dict[str, list[str]] = {}
        for key, values in parameters.items():
            if isinstance(values, list):
                normalized_parameters[str(key)] = [str(value) for value in values]
            elif values is True:
                normalized_parameters[str(key)] = []
        model = str(data.get("model") or "")
        return cls(
            id=str(data.get("id") or model),
            provider_id=str(data.get("provider_id") or ""),
            provider_name=str(data.get("provider_name") or ""),
            model=model,
            name=str(data.get("name") or model),
            enabled=bool(data.get("enabled", True)),
            default=bool(data.get("default", False)),
            parameter_template=str(data.get("parameter_template") or "custom"),
            parameters=normalized_parameters,
        )

    def public_snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider_id": self.provider_id,
            "provider_name": self.provider_name,
            "model": self.model,
            "name": self.name,
            "default": self.default,
            "parameter_template": self.parameter_template,
            "parameters": merged_model_parameters(self),
        }


def merged_model_parameters(model_profile: ModelProfile | None) -> dict[str, list[str]]:
    if model_profile is None:
        return {}
    merged = dict(MODEL_PARAMETER_TEMPLATES.get(model_profile.parameter_template, {}))
    merged.update(model_profile.parameters or {})
    return merged


def build_provider_request(
    profile: ProviderProfile,
    request_params: dict[str, Any],
    *,
    model_profile: ModelProfile | None = None,
) -> dict[str, Any]:
    if not profile.enabled:
        raise HTTPException(status_code=400, detail=f"Provider is disabled: {profile.id}.")
    if profile.provider_type != "openai-compatible":
        raise HTTPException(status_code=400, detail=f"Unsupported provider_type: {profile.provider_type}.")
    if model_profile is not None:
        if not model_profile.enabled:
            raise HTTPException(status_code=400, detail=f"Model is disabled: {model_profile.id}.")
        if model_profile.provider_id != profile.id:
            raise HTTPException(status_code=400, detail=f"Model {model_profile.id} does not belong to provider {profile.id}.")

    params = dict(request_params)
    default_model = model_profile.model if model_profile is not None else profile.default_model
    requested_model = str(params.get("model") or default_model).strip()
    if not requested_model:
        raise HTTPException(status_code=400, detail="model is required.")
    if model_profile is not None and requested_model != model_profile.model:
        raise HTTPException(status_code=400, detail=f"Unsupported model for profile {model_profile.id}: {requested_model}.")
    if model_profile is None and profile.models and requested_model not in profile.models:
        raise HTTPException(status_code=400, detail=f"Unsupported model for provider {profile.id}: {requested_model}.")
    params["model"] = requested_model

    supported = merged_model_parameters(model_profile) if model_profile is not None else profile.parameters or {}
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
    if model_profile is not None:
        params[INTERNAL_MODEL_PROFILE_PARAM] = model_profile
    return params


def public_request_params(request_params: dict[str, Any]) -> dict[str, Any]:
    provider = request_params.get(INTERNAL_PROVIDER_PARAM)
    model_profile = request_params.get(INTERNAL_MODEL_PROFILE_PARAM)
    clean = {
        key: value
        for key, value in request_params.items()
        if key not in {INTERNAL_PROVIDER_PARAM, INTERNAL_MODEL_PROFILE_PARAM}
    }
    if isinstance(provider, ProviderProfile):
        clean["provider_id"] = provider.id
        clean["provider_name"] = provider.name
        clean["provider_type"] = provider.provider_type
    if isinstance(model_profile, ModelProfile):
        clean["model_profile_id"] = model_profile.id
        clean["model_name"] = model_profile.name
        clean["parameter_template"] = model_profile.parameter_template
    return clean


def upstream_request_params(request_params: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in request_params.items()
        if key not in {INTERNAL_PROVIDER_PARAM, INTERNAL_MODEL_PROFILE_PARAM}
    }


def provider_from_request_params(request_params: dict[str, Any]) -> ProviderProfile | None:
    provider = request_params.get(INTERNAL_PROVIDER_PARAM)
    return provider if isinstance(provider, ProviderProfile) else None


def model_from_request_params(request_params: dict[str, Any]) -> ModelProfile | None:
    model_profile = request_params.get(INTERNAL_MODEL_PROFILE_PARAM)
    return model_profile if isinstance(model_profile, ModelProfile) else None
