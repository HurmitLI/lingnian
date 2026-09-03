from __future__ import annotations

import json
from pathlib import Path


root=Path(__file__).parents[1]
workflow=json.loads((root/"local_node/workflows/故事场景图.json").read_text(encoding="utf-8"))
task={
    "id":"mock-story-5080-001", "kind":"story_image", "authorized":True,
    "subject_consent":False, "purpose":"虚构素材本机闭环验收",
    "cost_limit_cents":10, "estimated_cost_cents":0, "assets":[], "workflow":workflow,
}
target=root/"local_node/runtime/mock-cloud/inbox/mock-story-5080-001.json"
target.parent.mkdir(parents=True,exist_ok=True)
target.write_text(json.dumps(task,ensure_ascii=False,indent=2),encoding="utf-8")
print(target)
