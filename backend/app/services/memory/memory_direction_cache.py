"""Persist one consent-bound director result before any GPU work begins."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.core.errors import DomainError
from app.services.llm import get_llm_provider
from .memory_film import planning_input, validate_direction


def prepare_direction(db, request, store, *, body: str, photo_sha256: str | None, recording: dict | None = None) -> dict:
    from app.api.routes import protect_values, secure_value
    spec = request.production_spec or {}
    if not spec.get('model_planning_authorized'):
        raise DomainError('MEMORY_PLANNING_CONSENT_REQUIRED', '请先同意将本次故事发送给已配置的文字模型整理分镜。', 409)
    system = (Path(__file__).resolve().parents[2] / 'prompts/memory_film_direction_v1.md').read_text()
    if recording: body = recording['body']
    slots = recording['slots'] if recording else None
    user = planning_input(body, spec['target_duration_seconds'], has_photo=bool(photo_sha256), slots=slots)
    binding = hashlib.sha256(json.dumps([system,user,photo_sha256,spec,recording],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    source_binding = hashlib.sha256(json.dumps([user,photo_sha256,spec,recording],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    family = request.elder.person.family
    saved = secure_value(db, family, request, 'production_direction', store) or {}
    if saved:
        if saved.get('source_input_sha256'):
            matches = saved['source_input_sha256'] == source_binding
        else:
            # Read-only compatibility for jobs accepted before source and
            # director-version bindings were separated. A deployment must not
            # regenerate an already received plan for unchanged user inputs.
            initial_system = (Path(__file__).resolve().parents[2] / 'prompts/memory_film_direction_initial_v1.md').read_text(encoding='utf-8')
            initial_binding = hashlib.sha256(json.dumps([initial_system,user,photo_sha256,spec,recording],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            matches = saved.get('input_sha256') in {binding, initial_binding}
        if not matches:
            raise DomainError('MEMORY_PLAN_INPUT_CHANGED', '故事或参考照片变化，请创建新任务，不能复用旧镜头。', 409)
        if saved.get('status') == 'ready': return saved['direction']
        if saved.get('status') != 'invalid_direction':
            raise DomainError('MEMORY_PLAN_UNRESOLVED', '上次分镜准备未完成，请核对原任务记录，不重复发送模型请求。', 409)
        user += '\nPrevious received response failed validation: ' + str(saved.get('validation_error', 'Review the exact schema, source labels and slot quotes.'))[:1500]
    provider = get_llm_provider()
    if not hasattr(provider, 'plan_memory_film'):
        raise DomainError('MEMORY_PLANNER_UNAVAILABLE', '尚未连接真实分镜模型，不能使用模拟结果生成影片。', 409)
    validation_history = list(saved.get('validation_history') or [])
    def persist(value):
        value = {**value, 'source_input_sha256':source_binding, 'validation_history': validation_history.copy()}
        request.production_direction = value
        protect_values(db, family, request, {'production_direction':value}, store)
        db.commit()
        from app.services.database_snapshot import persist_configured_database_snapshot
        persist_configured_database_snapshot()
    # A validator repair may make a previously received result usable. Check
    # that exact response before spending another model call; never alter it.
    if saved.get('status') == 'invalid_direction' and saved.get('received_direction'):
        try:
            recovered = validate_direction(saved['received_direction'], body=body,
                duration=spec['target_duration_seconds'], slots=slots)
        except (ValueError, TypeError):
            pass
        else:
            if recording:
                recovered.update(subtitles=recording['subtitles'], pcm_sha256=recording['pcm_sha256'], timing_basis=recording['timing_basis'])
            persist({'input_sha256': saved['input_sha256'], 'status': 'ready',
                     'attempt': saved.get('attempt', 0), 'direction': recovered,
                     'revalidated_received_response': True})
            return recovered
    if saved.get('attempt', 0) >= 4:
        raise DomainError('MEMORY_PLAN_INVALID', '分镜已完成四次已知结果的校验，仍需修正制作方案，尚未启动显卡。', 409)
    request.progress_stage = '正在整理人物与故事分镜'
    direction = None
    start_attempt = saved.get('attempt', 0) if saved.get('status') == 'invalid_direction' else 0
    for attempt in range(start_attempt, min(start_attempt + 2, 4)):
        persist({'input_sha256':binding, 'status':'submitting', 'attempt':attempt+1})
        try:
            raw = provider.plan_memory_film(system, user)
        except Exception as exc:
            persist({'input_sha256':binding, 'status':'response_unknown', 'attempt':attempt+1})
            raise DomainError('MEMORY_PLAN_UNRESOLVED', '分镜模型请求结果尚未确认；不会自动重复发送。', 503) from exc
        try:
            direction = validate_direction(raw, body=body, duration=spec['target_duration_seconds'], slots=slots)
            break
        except (ValueError, TypeError) as exc:
            validation_history.append({'attempt':attempt+1, 'error':str(exc)[:3000]})
            persist({'input_sha256':binding, 'status':'invalid_direction', 'attempt':attempt+1,
                     'validation_error':str(exc)[:3000], 'received_direction':raw})
            if attempt == min(start_attempt + 2, 4) - 1:
                raise DomainError('MEMORY_PLAN_INVALID', '分镜修正后仍未通过原文、人物或道具一致性校验，尚未启动显卡。', 409) from exc
            # A received invalid response is known, unlike a timed-out submission.
            user += '\nYour previous response failed validation: ' + str(exc)[:1500] + '\nReturn a corrected complete JSON against the original slots. Use stable owner names throughout.'
    if recording:
        direction.update(subtitles=recording['subtitles'], pcm_sha256=recording['pcm_sha256'], timing_basis=recording['timing_basis'])
    persist({'input_sha256':binding, 'status':'ready', 'attempt':attempt+1, 'direction':direction})
    return direction
