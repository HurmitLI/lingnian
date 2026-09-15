from __future__ import annotations

import io
import wave

from sqlalchemy import select

from app.api import routes
from app.main import app
from app.models import EncryptedField, InterviewTurn, MediaAsset, Transcript
from app.services.asr.provider import ASRResult
from app.services.asr.timing import source_timing
from app.services.llm.provider import MockLLMProvider
from app.services.security import InMemorySecretStore, get_secret_store
from app.services.tts.provider import SpeechResult


def wav_bytes(seconds: float = 0.15) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buffer.getvalue()


def create_subject_and_narrator(client):
    family = client.post(
        "/api/v1/families",
        json={"display_name": "测试家庭", "idempotency_key": "guided-family"},
    ).json()
    subject = client.post(
        "/api/v1/elder-profiles",
        json={
            "family_id": family["id"],
            "display_name": "外公",
            "preferred_name": "外公",
        },
    ).json()
    narrator = client.post(
        f"/api/v1/families/{family['id']}/people",
        json={"display_name": "妈妈", "role": "family_member"},
    ).json()
    return subject, narrator


def test_interview_question_audio_can_speak_acknowledgement_before_next_question(
    client, monkeypatch
):
    subject, narrator = create_subject_and_narrator(client)
    session = client.post(
        "/api/v1/memory-sessions",
        json={
            "elder_id": subject["id"],
            "narrator_person_id": narrator["id"],
            "interview_mode": "guided_voice",
            "life_stage": "童年",
        },
    ).json()
    spoken: list[str] = []

    class CapturingTTSProvider:
        def synthesize(self, text: str) -> SpeechResult:
            spoken.append(text)
            return SpeechResult(
                audio=wav_bytes(),
                provider="test",
                model="test-voice",
                voice="warm",
            )

    monkeypatch.setattr(routes, "get_tts_provider", lambda: CapturingTTSProvider())
    response = client.get(
        f"/api/v1/memory-sessions/{session['id']}/interview-question-audio",
        params={"lead_in": "我听到了，谢谢您。"},
    )
    assert response.status_code == 200, response.text
    assert spoken == [f"我听到了，谢谢您。{session['question_text']}"]


def test_local_interview_followup_avoids_repeating_asked_questions():
    provider = MockLLMProvider()
    turns = [
        {"question": question, "answer": "这是一段真实的回忆。"}
        for question in [
            "那时候家里住的地方是什么样的？",
            "这件事里，您印象最深的是谁？",
            "当时您心里是什么感受？",
            "后来这件事还影响过您吗？",
            "关于那段童年，还有哪个小细节您愿意留下？",
        ]
    ]
    result = provider.generate_interview_followup("外公", "妈妈", "童年", turns)
    assert result.next_question == "今天关于这段回忆，最后还有什么是您希望家里人以后记得的？"
    assert result.next_question not in {turn["question"] for turn in turns}


def test_guided_interview_keeps_subject_and_narrator_separate(client):
    subject, narrator = create_subject_and_narrator(client)
    created = client.post(
        "/api/v1/memory-sessions",
        json={
            "elder_id": subject["id"],
            "narrator_person_id": narrator["id"],
            "interview_mode": "guided_voice",
            "life_stage": "童年",
        },
    )
    assert created.status_code == 201, created.text
    session = created.json()
    assert session["status"] == "INTERVIEWING"
    assert session["narrator_person_id"] == narrator["id"]

    uploaded = client.post(
        f"/api/v1/memory-sessions/{session['id']}/interview-turns/audio",
        files={"audio": ("answer.wav", wav_bytes(), "audio/wav")},
    )
    assert uploaded.status_code == 201, uploaded.text
    turn = uploaded.json()
    assert turn["status"] == "answer_review"
    assert turn["raw_answer_text"]
    assert turn["audio_url"].startswith("/api/v1/media-assets/")
    assert turn["question_audio_url"].startswith("/api/v1/media-assets/")

    continued = client.post(
        f"/api/v1/memory-sessions/{session['id']}/interview-turns/{turn['id']}/continue",
        json={
            "corrected_answer_text": "嗯，呃，这是妈妈根据亲身经历讲述的一段测试回忆",
            "allow_cloud_followup": False,
        },
    )
    assert continued.status_code == 200, continued.text
    assert continued.json()["next_question"]
    assert continued.json()["turn"]["corrected_answer_text"] == "这是妈妈根据亲身经历讲述的一段测试回忆。"
    assert continued.json()["followup_mode"] == "test_or_local_model"

    finalized = client.post(
        f"/api/v1/memory-sessions/{session['id']}/interview-finalize"
    )
    assert finalized.status_code == 200, finalized.text
    detail = finalized.json()
    assert detail["session"]["status"] == "TRANSCRIPT_REVIEW"
    assert "妈妈：这是妈妈根据亲身经历讲述的一段测试回忆。" in detail["transcript"]["corrected_text"]
    assert any(asset["kind"] == "audio_original" for asset in detail["media_assets"])
    merged = next(asset for asset in detail["media_assets"] if asset["kind"] == "audio_original")
    merged_response = client.get(merged["content_url"])
    with wave.open(io.BytesIO(merged_response.content), "rb") as audio:
        assert audio.getnframes() / audio.getframerate() >= 0.3
        timeline = detail["transcript"]["asr_metadata"]["interview_timeline"]
        assert timeline["frames"] == audio.getnframes()
        assert timeline["segments"][-1]["asr_timing"]["status"] == "unavailable"


