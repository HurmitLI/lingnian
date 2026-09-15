"""Real HTTP + encrypted assets, synthetic fixtures; never invokes a model or GPU."""
from sqlalchemy import select, func
import pytest
from app.api.routes import protect_values
from app.main import app
from app.models import FamilyArchive, InterviewTurn, ShortSceneJob
from app.services.auth import AuthContext, current_auth
from app.services.security import get_secret_store
from test_short_scene_binding import source  # noqa
from test_short_scene_bundle import bundle_args  # noqa
from test_short_scene_export import export_case  # noqa
from test_short_scene_jobs import queued_case, claim, headers  # noqa
from test_short_reference_results import setup, upload


def ready(client,s,mode="illustrative"):
    task,image,report=setup(client,s,mode)
    assert upload(client,task,image,report).status_code==200
    base=f"/api/v1/short-reference-jobs/{task['id']}"
    review=client.get(base+"/review-input").json()
    payload={"review_input_sha256":review["review_input_sha256"],"decision":"accepted",
        "checks":{k:True for k in review["required_checks"]}}
    video={**s.consent,"input_sha256":review["selection_input_sha256"],"review_input_sha256":review["review_input_sha256"]}
    return base,review,payload,video


@pytest.mark.parametrize("mode",["illustrative","user_photo"])
def test_review_to_encrypted_video_and_resume_is_source_bound(client,db,queued_case,mode):
    s=queued_case;base,review,payload,video=ready(client,s,mode)
    assert client.post(base+"/video-job",json=video).status_code==409
    assert client.post(base+"/review",json={**payload,"checks":{}}).status_code==409
    assert client.post(base+"/review",json=payload).status_code==200
    made=client.post(base+"/video-job",json=video); assert made.status_code==200,made.text
    again=client.post(base+"/video-job",json={**video,"idempotency_key":"other-tab-0001"})
    assert again.json()["id"]==made.json()["id"]
    assert db.scalar(select(func.count()).select_from(ShortSceneJob))==1
    task=claim(client).json();assert task["id"]==made.json()["id"]
    authurl=task["package_url"].replace("/package","/input-authorization")
    grant=client.get(authurl,headers=headers(task));assert grant.status_code==200,grant.text
    assert grant.json()["decision"]=="accepted" and grant.json()["image_sha256"]==review["image_sha256"]
    assert grant.json()["audio_sha256"]==review["audio_sha256"] and not grant.json()["full_playback_accepted"]
    assert client.get(task["package_url"],headers=headers(task)).status_code==200
    client.patch(task["package_url"].replace("/package","/progress"),headers=headers(task),json={"stage":"awaiting_input_review","percent":5})
    assert client.post(f"/api/v1/short-scene-jobs/{task['id']}/resume",json={"authorize_resume":True}).status_code==200
    resumed=claim(client).json(); assert resumed["id"]==task["id"] and resumed["package_sha256"]==task["package_sha256"]
    store=app.dependency_overrides[get_secret_store]()
    protect_values(db,db.get(FamilyArchive,s.family_id),db.get(InterviewTurn,"turn"),{"question_text":"changed question"},store);db.commit()
    assert client.get(authurl,headers=headers(resumed)).status_code==409
    assert client.post(base+"/review",json=payload).status_code==409
    assert client.post(base+"/video-job",json=video).status_code==409


def test_rejection_and_family_scope_cannot_grant_video(client,queued_case):
    base,review,payload,video=ready(client,queued_case)
    assert client.post(base+"/review",json={**payload,"decision":"rejected","checks":{}}).status_code==200
    assert client.post(base+"/video-job",json=video).status_code==409
    token=current_auth.set(AuthContext("other-user","other-family","owner"))
    try:
        assert client.post(base+"/review",json=payload).status_code==404
        assert client.post(base+"/video-job",json=video).status_code==404
    finally:current_auth.reset(token)


def test_claim_response_loss_recovers_same_video_lease_without_second_job(client,queued_case):
    base,review,payload,video=ready(client,queued_case)
    client.post(base+"/review",json=payload)
    client.post(base+"/video-job",json=video)
    original=claim(client,protocol="native-short-scene-v1",request_key="persisted-claim-key").json()
    replay=claim(client,protocol="native-short-scene-v1",request_key="persisted-claim-key").json()
    assert original==replay
    assert claim(client,protocol="native-short-scene-v1",request_key="another-claim-key").json() is None


def test_reference_claim_response_loss_recovers_original_lease(client,queued_case):
    from test_short_scene_reference_jobs import prepare,submit
    from test_short_scene_jobs import TOKEN
    submit(client,prepare(client,queued_case))
    url="/api/v1/generation-worker/short-reference/tasks/claim"
    args={"json":{"protocol":"short-reference-v1","request_key":"saved-reference-claim"},"headers":{"Authorization":f"Bearer {TOKEN}"}}
    first=client.post(url,**args);second=client.post(url,**args)
    assert first.status_code==second.status_code==200
    assert first.json()==second.json() and first.json()["id"]
