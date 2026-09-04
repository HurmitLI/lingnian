from __future__ import annotations

import re


MINIMUM_WORKER_VERSIONS: dict[str, tuple[int, int, int]] = {
    "scene_video": (2, 0, 2),
}


def format_version(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def required_worker_version(generation_type: str) -> str | None:
    version = MINIMUM_WORKER_VERSIONS.get(generation_type)
    return format_version(version) if version else None


def parse_worker_version(software_version: str | None) -> tuple[int, int, int] | None:
    if not software_version:
        return None
    match = re.search(r"(?:^|/)(\d+)\.(\d+)\.(\d+)(?:$|[-+])", software_version.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def worker_supports(software_version: str | None, generation_type: str) -> bool:
    required = MINIMUM_WORKER_VERSIONS.get(generation_type)
    if required is None:
        return True
    actual = parse_worker_version(software_version)
    return actual is not None and actual >= required


def upgrade_requirements(
    software_version: str | None,
    capabilities: list[str],
) -> dict[str, str]:
    return {
        capability: format_version(MINIMUM_WORKER_VERSIONS[capability])
        for capability in capabilities
        if capability in MINIMUM_WORKER_VERSIONS
        and not worker_supports(software_version, capability)
    }
