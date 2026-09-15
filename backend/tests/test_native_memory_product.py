import pytest
from app.services.memory.memory_film import source_segments
from app.services.memory.memory_direction_cache import prepare_direction
from app.api.generation_node_routes import WorkerProgress
from app.models import GenerativeMediaRequest
from test_api_flow import create_profile
from test_memory_experience import create_confirmed_story
from test_memory_film_contract import STORY, direction
from types import SimpleNamespace


def test_long_film_progress_supports_twelve_and_eighteen_shots():
    assert WorkerProgress(progress_percent=80, progress_stage='合成', completed_scene_count=12, total_scene_count=18).total_scene_count == 18


def test_submission_reuses_same_key_and_rejects_changed_input(client):
    profile=create_profile(client)
    story,_=create_confirmed_story(client,profile)
    path=f"/api/v1/elder-profiles/{profile['id']}/generative-media-requests"
    payload=dict(story_id=story['id'], generation_type='scene_video',actor_label='虚构测试',subject_consent=True, rights_confirmed=True,no_impersonation=True,allow_external_upload=True,render_mode='native_memory',model_planning_authorized=True,target_duration_seconds=60,idempotency_key='native-test-key-12345678')
    first=client.post(path,json=payload);assert first.status_code ==201, first.text
    second=client.post(path,json=payload);assert second.json()['id']==first.json()['id']
    conflict=client.post(path,json={**payload,'target_duration_seconds':30});assert conflict.status_code==409


def test_director_cache_reuses_received_result_but_does_not_resend_unknown(monkeypatch):
    import app.services.memory.memory_direction_cache as module
    calls=[]
    provider=SimpleNamespace(plan_memory_film=lambda system,user: (calls.append(user),direction())[1])
    monkeypatch.setattr(module,'get_llm_provider',lambda:provider)
    monkeypatch.setattr('app.api.routes.secure_value',lambda db,family,obj,field,store:getattr(obj,field))
    monkeypatch.setattr('app.api.routes.protect_values',lambda *args:None)
    request=SimpleNamespace(production_spec={'model_planning_authorized':True,'target_duration_seconds':30},elder=SimpleNamespace(person=SimpleNamespace(family=object())),production_direction={})
    db=SimpleNamespace(commit=lambda:None)
    result=prepare_direction(db,request,None,body=STORY,photo_sha256=None)
    assert prepare_direction(db,request,None,body=STORY,photo_sha256=None)==result and len(calls)==1
    request.production_direction['status']='response_unknown'
    with pytest.raises(Exception,match='不重复发送'):prepare_direction(db,request,None,body=STORY,photo_sha256=None)
    assert len(calls)==1


def test_received_invalid_direction_is_recorded_and_retry_is_bounded(monkeypatch):
    import app.services.memory.memory_direction_cache as module
    calls=[]
    bad={'characters':[], 'reference_prompt':'invalid cast reference', 'shots':[]}
    monkeypatch.setattr(module,'get_llm_provider',lambda:SimpleNamespace(plan_memory_film=lambda *args:(calls.append(1),bad)[1]))
    monkeypatch.setattr('app.api.routes.secure_value',lambda db,family,obj,field,store:getattr(obj,field))
    monkeypatch.setattr('app.api.routes.protect_values',lambda *args:None)
    request=SimpleNamespace(production_spec={'model_planning_authorized':True,'target_duration_seconds':30},elder=SimpleNamespace(person=SimpleNamespace(family=object())),production_direction={})
    db=SimpleNamespace(commit=lambda:None)
    for expected in (2,4,4):
        with pytest.raises(Exception):
            prepare_direction(db,request,None,body=STORY,photo_sha256=None)
        assert len(calls)==expected
        assert request.production_direction['received_direction']==bad
        assert request.production_direction['validation_error']