def test_two_turn_timeline_counts_questions_and_keeps_answer_clock_relative(client, monkeypatch):
    class TimedASR:
        def transcribe(self, path):
            return ASRResult("妈妈", "test", "test-only", {"timing": source_timing(
                path, {"text": "妈 妈", "timestamp": [[10, 50], [50, 100]]}, "funasr")})

    class QuestionTTS:
        def synthesize(self, text):
            return SpeechResult(audio=wav_bytes(0.2), provider="test", model="test", voice="test")

    monkeypatch.setattr(routes, "get_asr_provider", lambda: TimedASR())
    monkeypatch.setattr(routes, "get_tts_provider", lambda: QuestionTTS())
    subject, narrator = create_subject_and_narrator(client)
    session = client.post("/api/v1/memory-sessions", json={
        "elder_id": subject["id"], "narrator_person_id": narrator["id"],
        "interview_mode": "guided_voice", "life_stage": "童年",
    }).json()
    base = f"/api/v1/memory-sessions/{session['id']}"
    turn_ids = []
    for _ in range(2):
        uploaded = client.post(f"{base}/interview-turns/audio",
            files={"audio": ("answer.wav", wav_bytes(0.15), "audio/wav")})
        assert uploaded.status_code == 201, uploaded.text
        turn_ids.append(uploaded.json()["id"])
        continued = client.post(f"{base}/interview-turns/{turn_ids[-1]}/continue",
            json={"corrected_answer_text": "妈妈。", "allow_cloud_followup": False})
        assert continued.status_code == 200, continued.text
    finalized = client.post(f"{base}/interview-finalize")
    assert finalized.status_code == 200, finalized.text
    detail = finalized.json()
    timeline = detail["transcript"]["asr_metadata"]["interview_timeline"]
    segments = timeline["segments"]
    assert [s["role"] for s in segments] == ["question", "answer", "question", "answer"]
    assert [s["turn_id"] for s in segments] == [turn_ids[0]] * 2 + [turn_ids[1]] * 2
    assert [s["start_frame"] for s in segments] == [0, 3200, 5600, 8800]
    assert timeline["frames"] == 11200
    for index in (1, 3):
        assert segments[index]["asr_timing"]["items"][0]["start_ms"] == 10
    asset = next(a for a in detail["media_assets"] if a["kind"] == "audio_original")
    with wave.open(io.BytesIO(client.get(asset["content_url"]).content), "rb") as audio:
        assert audio.getnframes() == timeline["frames"]


def test_family_recollection_keeps_narrator_provenance_in_archive_and_book(client):
    subject, narrator = create_subject_and_narrator(client)
    session = client.post(
        "/api/v1/memory-sessions",
        json={
            "elder_id": subject["id"],
            "narrator_person_id": narrator["id"],
            "interview_mode": "guided_voice",
            "life_stage": "童年",
        },
    ).json()
    uploaded = client.post(
        f"/api/v1/memory-sessions/{session['id']}/interview-turns/audio",
        files={"audio": ("answer.wav", wav_bytes(), "audio/wav")},
    ).json()
    answer = "外公小时候常在村口的大树下等家人回家。"
    assert client.post(
        f"/api/v1/memory-sessions/{session['id']}/interview-turns/{uploaded['id']}/continue",
        json={"corrected_answer_text": answer, "allow_cloud_followup": False},
    ).status_code == 200
    assert client.post(
        f"/api/v1/memory-sessions/{session['id']}/interview-finalize"
    ).status_code == 200
    organized = client.post(
        f"/api/v1/memory-sessions/{session['id']}/organization-tasks",
        json={},
    )
    assert organized.status_code == 202, organized.text
    detail = client.get(f"/api/v1/memory-sessions/{session['id']}").json()
    draft_id = detail["story_draft"]["id"]
    confirmed = client.post(
        f"/api/v1/story-drafts/{draft_id}/confirm",
        json={"confirmed_by": "妈妈"},
    )
    assert confirmed.status_code == 200, confirmed.text

    archive = client.post(
        f"/api/v1/elder-profiles/{subject['id']}/archive-questions",
        json={"question": "外公小时候在哪里等家人？"},
    )
    assert archive.status_code == 200, archive.text
    citation = archive.json()["citations"][0]
    assert citation["source_kind"] == "family_recollection"
    assert citation["source_label"] == "妈妈"

    book = client.post(
        f"/api/v1/elder-profiles/{subject['id']}/memory-books",
        json={"created_by": "测试家庭成员"},
    )
    assert book.status_code == 201, book.text
    assert book.json()["story_manifest"][0]["narrator_label"] == "妈妈"
    markdown = client.get(f"/api/v1/memory-books/{book.json()['id']}/markdown")
    assert "记忆人物：外公" in markdown.text
    assert "讲述来源：妈妈回忆讲述" in markdown.text


