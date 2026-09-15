from test_api_flow import create_profile, create_session


def test_text_narration_is_explicit_and_idempotent(client, db, monkeypatch):
    import app.api.narration_routes as module
    calls = []
    async def speech(text):
        calls.append(text)
        return b"ID3" + b"\0" * 200
    monkeypatch.setattr(module, 'synthesize_narration', speech)
    p = create_profile(client)
    s = create_session(client, p['id'])
    url = f"/api/v1/memory-sessions/{s['id']}/text-narration"
    payload = {'text':'这是一段虚构故事，用来验证文字配音保存。', 'external_speech_authorized':True}
    assert client.post(url,json={**payload,'external_speech_authorized':False}).status_code == 409
    assert not calls
    a = client.post(url,json=payload)
    assert a.status_code == 200, a.text
    assert a.json()['is_original'] is False
    assert 'AI合成配音' in a.json()['original_filename']
    b = client.post(url,json=payload)
    assert b.json()['id'] == a.json()['id'] and len(calls) == 1
    assert client.post(url,json={**payload,'text':'修改后的另一段虚构故事，不应覆盖配音。'}).status_code ==409
    assert len(calls) ==1
    assert client.get(f"/api/v1/memory-sessions/{s['id']}").json()['session']['status']=='AUDIO_UPLOADED'


def test_unknown_narration_does_not_resend(client, monkeypatch):
    import app.api.narration_routes as module
    calls=[]
    async def speech(text):
        calls.append(text)
        raise TimeoutError()
    monkeypatch.setattr(module,'synthesize_narration',speech)
    p=create_profile(client);s=create_session(client,p['id'])
    url=f"/api/v1/memory-sessions/{s['id']}/text-narration"
    payload={'text':'这是一段虚构故事，用来验证未知响应不重复发送。','external_speech_authorized':True}
    assert client.post(url,json=payload).status_code==503
    assert client.post(url,json=payload).status_code==409
    assert len(calls)==1


def test_narration_does_not_replace_uploaded_recording(client, monkeypatch):
    from test_api_flow import upload_test_audio
    p=create_profile(client);s=create_session(client,p['id'])
    uploaded=upload_test_audio(client,s['id'])
    response=client.post(f"/api/v1/memory-sessions/{s['id']}/text-narration",json={
        'text':'这是一段虚构故事，不能覆盖已有原始录音。','external_speech_authorized':True})
    assert response.status_code==409
    assert client.get(f"/api/v1/media-assets/{uploaded['id']}/content").status_code==200


def test_synthetic_source_enters_existing_transcription(client, monkeypatch):
    import subprocess
    import imageio_ffmpeg
    import app.api.narration_routes as module
    from test_api_flow import wav_bytes
    mp3 = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-i', 'pipe:0',
        '-f', 'mp3', 'pipe:1'], input=wav_bytes(1), capture_output=True, check=True).stdout
    async def speech(text):
        return mp3
    monkeypatch.setattr(module,'synthesize_narration',speech)
    p=create_profile(client);s=create_session(client,p['id'])
    r=client.post(f"/api/v1/memory-sessions/{s['id']}/text-narration",json={
        'text':'这是一段虚构故事，验证合成配音也可以继续识别文字。','external_speech_authorized':True})
    assert r.status_code==200,r.text
    task=client.post(f"/api/v1/memory-sessions/{s['id']}/transcription-tasks")
    assert task.status_code==202,task.text
    detail=client.get(f"/api/v1/memory-sessions/{s['id']}").json()
    assert detail['session']['status']=='TRANSCRIPT_REVIEW', detail
    assert detail['transcript']['raw_text']


def test_film_keeps_synthetic_source_label(client, db):
    from app.models import MediaAsset, Story
    from sqlalchemy import select
    from test_memory_experience import create_confirmed_story
    from app.services.memory.production import normalize_production_spec
    p=create_profile(client);story,_=create_confirmed_story(client,p)
    session_id=db.get(Story,story['id']).source_draft.session_id
    asset=db.scalar(select(MediaAsset).where(MediaAsset.session_id==session_id,MediaAsset.kind=='audio_original'))
    timeline_url=f"/api/v1/elder-profiles/{p['id']}/timeline"
    assert client.get(timeline_url).json()[0]['audio_is_original'] is True
    asset.is_original=False;db.commit()
    assert client.get(timeline_url).json()[0]['audio_is_original'] is False
    r=client.post(f"/api/v1/elder-profiles/{p['id']}/generative-media-requests",json={
        'story_id':story['id'],'generation_type':'scene_video','render_mode':'native_memory',
        'model_planning_authorized':True,'actor_label':'虚构测试','subject_consent':True,
        'rights_confirmed':True,'no_impersonation':True,'allow_external_upload':True,
        'target_duration_seconds':60})
    assert r.status_code==201,r.text
    spec=r.json()['production_spec']
    assert spec['source_audio_origin']=='synthetic_narration'
    assert normalize_production_spec('scene_video',spec)['voice_strategy']=='provided_synthetic_narration'
