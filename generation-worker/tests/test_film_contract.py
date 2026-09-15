import copy

import pytest

from lingnian_worker.film_contract import film_contract_digest, validate_film_contract
from lingnian_worker.models import PackageError


@pytest.fixture
def film():
    source = "她第一次独自坐火车去无锡。"
    return {
        "format": "lingnian-continuous-film", "version": 1,
        "source_text": source, "duration_seconds": 64,
        "render_bible": "The same fictional adult woman beside the same green carriage; unchanged clothing and bag.",
        "film_bible": {k: "演绎设定，未知事实不补写" for k in (
            "summary", "character", "wardrobe", "prop", "era", "location", "journey", "visual_style", "forbidden_changes")},
        "opening_state": "state-0", "reference_policy": "first_reference_then_previous_last_frame",
        "reference": {"kind": "generated_reference", "sha256": "a" * 64,
                      "usage_authorized": True, "identity_claim": "illustrative_not_verified_likeness"},
        "segments": [{"id": n, "duration_seconds": 4, "before": f"state-{n-1}", "after": f"state-{n}",
                      "action": "转身", "render_action": "Turn toward the carriage.", "camera": "固定",
                      "staging_note": "演绎动作", "source_quotes": [source]} for n in range(1, 17)],
        "narration": {"sha256": "b" * 64, "duration_seconds": 63.5,
                      "kind": "authorized_recording", "usage_authorized": True},
        "narration_cues": [{"start_seconds": i * 4, "end_seconds": min((i + 1) * 4, 63.5),
                            "text": source, "source_quote": source} for i in range(16)],
    }


def test_valid_no_photo_and_photo_paths(film):
    validate_film_contract(film)
    film["reference"].update(kind="user_photo", identity_claim="photo_reference_not_historical_footage")
    validate_film_contract(film)


@pytest.mark.parametrize("defect", ["duration", "sum", "jump", "quote", "photo", "consent", "voice",
                                   "long_voice", "short_voice", "nan", "overlap", "subtitle_tail", "bool", "type"])
def test_bad_film_is_rejected_before_gpu_submission(film, defect):
    if defect == "duration": film["duration_seconds"] = 10
    elif defect == "sum": film["segments"][0]["duration_seconds"] = 5
    elif defect == "jump": film["segments"][1]["before"] = "另一城市"
    elif defect == "quote": film["segments"][0]["source_quotes"] = ["她已经到达纺织厂"]
    elif defect == "photo": film["reference"]["identity_claim"] = "real_person"
    elif defect == "consent": film["reference"]["usage_authorized"] = False
    elif defect == "voice": film["narration"]["usage_authorized"] = False
    elif defect == "long_voice": film["narration"]["duration_seconds"] = 80
    elif defect == "short_voice": film["narration"]["duration_seconds"] = 10
    elif defect == "nan": film["narration_cues"][0]["end_seconds"] = float("nan")
    elif defect == "overlap": film["narration_cues"] *= 2
    elif defect == "subtitle_tail": film["narration_cues"].pop()
    elif defect == "bool": film["segments"][0]["id"] = True
    else: film["segments"][0] = None
    with pytest.raises(PackageError):
        validate_film_contract(film)


def test_digest_tracks_content_not_key_order_and_does_not_mutate(film):
    saved = copy.deepcopy(film)
    first = film_contract_digest(film)
    assert film == saved
    assert film_contract_digest(dict(reversed(list(film.items())))) == first
    film["segments"][0]["camera"] = "轻微侧移"
    assert film_contract_digest(film) != first
