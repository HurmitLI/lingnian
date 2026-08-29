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
    renamed = client.patch(
        f"/api/v1/people/{child.json()['id']}",
        json={"display_name": "测试女儿（已修改）"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["display_name"] == "测试女儿（已修改）"
    deleted_relationship = client.delete(
        f"/api/v1/relationships/{relationship.json()['id']}"
    )
    assert deleted_relationship.status_code == 204
    deleted_person = client.delete(f"/api/v1/people/{child.json()['id']}")
    assert deleted_person.status_code == 204
    blocked_elder_delete = client.delete(f"/api/v1/people/{elder_person_id}")
    assert blocked_elder_delete.status_code == 409
    assert len(
        db.scalars(
            select(Person).where(Person.family_id == profile["family_id"])
        ).all()
    ) == 1


def test_elder_profile_can_be_added_to_family_and_edited(client):
    profile = create_profile(client)
    second = client.post(
        "/api/v1/elder-profiles",
        json={
            "family_id": profile["family_id"],
            "display_name": "测试外公",
            "preferred_name": "外公",
        },
    )
    assert second.status_code == 201

    updated = client.patch(
        f"/api/v1/elder-profiles/{second.json()['id']}",
        json={
            "display_name": "测试爷爷",
            "preferred_name": "爷爷",
            "birth_year": 1946,
            "native_place": "测试故乡",
            "occupation_summary": "测试职业",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["display_name"] == "测试爷爷"
    assert updated.json()["preferred_name"] == "爷爷"
    assert updated.json()["birth_year"] == 1946
    assert updated.json()["native_place"] == "测试故乡"

    listed = client.get("/api/v1/elder-profiles")
    assert listed.status_code == 200
    assert {item["id"] for item in listed.json()} >= {profile["id"], second.json()["id"]}


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


def test_recent_memory_sessions_support_refresh_recovery(client):
    profile = create_profile(client)
    first = create_session(client, profile["id"])
    second = create_session(client, profile["id"])

    listed = client.get(
        f"/api/v1/elder-profiles/{profile['id']}/memory-sessions?limit=1"
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [second["id"]]
    assert listed.json()[0]["question_text"] == second["question_text"]

    invalid_limit = client.get(
        f"/api/v1/elder-profiles/{profile['id']}/memory-sessions?limit=0"
    )
    assert invalid_limit.status_code == 422

    missing = client.get(
        "/api/v1/elder-profiles/00000000-0000-0000-0000-000000000000/memory-sessions"
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "ELDER_NOT_FOUND"

    all_sessions = client.get(
        f"/api/v1/elder-profiles/{profile['id']}/memory-sessions"
    ).json()
    assert {item["id"] for item in all_sessions} == {first["id"], second["id"]}


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
