"""Bind native editorial slots to measured source audio, never rewritten prose."""
import re
from app.core.errors import DomainError
from app.services.asr.timing import pcm_evidence, bound_answer_timing


def recorded_slots(db, session, family, store, audio_path, audio_id, duration):
    from app.api.routes import secure_value
    evidence = pcm_evidence(audio_path)
    if not duration - 5 <= evidence['duration_ms'] / 1000 <= duration + .05:
        raise DomainError('AUDIO_DURATION_MISMATCH', '完整录音与所选时长不匹配，请选择匹配时长；不会自动截取前10秒。', 409)
    transcript = session.transcript
    metadata = secure_value(db, family, transcript, 'asr_metadata', store) if transcript else {}
    merged = metadata.get('interview_timeline', {})
    items = []
    if merged.get('asset_id') == audio_id and merged.get('pcm_sha256') == evidence['pcm_sha256']:
        turns = {turn.id: turn for turn in session.interview_turns}
        for segment in merged.get('segments', []):
            start = segment['start_frame'] * 1000 / evidence['sample_rate']
            end = segment['end_frame'] * 1000 / evidence['sample_rate']
            if segment['role'] == 'question':
                turn = turns.get(segment['turn_id'])
                if turn:
                    items.append({'text': secure_value(db, family, turn, 'question_text', store), 'start_ms': start, 'end_ms': end})
            elif segment.get('asr_timing', {}).get('status') == 'available':
                items.extend({'text': s['text'], 'start_ms': start+s['start_ms'], 'end_ms': start+s['end_ms']} for s in segment['asr_timing']['items'])
            else:
                raise DomainError('SOURCE_TIMING_REQUIRED', '该录音缺少真实语音时间信息，请先完成语音识别。', 409)
    else:
        timing = bound_answer_timing(metadata, evidence)
        if timing['status'] == 'available': items = timing['items']
    if not items:
        raise DomainError('SOURCE_TIMING_REQUIRED', '录音与语音识别时间信息不匹配，不能按改写稿猜测镜头时间。', 409)
    if transcript and session.interview_mode == 'single':
        corrected = secure_value(db, family, transcript, 'corrected_text', store)
        items = minor_corrections(items, corrected)
    slots = []
    for i in range(duration // 5):
        matching = [s for s in items if s['end_ms'] > i*5000 and s['start_ms'] < (i+1)*5000]
        if not matching:
            # Silence continues the nearest established scene; no new narration.
            matching = [min(items, key=lambda s: abs((s['start_ms']+s['end_ms'])/2 - (i+.5)*5000))]
        slots.append({'scene': i+1, 'source_quote': ''.join(s['text'] for s in matching),
                      'start_seconds': i*5, 'duration_seconds': 5})
    return {'body': ''.join(s['text'] for s in items), 'slots': slots, 'subtitles': items,
            'pcm_sha256': evidence['pcm_sha256'], 'timing_basis': 'measured_asr_source_audio'}


def minor_corrections(items, corrected):
    """Apply sparse character replacements only; never retime rewritten narration."""
    pattern = r"[\w]"
    original = re.findall(pattern, ''.join(s['text'] for s in items))
    revised = re.findall(pattern, corrected or '')
    if len(original) != len(revised) or not original:
        return items
    if sum(a != b for a,b in zip(original,revised)) > max(2, int(len(original)*.04)):
        return items
    replacements = iter(revised)
    return [{**s, 'text': re.sub(pattern, lambda m: next(replacements), s['text'])} for s in items]
