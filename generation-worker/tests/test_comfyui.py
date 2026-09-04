from lingnian_worker.comfyui import (
    _adapt_common_workflow,
    _replace_tokens,
    _scene_seed,
    _workflow_binding_report,
)


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
        "__LINGNIAN_FRAMES__": 120,
    }
    rendered = _adapt_common_workflow(workflow, replacements)
    assert rendered["1"]["inputs"]["text"] == "1982年蓝布包近景"
    assert rendered["2"]["inputs"]["text"] == "现代高楼"
    assert rendered["3"]["inputs"]["seed"] == 1234
    assert rendered["4"]["inputs"] == {"width": 1280, "height": 720}
    assert rendered["5"]["inputs"]["filename_prefix"] == "lingnian/scene-03"
    assert _workflow_binding_report(workflow) == {"prompt": True, "seed": True, "output": True}
