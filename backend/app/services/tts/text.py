from __future__ import annotations

import re


_DIGIT_READINGS = str.maketrans(
    {
        "0": "零",
        "1": "一",
        "2": "二",
        "3": "三",
        "4": "四",
        "5": "五",
        "6": "六",
        "7": "七",
        "8": "八",
        "9": "九",
    }
)
_YEAR_PATTERN = re.compile(r"(?<!\d)((?:18|19|20)\d{2})\s*年")


def prepare_tts_text(text: str) -> str:
    """Prepare display text for natural Mandarin speech without rewriting its facts."""

    normalized = " ".join(text.strip().split())

    def speak_year(match: re.Match[str]) -> str:
        return f"{match.group(1).translate(_DIGIT_READINGS)}年"

    return _YEAR_PATTERN.sub(speak_year, normalized)
