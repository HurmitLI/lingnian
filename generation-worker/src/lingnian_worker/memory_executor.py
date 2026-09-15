"""Reference-led native film renderer, invoked only by the v4 product contract."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Callable

from .comfyui import ComfyUiClient
from .memory_graphs import motion_graph, reference_graph
from .media import MediaRenderer
from .models import AuthorizedPackage, PackageError, RenderReport, WorkerTask


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class NativeMemoryExecutor:
    def __init__(self, *, renderer: MediaRenderer, comfyui: ComfyUiClient, work_dir: Path):
        self.renderer, self.comfyui, self.work_dir = renderer, comfyui, work_dir

    def execute(self, task: WorkerTask, package: AuthorizedPackage, progress: Callable) -> RenderReport:
        plan = package.plan
        spec = plan.get('production_spec') or {}
        direction = plan.get('direction') or {}
        shots = direction.get('shots') or []
        duration = spec.get('target_duration_seconds')
        if plan.get('version') != 4 or spec.get('visual_strategy') != 'native_memory':
            raise PackageError('不是原生动态回忆影片任务。')
        if duration not in {30, 45, 60, 90} or len(shots) != duration // 5:
            raise PackageError('原生分镜时长不正确。')
        if any(s.get('scene') != i + 1 or s.get('duration_seconds') != 5 for i, s in enumerate(shots)):
            raise PackageError('原生镜头必须按顺序各生成5秒，不能拉长补时。')
        if not package.audio_path:
            raise PackageError('没有对应的录音，不能假装有声音成片。')
        audio_duration = self.renderer.probe(package.audio_path)['duration']
        if not duration - 5 <= audio_duration <= duration + .05:
            raise PackageError('录音时长与影片不匹配，请选择匹配时长；不会截句或拉长静音。')
        cast = direction.get('characters') or []
        if not cast or len(cast) > 4 or not direction.get('reference_prompt'):
            raise PackageError('缺少统一的人物设定。')
        root = self.work_dir / 'jobs' / task.id / 'native-memory'
        root.mkdir(parents=True, exist_ok=True)
        binding = canonical({'plan': plan, 'audio': digest(package.audio_path),
                             'photo': digest(package.image_path) if package.image_path else None})
        binding_file = root / 'input-binding.json'
        if binding_file.exists() and json.loads(binding_file.read_text(encoding='utf-8')).get('sha256') != binding:
            raise PackageError('任务输入变化，不能沿用已有镜头或重新提交。')
        self.comfyui._save_journal(binding_file, {'sha256': binding})
        total = len(shots)
        completed_count = 0
        def update(percent, stage, done=None):
            nonlocal completed_count
            if done is not None: completed_count = done
            progress(percent, stage, completed_count, total, f'native-{completed_count:02d}')
        def uploaded(path):
            # Content-addressed filenames prevent separate jobs overwriting inputs.
            copy = root / (digest(path) + path.suffix)
            if not copy.exists(): shutil.copyfile(path, copy)
            return self.comfyui.upload_image(copy)
        def generate(name, graph, suffix, percent, message):
            output = root / (name + suffix); receipt = root / (name + '.receipt.json')
            graph_hash = canonical(graph)
            if receipt.exists():
                state = json.loads(receipt.read_text(encoding='utf-8'))
                if state.get('graph_sha256') != graph_hash or not output.is_file() or state.get('sha256') != digest(output):
                    raise PackageError('镜头检查点不一致，不能静默覆盖。')
                return output
            result = self.comfyui.run_workflow(graph, output_path=output,
                journal_path=root / (name + '.job.json'), on_wait=lambda: update(percent, message))
            if result != output or not output.is_file():
                raise PackageError('工作流返回了不符合协议的文件类型。')
            self.comfyui._save_journal(receipt, {'graph_sha256': graph_hash, 'sha256': digest(output)})
            return output
        seed = int(binding[:15], 16)
        prefix = 'lingnian-memory/' + binding[:16]
        update(5, '正在建立统一人物参考')
        anchor = uploaded(package.image_path) if package.image_path else None
        portrait = generate('cast', reference_graph(prompt=direction['reference_prompt'], seed=seed,
            prefix=prefix+'-cast', reference=anchor), '.png', 5, '正在建立统一人物参考')
        cast_reference = uploaded(portrait)
        # Prepare references together before loading Wan. Alternating Klein and
        # Wan per shot repeatedly evicts model weights on a 16 GB node.
        references = []
        for i, shot in enumerate(shots):
            percent = 6 + int(14 * i / total)
            update(percent, f'正在生成第{i+1}/{total}个场景参考', 0)
            visible = [c for c in cast if c['id'] in shot['character_ids']]
            wardrobe = ' '.join(c['id']+': '+c['appearance']+' '+c['wardrobe'] for c in visible)
            opening = shot['opening_prompt'] + (' Preserve this visible cast only: ' + wardrobe if visible else ' No people or faces in this shot; focus on the specified object or environment.')
            still = generate(f'scene-{i+1:02d}-ref', reference_graph(prompt=opening, seed=seed+i+1,
                prefix=prefix+f'-ref-{i+1}', reference=cast_reference), '.png', percent, f'正在生成第{i+1}/{total}个场景参考')
            references.append(uploaded(still))
        clips = []; hashes = set()
        for i, (shot, image_name) in enumerate(zip(shots, references)):
            percent = 20 + int(65 * i / total)
            update(percent, f'正在生成第{i+1}/{total}个动态镜头', i)
            continuity = (
                ' Preserve the exact faces, hair, wardrobe, prop ownership and number of people from this frame.'
                if shot['character_ids'] else
                ' Keep the environment empty of people. No people, faces or hands enter the frame. Preserve the objects from this frame.'
            )
            movie = generate(f'scene-{i+1:02d}-raw', motion_graph(prompt=shot['motion_prompt'] +
                continuity + ' Natural coordinated movement. One continuous shot.',
                seed=seed+100+i, prefix=prefix+f'-motion-{i+1}', reference=image_name),
                '.mp4', percent+2, f'正在生成第{i+1}/{total}个动态镜头')
            h = self.renderer.visual_fingerprint(movie)
            if h in hashes: raise PackageError('不同镜头返回相同视频，不能重复拼片。')
            hashes.add(h)
            clip = root / f'scene-{i+1:02d}.mp4'
            subtitle_options = {}
            if 'subtitles' in direction:
                subtitle_options['subtitle_segments'] = [
                    {'text': line['text'], 'start_seconds': max(0, line['start_ms']/1000-i*5),
                     'end_seconds': min(5, line['end_ms']/1000-i*5)}
                    for line in direction['subtitles'] if line['end_ms'] > i*5000 and line['start_ms'] < (i+1)*5000]
            self.renderer.video_clip(movie, clip, subtitle=shot['source_quote'], duration=5,
                width=1280, height=720, fps=24, allow_loop=False, **subtitle_options)
            clips.append(clip)
            update(20+int(65*(i+1)/total), f'已完成第{i+1}/{total}个镜头', i+1)
        result = root / 'film.mp4'
        update(90, '正在合并完整录音、字幕和动态镜头', total)
        self.renderer.assemble(clips, result, audio=package.audio_path, duration=duration)
        metadata = self.renderer.probe(result)
        if abs(metadata['duration']-duration) > .1:
            raise PackageError('合成时长不正确。')
        return RenderReport(result, total, metadata['duration'], metadata['width'], metadata['height'],
                            total, total, 0, len(hashes), True)