def test_guided_interview_rejects_narrator_from_another_family(client):
    subject, _ = create_subject_and_narrator(client)
    other_family = client.post(
        "/api/v1/families",
        json={"display_name": "另一家庭", "idempotency_key": "other-family"},
    ).json()
    outsider = client.post(
        f"/api/v1/families/{other_family['id']}/people",
        json={"display_name": "外部人员", "role": "family_member"},
    ).json()
    response = client.post(
        "/api/v1/memory-sessions",
        json={
            "elder_id": subject["id"],
            "narrator_person_id": outsider["id"],
            "interview_mode": "guided_voice",
            "life_stage": "童年",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NARRATOR_FAMILY_MISMATCH"


def test_guided_interview_remains_encrypted_for_real_family_data(client, db, monkeypatch):
    class TimedASR:
        def transcribe(self, path):
            return ASRResult("测试", "test", "test-only", {"timing": source_timing(
                path, {"text": "测 试", "timestamp": [[0, 50], [50, 100]]}, "funasr")})
    monkeypatch.setattr(routes, "get_asr_provider", lambda: TimedASR())
    store = InMemorySecretStore()
    app.dependency_overrides[get_secret_store] = lambda: store
    passphrase = "虚构采访恢复口令-长度足够-2026"
    try:
        subject, narrator = create_subject_and_narrator(client)
        family_id = subject["family_id"]
        assert client.post(
            f"/api/v1/families/{family_id}/security/initialize",
            json={"actor_label": "测试家庭成员"},
        ).status_code == 201
        package = client.post(
            f"/api/v1/families/{family_id}/security/recovery-package",
            json={
                "actor_label": "测试家庭成员",
                "recovery_passphrase": passphrase,
            },
        )
        assert package.status_code == 200
        verified = client.post(
            f"/api/v1/families/{family_id}/security/verify-recovery",
            data={"recovery_passphrase": passphrase, "actor_label": "测试家庭成员"},
            files={"package": ("recovery.json", package.content, "application/json")},
        )
        assert verified.status_code == 200
        activated = client.post(
            f"/api/v1/families/{family_id}/security/activate",
            json={
                "actor_label": "测试家庭成员",
                "data_classification": "authorized_sensitive",
            },
        )
        assert activated.status_code == 200, activated.text

        session = client.post(
            "/api/v1/memory-sessions",
            json={
                "elder_id": subject["id"],
                "narrator_person_id": narrator["id"],
                "interview_mode": "guided_voice",
                "life_stage": "童年",
            },
        ).json()
        uploaded = client.post(
            f"/api/v1/memory-sessions/{session['id']}/interview-turns/audio",
            files={"audio": ("answer.wav", wav_bytes(), "audio/wav")},
        )
        assert uploaded.status_code == 201, uploaded.text
        turn_payload = uploaded.json()
        continued = client.post(
            f"/api/v1/memory-sessions/{session['id']}/interview-turns/{turn_payload['id']}/continue",
            json={
                "corrected_answer_text": "这是一段只保存在本机的加密采访回答。",
                "allow_cloud_followup": False,
            },
        )
        assert continued.status_code == 200, continued.text
        finalized = client.post(
            f"/api/v1/memory-sessions/{session['id']}/interview-finalize"
        )
        assert finalized.status_code == 200, finalized.text
        assert "这是一段只保存在本机的加密采访回答。" in finalized.json()["transcript"]["corrected_text"]
        timeline = finalized.json()["transcript"]["asr_metadata"]["interview_timeline"]
        question_segment, answer_segment = timeline["segments"]
        assert question_segment["role"] == "question"
        assert answer_segment["start_frame"] == question_segment["end_frame"] > 0
        assert answer_segment["asr_timing"]["status"] == "available"
        assert answer_segment["asr_timing"]["items"][0]["text"] == "测"
        assert answer_segment["asr_timing"]["text_basis"] == "raw_asr"
        assert timeline["frames"] == answer_segment["end_frame"]

        db.expire_all()
        turn = db.get(InterviewTurn, turn_payload["id"])
        assert turn.corrected_answer_text == "[niannian:encrypted:v1]"
        assert turn.asr_metadata == {}
        transcript = db.scalar(select(Transcript).where(Transcript.session_id == session["id"]))
        assert transcript.asr_metadata == {}
        assert db.scalars(
            select(EncryptedField).where(EncryptedField.object_id == turn.id)
        ).all()
        turn_audio = db.get(MediaAsset, turn.audio_asset_id)
        assert turn_audio.encryption_version == 1
        question_audio = db.get(MediaAsset, turn.question_audio_asset_id)
        assert question_audio.encryption_version == 1
        merged_audio = db.scalar(
            select(MediaAsset).where(
                MediaAsset.session_id == session["id"],
                MediaAsset.kind == "audio_original",
            )
        )
        assert merged_audio is not None
        assert merged_audio.encryption_version == 1
    finally:
        app.dependency_overrides.pop(get_secret_store, None)
