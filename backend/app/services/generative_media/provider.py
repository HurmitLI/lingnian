from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MediaCapability:
    generation_type: str
    label: str
    available: bool
    provider_key: str | None
    requires_external_upload: bool
    requires_subject_consent: bool
    estimated_cost_cents: int | None
    unavailable_reason: str | None


def capability_catalog() -> list[MediaCapability]:
    """Return safe capability declarations without contacting a paid provider."""

    reason = "尚未选择并配置付费生成服务；当前不会上传照片、声音或故事。"
    return [
        MediaCapability(
            generation_type="photo_restore",
            label="老照片修复副本",
            available=False,
            provider_key=None,
            requires_external_upload=True,
            requires_subject_consent=False,
            estimated_cost_cents=None,
            unavailable_reason="原图会永久保留；选择修复服务、确认照片使用权和外部上传范围后，才会生成独立副本。",
        ),
        MediaCapability(
            generation_type="portrait_video",
            label="人物讲述视频",
            available=False,
            provider_key=None,
            requires_external_upload=True,
            requires_subject_consent=True,
            estimated_cost_cents=None,
            unavailable_reason=reason,
        ),
        MediaCapability(
            generation_type="scene_video",
            label="纪实故事影片",
            available=False,
            provider_key=None,
            requires_external_upload=True,
            requires_subject_consent=True,
            estimated_cost_cents=None,
            unavailable_reason=reason,
        ),
        MediaCapability(
            generation_type="voice_replica",
            label="授权声音复刻",
            available=False,
            provider_key=None,
            requires_external_upload=True,
            requires_subject_consent=True,
            estimated_cost_cents=None,
            unavailable_reason="声音复刻需要讲述者本人专项授权和独立服务配置；当前不会采集训练样本。",
        ),
    ]


def estimate_request(generation_type: str) -> tuple[str, int, str]:
    capability = next(
        (item for item in capability_catalog() if item.generation_type == generation_type),
        None,
    )
    if capability is None:
        raise ValueError("UNSUPPORTED_GENERATION_TYPE")
    return "not_configured", capability.estimated_cost_cents or 0, "awaiting_provider"
