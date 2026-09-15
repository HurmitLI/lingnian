"""One local Klein reference, followed by review; never submits Wan or installs models."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
from urllib.parse import urlparse

from PIL import Image

from .comfyui import ComfyUiClient
from .continuity import digest
from .models import PackageError, TemporaryWorkerError
from .short_scene import _hash, _require, _save


MODELS = {"UNETLoader": ("unet_name", "flux-2-klein-4b.safetensors"),
          "CLIPLoader": ("clip_name", "qwen_3_4b.safetensors"),
          "VAELoader": ("vae_name", "flux2-vae.safetensors")}
REFERENCE_CHECKS = ["whole_recording_context", "source_matches_scene", "identity", "eyes_face",
                    "hands_limbs", "wardrobe_props", "era_location", "opening_state"]


def validate_brief(envelope: dict, photo: Path | None) -> dict:
    body = envelope.get("brief")
    _require(isinstance(body, dict) and _hash(body) == envelope.get("brief_sha256"), "参考方案已变化，不能沿用旧授权。")
    _require(body.get("format") == "lingnian-short-reference" and type(body.get("version")) is int
             and body["version"] == 1 and body.get("generation_authorized") is False
             and body.get("visual_accepted") is False, "参考方案协议或核对状态不正确。")
    _require(body.get("target") == {"width": 1280, "height": 704, "images": 1}, "只准备一个1280×704开场参考。")
    renderer = body.get("renderer_version", 1)
    _require(type(renderer) is int and renderer in {1, 2}, "参考渲染版本不受支持，不能换工作流重投。")
    mode = body.get("reference_mode")
    _require(mode in {"illustrative", "user_photo"}, "参考来源不明确。")
    anchor = body.get("source_photo")
    if mode == "user_photo":
        _require(isinstance(anchor, dict) and anchor.get("usage_authorized") is True
                 and anchor.get("identity_claim") == "photo_reference_not_historical_footage"
                 and body.get("identity_claim") == anchor["identity_claim"]
                 and photo is not None and not photo.is_symlink() and photo.is_file()
                 and 0 < photo.stat().st_size <= 32 * 1024**2 and digest(photo) == anchor.get("sha256"),
                 "原照片未获授权或与当前参考依据不同。")
        with Image.open(photo) as picture:
            _require(picture.format in {"PNG", "JPEG"} and all(128 <= d <= 4096 for d in picture.size), "原照片格式或尺寸不符合。")
            picture.verify()
    else:
        _require(photo is None and anchor is None and body.get("identity_claim") == "illustrative_not_verified_likeness",
                 "无照片路径只能使用示意人物，不能偷偷加入他人照片。")
    scene, candidate = body.get("scene"), body.get("candidate")
    _require(isinstance(scene, dict) and isinstance(candidate, dict) and isinstance(candidate.get("text"), str), "缺少选中的原文和场景。")
    _require(isinstance(body.get("answers"), list) and body["answers"] and isinstance(body.get("interview_context"), dict), "缺少整场采访上下文。")
    full_text = "\n".join(a["text"] for a in body["answers"] if isinstance(a, dict) and isinstance(a.get("text"), str))
    _require(candidate["text"] and candidate["text"] in full_text and 4 <= candidate.get("duration_seconds", 0) <= 10,
             "选中片段不属于完整采访依据。")
    _require(isinstance(scene.get("source_quotes"), list) and scene["source_quotes"]
             and all(isinstance(q, str) and q and q in candidate["text"] for q in scene["source_quotes"]), "场景缺少选中片段的出处。")
    for key in ("render_bible", "opening_state", "context_summary"):
        _require(isinstance(scene.get(key), str) and 0 < len(scene[key].strip()) <= 2500, "场景文字缺失或过长。")
    if renderer == 2:
        facts = scene.get("facts")
        fields = {"character", "location", "era", "wardrobe", "prop"}
        _require(isinstance(facts, list) and len(facts) == 5
                 and all(isinstance(f, dict) and isinstance(f.get("field"), str) for f in facts)
                 and {f["field"] for f in facts} == fields, "参考渲染缺少完整事实依据。")
        for fact in facts:
            value, quotes = fact.get("value"), fact.get("source_quotes")
            if fact.get("status") == "known":
                _require(isinstance(value, str) and 0 < len(value.strip()) <= 500
                         and isinstance(quotes, list) and 0 < len(quotes) <= 6
                         and all(isinstance(q, str) and q.strip() and q in full_text for q in quotes)
                         and any(value in q for q in quotes), "参考事实不能脱离采访原文。")
            else:
                _require(fact.get("status") == "unknown" and value is None and quotes == [],
                         "未知参考事实不能夹带推断。")
    return body


def make_graph(body: dict) -> dict:
    scene = body["scene"]
    # Image composition uses the opening state, not a montage of every noun or later action.
    prompt = ("Create ONE consistent color live-action opening frame, ONE main subject. "
              "Natural human eyes, face and limbs. Stable wardrobe and props. No montage, no text, no collage. "
              + scene["render_bible"] + " Opening state: " + scene["opening_state"])
    if body["reference_mode"] == "user_photo":
        prompt += (" Use the supplied photo only as the person's identity reference, not its background or monochrome style. "
                   "Follow the event context for the scene; do not assume the narrator and story subject are the same person. "
                   "Do not claim an inferred age or reconstruction is verified history.")
    else:
        prompt += " The person is illustrative, not a recovered likeness of an actual family member."
    # Keep version 1 byte-for-byte stable: an upgrade must not change the graph
    # behind an already-authorized brief or create another uncertain GPU job.
    if body.get("renderer_version", 1) == 2:
        known = {f["field"]: f["value"] for f in scene["facts"] if f["status"] == "known"}
        prompt += ("\nSource-backed scene facts: " + json.dumps(known, ensure_ascii=False, sort_keys=True)
                   + "\nConstraints: Depict the person's age AT THE RECORDED EVENT, not their age today "
                   "or an elderly-family-member stereotype. Keep natural age-appropriate face, hair and posture. "
                   "Background details not established by the scene must stay unobtrusive and indistinct. "
                   "A season alone is not evidence of snowfall, rain or dramatic weather. "
                   "Do not introduce identifiable scenery, landmarks or new story events to fill unknowns. "
                   "Keep the established opening pose, clothing, props, camera and natural color.")
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": MODELS["UNETLoader"][1], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": MODELS["CLIPLoader"][1], "type": "flux2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": MODELS["VAELoader"][1]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "5": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
        "6": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": 1280, "height": 704, "batch_size": 1}},
        "7": {"class_type": "RandomNoise", "inputs": {"noise_seed": int(_hash(body)[:15], 16)}},
        "8": {"class_type": "CFGGuider", "inputs": {"model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0], "cfg": 1.0}},
        "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "10": {"class_type": "Flux2Scheduler", "inputs": {"steps": 4, "width": 1280, "height": 704}},
        "11": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["7", 0], "guider": ["8", 0], "sampler": ["9", 0], "sigmas": ["10", 0], "latent_image": ["6", 0]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "SaveImage", "inputs": {"images": ["12", 0], "filename_prefix": "lingnian-short-reference/" + _hash(body)[:24]}},
    }
    if body["reference_mode"] == "user_photo":
        graph.update({
            "20": {"class_type": "LoadImage", "inputs": {"image": "source-photo"}},
            "21": {"class_type": "ImageScaleToTotalPixels", "inputs": {"image": ["20", 0], "upscale_method": "nearest-exact", "megapixels": 1.0, "resolution_steps": 1}},
            "22": {"class_type": "VAEEncode", "inputs": {"pixels": ["21", 0], "vae": ["3", 0]}},
            "23": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["4", 0], "latent": ["22", 0]}},
            "24": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["5", 0], "latent": ["22", 0]}},
        })
        graph["8"]["inputs"].update(positive=["23", 0], negative=["24", 0])
    return graph


def prepare_reference(envelope: dict, *, photo: Path | None, work_dir: Path, client: ComfyUiClient,
                      authorized_brief_sha256: str | None = None, on_generating=None) -> dict:
    body = validate_brief(envelope, photo)
    _require(authorized_brief_sha256 == envelope["brief_sha256"], "本次场景文字与照片尚未取得参考生成专属授权。")
    _require(urlparse(client.base_url).hostname in {"127.0.0.1", "localhost", "::1"}, "只在家庭节点本机生成参考。")
    graph = make_graph(body)
    binding = _hash({"brief": body, "graph": graph, "server": client.base_url})
    folder = work_dir / binding[:24]
    _require(not work_dir.is_symlink() and not folder.is_symlink(), "参考工作目录不能是链接。")
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = folder / "reference.lock"
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600); os.close(fd)
    except FileExistsError as exc:
        raise TemporaryWorkerError("参考图任务已有执行锁，先核对原进程，不重复生成。") from exc
    try:
        image, receipt = folder / "reference.png", folder / "reference-evidence.json"
        if receipt.exists():
            evidence = json.loads(receipt.read_text(encoding="utf-8"))
            _require(evidence.get("binding") == binding and evidence.get("status") == "awaiting_reference_review"
                     and evidence.get("visual_accepted") is False and evidence.get("generation_ready") is False
                     and image.is_file() and digest(image) == evidence.get("sha256"), "参考产物记录变化，不自动重新生成。")
            return evidence
        _require(not image.exists(), "有未核对的参考产物，先恢复原任务记录，不覆盖或重生。")
        actual = folder / "actual-api.json"
        if not (folder / "job.json").exists():
            client.check_reference_graph(graph)
        if actual.exists():
            saved = json.loads(actual.read_text(encoding="utf-8"))
            normalized = json.loads(json.dumps(saved))
            if photo is not None:
                normalized["20"]["inputs"]["image"] = "source-photo"
            _require(normalized == graph, "参考图实际请求已变化，不能重投。")
            graph = saved
        else:
            _require(not (folder / "job.json").exists(), "已有生成日志但缺失请求，保留现场。")
            if photo is not None:
                source = folder / ("source-" + body["source_photo"]["sha256"] + photo.suffix.lower())
                if not source.exists():
                    with os.fdopen(os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as target, photo.open("rb") as original:
                        shutil.copyfileobj(original, target)
                    source.chmod(0o600)
                _require(digest(source) == body["source_photo"]["sha256"], "照片在准备期间变化，没有提交生成。")
                graph["20"]["inputs"]["image"] = client.upload_image(source)
            _save(actual, graph)
        _save(folder / "brief.json", envelope)
        wait_options = {}
        if on_generating is not None:
            on_generating()
            wait_options["on_wait"] = on_generating
        output = client.run_workflow(graph, output_path=image, journal_path=folder / "job.json", **wait_options)
        _require(output.resolve() == image.resolve() and image.is_file(), "未返回约定的PNG参考，保留原任务。")
        with Image.open(image) as picture:
            _require(picture.format == "PNG" and picture.size == (1280, 704), "参考输出尺寸不符，不拉伸补齐。")
            picture.verify()
        image.chmod(0o600)
        evidence = {"status": "awaiting_reference_review", "binding": binding, "brief_sha256": envelope["brief_sha256"],
                    "sha256": digest(image), "reference_path": str(image), "source_photo": body["source_photo"],
                    "identity_claim": body["identity_claim"], "visual_accepted": False, "generation_ready": False,
                    "required_checks": REFERENCE_CHECKS, "automatic_retry": False}
        _save(receipt, evidence)
        return evidence
    finally:
        lock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="仅检查或准备一张原文绑定的Klein场景参考，不启动视频。")
    parser.add_argument("--brief", type=Path, required=True)
    parser.add_argument("--photo", type=Path)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    parser.add_argument("--authorized-brief-sha256")
    args = parser.parse_args()
    envelope = json.loads(args.brief.read_text(encoding="utf-8"))
    body = validate_brief(envelope, args.photo)
    _require(urlparse(args.comfy_url).hostname in {"127.0.0.1", "localhost", "::1"}, "只检查家庭节点本机。")
    client = ComfyUiClient(args.comfy_url, args.brief, timeout_seconds=600)
    try:
        if args.authorized_brief_sha256 is None:
            client.check_reference_graph(make_graph(body))
            result = {"status": "preflight_only", "brief_sha256": envelope["brief_sha256"],
                      "generation_submitted": False, "visual_accepted": False}
        else:
            result = prepare_reference(envelope, photo=args.photo, work_dir=args.work_dir, client=client,
                                       authorized_brief_sha256=args.authorized_brief_sha256)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        client.close()


if __name__ == "__main__":
    main()
