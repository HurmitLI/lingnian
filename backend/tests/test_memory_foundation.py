from __future__ import annotations

from sqlalchemy import select

from app.models import MemoryFact, Person, QuestionPrompt
from test_api_flow import create_profile, create_session, upload_test_audio


def test_family_people_and_confirmed_relationships(client, db):
    profile = create_profile(client)
    elder_person_id = profile["person_id"]
    child = client.post(
        f"/api/v1/families/{profile['family_id']}/people",
        json={"display_name": "测试女儿", "role": "family_member"},
    )
    assert child.status_code == 201

    relationship = client.post(
        f"/api/v1/families/{profile['family_id']}/relationships",
        json={
            "from_person_id": child.json()["id"],
            "to_person_id": elder_person_id,
            "relationship_type": "child",
            "confirmed_by": "测试家庭管理员",
        },
    )
    assert relationship.status_code == 201
    repeated = client.post(
        f"/api/v1/families/{profile['family_id']}/relationships",
        json={
            "from_person_id": child.json()["id"],
            "to_person_id": elder_person_id,
            "relationship_type": "child",
            "confirmed_by": "测试家庭管理员",
        },
    )
    assert repeated.status_code == 201
    assert repeated.json()["id"] == relationship.json()["id"]

    listed = client.get(
        f"/api/v1/families/{profile['family_id']}/relationships"
    ).json()
    assert len(listed) == 1
    assert len(
        db.scalars(
            select(Person).where(Person.family_id == profile["family_id"])
        ).all()
    ) == 2


def test_question_bank_rotates_and_avoid_preference_blocks_topic(client, db):
    profile = create_profile(client)
    first = create_session(client, profile["id"])
    second = create_session(client, profile["id"])
    assert first["prompt_id"] != second["prompt_id"]
    assert first["question_text"] != second["question_text"]
    assert db.scalar(select(QuestionPrompt).where(QuestionPrompt.life_stage == "童年"))

    preference = client.put(
        f"/api/v1/elder-profiles/{profile['id']}/topic-preferences/童年",
        json={
            "topic_key": "童年",
            "preference": "avoid",
            "note": "讲述者表示不想再问",
            "updated_by": "测试家庭管理员",
        },
    )
    assert preference.status_code == 200
    blocked = client.post(
        "/api/v1/memory-sessions",
        json={"elder_id": profile["id"], "life_stage": "童年"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "TOPIC_BLOCKED_BY_PREFERENCE"

    client.put(
        f"/api/v1/elder-profiles/{profile['id']}/topic-preferences/童年",
        json={
            "topic_key": "童年",
            "preference": "ask_first",
            "updated_by": "测试家庭管理员",
        },
    )
    unconfirmed = client.post(
        "/api/v1/memory-sessions",
        json={"elder_id": profile["id"], "life_stage": "童年"},
    )
    assert unconfirmed.status_code == 409
    assert unconfirmed.json()["error"]["code"] == "TOPIC_CONFIRMATION_REQUIRED"
    confirmed = client.post(
        "/api/v1/memory-sessions",
        json={
            "elder_id": profile["id"],
            "life_stage": "童年",
            "topic_confirmed": True,
        },
    )
    assert confirmed.status_code == 201


def test_only_confirmed_story_enters_long_term_memory(client, db):
    profile = create_profile(client)
    session = create_session(client, profile["id"])
    upload_test_audio(client, session["id"])
    transcribed = client.post(
        f"/api/v1/memory-sessions/{session['id']}/transcription-tasks"
    )
    assert transcribed.status_code == 202
    detail = client.get(f"/api/v1/memory-sessions/{session['id']}").json()
    corrected = "这是经过人工确认后才能进入长期记忆的虚构故事。"
    client.patch(
        f"/api/v1/transcripts/{detail['transcript']['id']}",
        json={"corrected_text": corrected},
    )
    organized = client.post(
        f"/api/v1/memory-sessions/{session['id']}/organization-tasks"
    )
    assert organized.status_code == 202
    assert db.scalars(select(MemoryFact)).all() == []

    detail = client.get(f"/api/v1/memory-sessions/{session['id']}").json()
    confirmed = client.post(
        f"/api/v1/story-drafts/{detail['story_draft']['id']}/confirm",
        json={"confirmed_by": "测试家庭成员"},
    )
    assert confirmed.status_code == 200
    facts = db.scalars(select(MemoryFact)).all()
    assert len(facts) == 1
    assert facts[0].value_text == corrected
    assert facts[0].confidence == "confirmed"

    context = client.get(
        f"/api/v1/elder-profiles/{profile['id']}/memory-context"
    )
    assert context.status_code == 200
    assert context.json()["confirmed_facts"][0]["story_id"] == confirmed.json()["id"]
    assert context.json()["coverage"] == [
        {"life_stage": "童年", "session_count": 1, "confirmed_story_count": 1}
    ]
