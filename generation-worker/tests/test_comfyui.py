from lingnian_worker.comfyui import _replace_tokens


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
