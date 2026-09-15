import pytest
from app.services.memory.memory_film import source_segments, validate_direction

STORY = '妈妈把饭装进蓝布包。爸爸在门口等我。我们一起走到河边。风吹动了树叶。大家笑着站在一起。那是我记得的一天。'


def direction():
    slots = source_segments(STORY, 30)
    return {'characters': [{'id': 'mother', 'source_label': '妈妈',
                            'appearance': 'An illustrative middle-aged woman.', 'wardrobe': 'A navy cotton blouse.'}],
            'reference_prompt': 'One fictional mother wearing a navy blouse.',
            'shots': [{'scene': s['scene'], 'source_quote': s['source_quote'], 'kind': 'memory_action',
                       'character_ids': ['mother'], 'location': 'An old home.',
                       'opening_prompt': 'The mother stands by the old doorway.',
                       'motion_prompt': 'She turns naturally and smiles.', 'prop_owners': {'blue_bag': 'mother'}} for s in slots]}


def test_every_source_character_is_preserved_and_no_scene_exceeds_native_duration():
    for duration in (30, 45, 60, 90):
        slots = source_segments(STORY, duration)
        assert ''.join(s['source_quote'] for s in slots) == STORY
        assert sum(s['duration_seconds'] for s in slots) == duration
        assert all(s['duration_seconds'] <= 5 for s in slots)


def test_direction_rejects_changed_quotes_and_undefined_cast():
    result = validate_direction(direction(), body=STORY, duration=30)
    assert len(result['shots']) == 6
    changed = direction(); changed['shots'][0]['source_quote'] = '爸爸带来了钥匙。'
    with pytest.raises(ValueError): validate_direction(changed, body=STORY, duration=30)
    changed = direction(); changed['shots'][0]['character_ids'] = ['stranger']
    with pytest.raises(ValueError): validate_direction(changed, body=STORY, duration=30)


def test_unshown_prop_handover_is_not_silently_accepted():
    changed = direction(); changed['shots'][1]['prop_owners'] = {'blue_bag': 'none'}
    changed['characters'].append({'id': 'father', 'source_label': '爸爸',
        'appearance': 'An illustrative middle-aged man.', 'wardrobe': 'A gray shirt.'})
    changed['shots'][2]['prop_owners'] = {'blue_bag': 'father'}
    with pytest.raises(ValueError): validate_direction(changed, body=STORY, duration=30)


def test_same_person_can_put_down_and_pick_up_a_prop():
    changed = direction()
    changed['shots'][0]['prop_owners'] = {'blue_bag': 'none'}
    changed['shots'][2]['prop_owners'] = {'blue_bag': 'none'}
    assert len(validate_direction(changed, body=STORY, duration=30)['shots']) == 6