def test_native_result_rejects_missing_dynamic_shots(client, db):
    from app.models import GenerationNode
    from app.services.auth import session_token_hash
    profile=create_profile(client)
    story,_=create_confirmed_story(client,profile)
    result=client.post(f"/api/v1/elder-profiles/{profile['id']}/generative-media-requests",json=dict(story_id=story['id'],generation_type='scene_video',actor_label='虚构测试',subject_consent=True,rights_confirmed=True,no_impersonation=True,allow_external_upload=True,render_mode='native_memory',model_planning_authorized=True,target_duration_seconds=60))
    assert result.status_code==201
    token='test-native-structural-check-token-123456789'
    db.add(GenerationNode(display_name='test-native',token_hash=session_token_hash(token),capabilities=['scene_video'],status='active',software_version='lingnian-worker/2.2.1'))
    db.commit()
    headers={'Authorization':f'Bearer {token}'}
    task=client.post('/api/v1/generation-worker/tasks/claim',headers=headers).json()
    assert task['id']==result.json()['id']
    checked=client.post(f"/api/v1/generation-worker/tasks/{task['id']}/result",headers={**headers,'X-Lingnian-Lease':task['lease_token']},data=dict(rendered_scene_count='12',duration_seconds='60',width='1280',height='720',generated_context_scene_count='12',generated_video_scene_count='11',stable_visual_scene_count='1',unique_generated_visual_count='12',duplicate_visual_check_passed='true'),files={'result':('not-a-real-film.mp4',b'not-used-because-contract-rejects-first','video/mp4')})
    assert checked.status_code==409
    assert '全部独立生成' in checked.json()['error']['message']


def test_director_upgrade_reuses_same_source_plan(monkeypatch):
    from pathlib import Path
    import app.services.memory.memory_direction_cache as module
    calls=[]
    monkeypatch.setattr(module,'get_llm_provider',lambda:SimpleNamespace(plan_memory_film=lambda *args:(calls.append(1),direction())[1]))
    monkeypatch.setattr('app.api.routes.secure_value',lambda db,family,obj,field,store:getattr(obj,field))
    monkeypatch.setattr('app.api.routes.protect_values',lambda *args:None)
    request=SimpleNamespace(production_spec={'model_planning_authorized':True,'target_duration_seconds':30},elder=SimpleNamespace(person=SimpleNamespace(family=object())),production_direction={})
    db=SimpleNamespace(commit=lambda:None)
    first=prepare_direction(db,request,None,body=STORY,photo_sha256=None)
    read_text=Path.read_text
    monkeypatch.setattr(Path,'read_text',lambda path,*args,**kwargs: read_text(path,*args,**kwargs)+ ('\nNew director version.' if path.name=='memory_film_direction_v1.md' else ''))
    assert prepare_direction(db,request,None,body=STORY,photo_sha256=None)==first
    assert len(calls)==1
    with pytest.raises(Exception,match='故事或参考照片变化'):
        prepare_direction(db,request,None,body=STORY+'新的内容。',photo_sha256=None)
    assert len(calls)==1


def test_validator_repair_reuses_received_response_without_model_call(monkeypatch):
    import app.services.memory.memory_direction_cache as module
    calls=[]
    monkeypatch.setattr(module,'get_llm_provider',lambda:SimpleNamespace(plan_memory_film=lambda *args:(calls.append(1),direction())[1]))
    monkeypatch.setattr('app.api.routes.secure_value',lambda db,family,obj,field,store:getattr(obj,field))
    monkeypatch.setattr('app.api.routes.protect_values',lambda *args:None)
    request=SimpleNamespace(production_spec={'model_planning_authorized':True,'target_duration_seconds':30},elder=SimpleNamespace(person=SimpleNamespace(family=object())),production_direction={})
    db=SimpleNamespace(commit=lambda:None)
    prepare_direction(db,request,None,body=STORY,photo_sha256=None)
    raw=direction(); raw['shots'][1]['prop_owners']={'blue_bag':'none'}
    request.production_direction.update(status='invalid_direction', attempt=4, received_direction=raw)
    result=prepare_direction(db,request,None,body=STORY,photo_sha256=None)
    assert result['shots'][1]['prop_owners']=={'blue_bag':'none'}
    assert len(calls)==1
    assert request.production_direction['revalidated_received_response'] is True
