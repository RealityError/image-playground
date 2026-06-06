from app import build_upstream_image_params, map_upstream_quality


def test_nowcoding_thinking_quality_mapping() -> None:
    assert map_upstream_quality("auto", "thinking") is None
    assert map_upstream_quality("low", "thinking") == "low"
    assert map_upstream_quality("medium", "thinking") == "medium"
    assert map_upstream_quality("high", "thinking") == "high"
    assert map_upstream_quality("standard", "thinking") == "medium"
    assert map_upstream_quality("hd", "thinking") == "xhigh"
    assert map_upstream_quality("xhigh", "thinking") == "xhigh"


def test_explicit_legacy_thinking_value_is_normalized() -> None:
    params = build_upstream_image_params(
        {
            "model": "gpt-image-2",
            "prompt": "probe",
            "quality": "low",
            "thinking": "hd",
            "response_format": "b64_json",
        }
    )

    assert params == {
        "model": "gpt-image-2",
        "prompt": "probe",
        "response_format": "b64_json",
        "extra_body": {"thinking": "xhigh"},
    }
