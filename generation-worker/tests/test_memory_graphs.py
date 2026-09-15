import pytest
from lingnian_worker.memory_graphs import reference_graph, motion_graph
from lingnian_worker.models import PackageError


def test_native_chain_preserves_reference_and_model_canvas():
    g = reference_graph(prompt='A woman packs a cloth bag.', seed=18,
                        prefix='lingnian-memory/test-ref', reference='authorized-reference.png')
    assert g['8']['inputs']['positive'] == ['23', 0]
    assert g['23']['inputs']['latent'] == ['22', 0]
    assert g['20']['inputs']['image'] == 'authorized-reference.png'
    assert g['6']['inputs']['width'] == 1280
    v = motion_graph(prompt='She folds the fabric naturally.', seed=19,
                     prefix='lingnian-memory/test-motion', reference='scene-ref.png')
    assert v['4']['inputs']['image'] == 'scene-ref.png'
    assert v['5']['inputs']['text'] == 'She folds the fabric naturally.'
    nodes = [n['inputs'] for n in v.values() if n['class_type'] == 'Wan22ImageToVideoLatent']
    assert len(nodes) == 1
    assert (nodes[0]['width'], nodes[0]['height'], nodes[0]['length']) == (1280, 704, 121)


def test_illustrative_reference_does_not_silently_load_a_photo():
    g = reference_graph(prompt='A fictional adult woman.', seed=1, prefix='lingnian-memory/example')
    assert all(n['class_type'] != 'LoadImage' for n in g.values())
    assert '__PROMPT__' not in str(g)


@pytest.mark.parametrize('prefix', ['../other', '/tmp/video', 'lingnian-memory/../../escape'])
def test_untrusted_output_paths_rejected(prefix):
    with pytest.raises(PackageError):
        motion_graph(prompt='Ordinary natural movement.', seed=1, prefix=prefix, reference='ref.png')
