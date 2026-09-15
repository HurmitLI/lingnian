import json
from pathlib import Path
import pytest
from lingnian_worker.memory_executor import NativeMemoryExecutor
from lingnian_worker.models import AuthorizedPackage, WorkerTask, PackageError

class Client:
    calls = 0
    def upload_image(self, path): return path.name
    def _save_journal(self, path, state): path.write_text(json.dumps(state))
    def run_workflow(self, graph, *, output_path, **kwargs):
        self.calls += 1
        output_path.write_bytes(json.dumps(graph, sort_keys=True).encode())
        return output_path

class Renderer:
    flags = []
    def visual_fingerprint(self, path):
        import hashlib
        return hashlib.sha256(path.read_bytes()).hexdigest()
    def probe(self, path): return {'duration': 30, 'width': 1280, 'height': 720}
    def video_clip(self, source, target, *, allow_loop, **kwargs):
        self.flags.append(allow_loop); target.write_bytes(source.read_bytes()); return target
    def assemble(self, clips, target, **kwargs): target.write_bytes(b''.join(p.read_bytes() for p in clips))


def inputs(tmp_path):
    audio=tmp_path/'audio.wav';audio.write_bytes(b'authorized original waveform')
    plan={'version':4,'production_spec':{'visual_strategy':'native_memory','target_duration_seconds':30},
          'direction':{'reference_prompt':'One illustrative woman wearing a navy blouse.',
                       'characters':[{'id':'woman','appearance':'A woman with short black hair.', 'wardrobe':'A navy blouse.'}],
                       'shots':[{'scene':i+1,'duration_seconds':5,'source_quote':f'原文{i}',
                                 'opening_prompt':f'A woman preparing the object for scene {i}.',
                                 'motion_prompt':'Her hands move naturally.', 'character_ids':['woman']} for i in range(6)]}}
    task=WorkerTask('test-task','scene_video','lease','url',0,1,plan['production_spec'],{})
    return task,AuthorizedPackage(tmp_path,{},plan,audio,None)


def test_retry_reuses_all_hash_bound_native_generations_and_never_loops(tmp_path):
    client=Client(); renderer=Renderer(); task,package=inputs(tmp_path)
    engine=NativeMemoryExecutor(renderer=renderer,comfyui=client,work_dir=tmp_path/'work')
    engine.execute(task,package,lambda *a:None)
    assert client.calls==13 # cast + 6 scene refs + 6 actual videos
    engine.execute(task,package,lambda *a:None)
    assert client.calls==13

    assert renderer.flags and not any(renderer.flags)
    package.audio_path.write_bytes(b'changed waveform')
    with pytest.raises(PackageError, match='输入变化'):
        engine.execute(task,package,lambda *a:None)
    assert client.calls==13


def test_object_shot_does_not_inherit_portrait_motion_instructions(tmp_path):
    client=Client(); task,package=inputs(tmp_path)
    shot=package.plan['direction']['shots'][0]
    shot.update(character_ids=[], opening_prompt='A single jasmine against an empty wall.',
                motion_prompt='The leaves sway in the breeze.')
    engine=NativeMemoryExecutor(renderer=Renderer(),comfyui=client,work_dir=tmp_path/'work')
    engine.execute(task,package,lambda *a:None)
    videos=list((tmp_path/'work').rglob('scene-01-raw.mp4'))
    prompt=json.loads(videos[0].read_text())['5']['inputs']['text']
    assert prompt.startswith('The leaves sway in the breeze.')
    assert 'No people, faces or hands enter' in prompt
    assert 'wardrobe' not in prompt
