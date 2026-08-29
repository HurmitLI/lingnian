from __future__ import annotations

from app.core.errors import DomainError


TRANSITIONS: dict[str, set[str]] = {
    "PROMPT_READY": {"RECORDING_PENDING", "AUDIO_UPLOADED", "SKIPPED"},
    "RECORDING_PENDING": {"AUDIO_UPLOADED", "SKIPPED"},
    "AUDIO_UPLOADED": {"TRANSCRIBING", "SKIPPED"},
    "TRANSCRIBING": {"TRANSCRIPT_REVIEW", "FAILED_RETRYABLE", "SKIPPED"},
    "TRANSCRIPT_REVIEW": {"ORGANIZING", "SKIPPED"},
    "ORGANIZING": {"DRAFT_REVIEW", "FAILED_RETRYABLE", "SKIPPED"},
    "DRAFT_REVIEW": {"TRANSCRIPT_REVIEW", "CONFIRMED", "SKIPPED"},
    "CONFIRMED": {"ARCHIVED"},
    "FAILED_RETRYABLE": {"TRANSCRIBING", "ORGANIZING", "SKIPPED"},
    "ARCHIVED": set(),
    "SKIPPED": set(),
}


def transition(current: str, target: str) -> str:
    if target not in TRANSITIONS.get(current, set()):
        raise DomainError(
            "INVALID_STATE_TRANSITION",
            f"当前状态不能执行这个操作（{current} → {target}）。",
            409,
        )
    return target

