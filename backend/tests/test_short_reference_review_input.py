"""Read-only family comparison from real encrypted test packages/results."""
import pytest
from sqlalchemy import func, select

from app.api.routes import protect_values
from app.core.config import get_settings
from app.main import app
from app.models import FamilyArchive, InterviewTurn, MediaAsset, ModelConsentEvent, ShortSceneReferenceJob
from app.services.auth import AuthContext, current_auth
from app.services.security import get_secret_store
from test_short_scene_binding import source  # noqa: F401
from test_short_scene_bundle import bundle_args  # noqa: F401
from test_short_scene_export import export_case  # noqa: F401
from test_short_scene_jobs import queued_case  # noqa: F401
from test_short_scene_reference_jobs import prepare, submit
from test_short_reference_results import setup, upload


@pytest.mark.parametrize("mode",["illustrative","user_photo"])
def test_family_can_compare_original_context_and_actual_decrypted_reference(client,db,queued_case,mode):
    s=queued_case; task,image,report=setup(client,s,mode)
    assert upload(client,task,image,report).status_code==200
    events=db.scalar(select(func.count()).select_from(ModelConsentEvent))
    url=f"/api/v1/short-reference-jobs/{task['id']}/review-input"
    response=client.get(url); assert response.status_code==200,response.text
    body=response.json()
    assert body["source_current"] is True and body["review_submission_available"] is True
    assert body["visual_accepted"] is False and body["video_authorized"] is False
    assert body["brief_sha256"]==report["brief_sha256"] and body["image_sha256"]==report["image_sha256"]
    assert body["answers"] and body["candidate"] and body["scene"]["opening_state"]
    assert "subject_and_event_age" in body["required_checks"]
    assert client.get(body["reference_image_url"]).content==image
    assert client.get(url).json()["review_input_sha256"]==body["review_input_sha256"]
    assert response.headers["cache-control"]=="no-store"
    assert "lease_token" not in response.text and "relative_path" not in response.text
    if mode=="user_photo":
        original=client.get(body["source_photo_url"])
        assert original.status_code==200 and original.content==s.image
        assert original.headers["cache-control"]=="no-store"
    else:
        assert body["source_photo_url"] is None
        assert client.get(url.replace("review-input","source-photo")).status_code==404
        assert body["identity_claim"]=="illustrative_not_verified_likeness"
    assert db.scalar(select(func.count()).select_from(ModelConsentEvent))==events
    assert all(not p.exists() for p in s.roots)


def test_changed_interview_shows_stale_binding_instead_of_silently_replacing_context(client,db,queued_case):
    s=queued_case; task,image,report=setup(client,s)
    upload(client,task,image,report)
    url=f"/api/v1/short-reference-jobs/{task['id']}/review-input"
    before=client.get(url).json()
    turn=db.get(InterviewTurn,"turn")
    store=app.dependency_overrides[get_secret_store]()
    protect_values(db,db.get(FamilyArchive,s.family_id),turn,{"question_text":"已修改的问题"},store); db.commit()
    after=client.get(url).json()
    assert after["source_current"] is False
    assert after["answers"]==before["answers"] and after["review_input_sha256"]==before["review_input_sha256"]
    assert after["video_authorized"] is False


def test_other_family_cannot_read_review_or_original_photo(client,queued_case):
    task,image,report=setup(client,queued_case,"user_photo"); upload(client,task,image,report)
    token=current_auth.set(AuthContext("other-user","other-family","owner"))
    try:
        for suffix in ("review-input","source-photo"):
            assert client.get(f"/api/v1/short-reference-jobs/{task['id']}/{suffix}").status_code==404
    finally: current_auth.reset(token)


@pytest.mark.parametrize("asset_kind",["package","result"])
def test_corrupt_source_or_image_is_not_presented_as_reviewable(client,db,queued_case,asset_kind):
    s=queued_case; task,image,report=setup(client,s); upload(client,task,image,report)
    job=db.get(ShortSceneReferenceJob,task["id"])
    asset=db.get(MediaAsset,job.package_asset_id if asset_kind=="package" else job.result_asset_id)
    path=get_settings().resolved_asset_root/asset.relative_path
    path.write_bytes(b"corrupted synthetic asset")
    response=client.get(f"/api/v1/short-reference-jobs/{task['id']}/review-input")
    assert response.status_code==409 and str(path) not in response.text
    assert all(not p.exists() for p in s.roots)


def test_unfinished_reference_cannot_look_like_a_reviewable_image(client,queued_case):
    made=submit(client,prepare(client,queued_case)).json()
    assert client.get(f"/api/v1/short-reference-jobs/{made['id']}/review-input").status_code==409
