import json
from pathlib import Path

import pytest

from lingnian_node.core import Node, Settings, Task


def task(**changes):
    value={"id":"t1","kind":"story_image","authorized":True,"purpose":"虚构故事测试","cost_limit_cents":10,"estimated_cost_cents":0,"workflow":{}}
    value.update(changes); return Task.model_validate(value)


def test_rejects_unauthorized(tmp_path):
    node=Node(Settings(tmp_path))
    with pytest.raises(PermissionError,match="没有明确用户授权"): node.validate(task(authorized=False))


def test_rejects_portrait_without_subject_consent(tmp_path):
    node=Node(Settings(tmp_path))
    with pytest.raises(PermissionError,match="本人专项授权"): node.validate(task(kind="lip_sync"))


def test_rejects_over_budget(tmp_path):
    node=Node(Settings(tmp_path,max_cost_cents=5))
    with pytest.raises(PermissionError,match="超过上限"): node.validate(task(estimated_cost_cents=6))


def test_restart_recovers_incomplete(tmp_path):
    node=Node(Settings(tmp_path)); node.store.set("job","generating")
    again=Node(Settings(tmp_path))
    assert again.store.status("job")=="retry"
