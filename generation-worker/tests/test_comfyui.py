from lingnian_worker.comfyui import (
    _adapt_common_workflow,
    _english_scene_prompt,
    _model_dimensions,
    _replace_tokens,
    _scene_seed,
    _workflow_binding_report,
)
import pytest

from lingnian_worker.models import ConfigurationError


def test_workflow_tokens_keep_numeric_types_and_replace_embedded_text():
    workflow = {
        "1": {
            "inputs": {
                "width": "__LINGNIAN_WIDTH__",
                "text": "画面：__LINGNIAN_PROMPT__",
                "frames": "__LINGNIAN_FRAMES__",
            }
        }
    }
    rendered = _replace_tokens(
        workflow,
        {
            "__LINGNIAN_WIDTH__": 1280,
            "__LINGNIAN_FRAMES__": 240,
            "__LINGNIAN_PROMPT__": "老火车驶离站台",
        },
    )
    assert rendered["1"]["inputs"]["width"] == 1280
    assert rendered["1"]["inputs"]["frames"] == 240
    assert rendered["1"]["inputs"]["text"] == "画面：老火车驶离站台"


def test_scene_seed_is_stable_but_unique_per_shot_and_retry():
    first = {
        "source_story_id": "story-1",
        "scene": 2,
        "subtitle": "蓝布包",
        "visual_direction": "蓝布包近景",
    }
    second = {**first, "scene": 3, "subtitle": "火车站台"}
    assert _scene_seed(first) == _scene_seed(first)
    assert _scene_seed(first) != _scene_seed(second)
    assert _scene_seed(first) != _scene_seed(first, 1)


def test_common_fixed_prompt_workflow_is_bound_through_sampler_edges():
    workflow = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "老师写死的城市航拍"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "低质量"}},
        "3": {
            "class_type": "KSampler",
            "inputs": {"positive": ["1", 0], "negative": ["2", 0], "seed": 7},
        },
        "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512}},
        "5": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI"}},
    }
    replacements = {
        "__LINGNIAN_PROMPT__": "1982年蓝布包近景",
        "__LINGNIAN_NEGATIVE_PROMPT__": "现代高楼",
        "__LINGNIAN_SEED__": 1234,
        "__LINGNIAN_OUTPUT_PREFIX__": "lingnian/scene-03",
        "__LINGNIAN_WIDTH__": 1280,
        "__LINGNIAN_HEIGHT__": 720,
        "__LINGNIAN_MODEL_WIDTH__": 512,
        "__LINGNIAN_MODEL_HEIGHT__": 320,
        "__LINGNIAN_FRAMES__": 120,
    }
    rendered = _adapt_common_workflow(workflow, replacements)
    assert rendered["1"]["inputs"]["text"] == "1982年蓝布包近景"
    assert rendered["2"]["inputs"]["text"] == "现代高楼"
    assert rendered["3"]["inputs"]["seed"] == 1234
    assert rendered["4"]["inputs"] == {"width": 512, "height": 320}
    assert rendered["5"]["inputs"]["filename_prefix"] == "lingnian/scene-03"
    assert _workflow_binding_report(workflow) == {"prompt": True, "seed": True, "output": True}


def test_model_resolution_is_separate_and_prompt_is_english_for_sd15():
    assert _model_dimensions(1280, 720) == (512, 320)
    assert _model_dimensions(720, 1280) == (320, 512)
    prompt = _english_scene_prompt(
        {"visual_direction": "1982年中国火车站台，绿色客车缓慢驶过"}
    )
    assert "passenger train" in prompt
    assert "railway station platform" in prompt
    assert not any("\u4e00" <= char <= "\u9fff" for char in prompt)


def test_shot_subject_does_not_leak_from_whole_story_or_negative_direction():
    direction = (
        "整段已确认故事仅为「那个蓝布包她一直没舍得扔。1982年春天，她19岁，坐火车去纺织厂。」；"
        "当前镜头必须直接对应「那个蓝布包她一直没舍得扔。」。时代固定为1982年前后的中国；"
        "禁止无关火车站台或山脉。"
    )
    bag = _english_scene_prompt({"narration": "那个蓝布包她一直没舍得扔。", "visual_direction": direction})
    assert bag.startswith("a worn blue cloth satchel")
    assert "1982" in bag
    assert all(term not in bag for term in ("train", "factory", "mountains"))
    train = _english_scene_prompt({"narration": "第一次一个人坐火车去无锡的纺织厂。", "visual_direction": direction})
    assert train.startswith("a Chinese passenger train")
    assert "satchel" not in train


def test_date_only_shot_reuses_grounded_object_but_unknown_story_is_not_invented():
    prompt = _english_scene_prompt({
        "narration": "1982年春天，她19岁，",
        "visual_direction": "整段已确认故事仅为「蓝布包一直没舍得扔。坐火车去纺织厂。」",
    })
    assert "satchel" in prompt and "1982" in prompt and "spring" in prompt
    with pytest.raises(ConfigurationError, match="分镜翻译"):
        _english_scene_prompt({"narration": "她终于懂得了那句话的意思。"})
