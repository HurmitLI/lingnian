"""Local-only, review-gated story trial. Never reads worker/cloud credentials.

Run with the worker's Python environment. ComfyUI is fixed to loopback.
Reference and motion reviews are explicit, hash-bound observations, not scores
inferred from successful generation. Existing jobs resume via their prompt IDs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from lingnian_worker.comfyui import ComfyUiClient
from lingnian_worker.memory_graphs import reference_graph, motion_graph

BASE = "http://127.0.0.1:8188"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, value):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def validate(plan):
    assert plan["fictional"] is True
    assert plan["local_only"] is True
    assert sum(s["seconds"] for s in plan["shots"]) == 60
    for i, shot in enumerate(plan["shots"], 1):
        assert shot["id"] == i and 0 < shot["seconds"] <= 5
        assert shot["quote"] and shot["quote"] in plan["source_text"]
        assert shot["expected_action"] and shot["opening"] and shot["motion"]


def require_review(path, artifact, shot, phase):
    review = read(path) if path.exists() else {}
    checks = ["identity", "anatomy", "story", "opening_state"] if phase == "reference" else ["identity", "anatomy", "story", "motion", "continuity"]
    if not (review.get("sha256") == sha(artifact)
            and review.get("shot") == shot["id"]
            and review.get("expected_action") == shot["expected_action"]
            and review.get("observed_action")
            and review.get("reviewer") in {"assistant_visual", "human_visual"}
            and review.get("decision") == "accepted"
            and all(review.get("checks", {}).get(k) is True for k in checks)):
        raise RuntimeError(f"Awaiting {phase} review: {artifact}")


def run(plan_path, root, phase, only):
    plan = read(plan_path)
    validate(plan)
    root.mkdir(parents=True, exist_ok=True)
    binding = sha(plan_path)
    manifest = root / "manifest.json"
    if manifest.exists():
        assert read(manifest)["plan_sha256"] == binding, "Use a new run directory for changed plans"
    else:
        save(manifest, {"plan_sha256": binding, "local_only": True, "server": BASE,
                        "final_accepted": False, "target_seconds": 60})
    client = ComfyUiClient(BASE, Path("unused-local-explicit-graphs"), timeout_seconds=14400)
    prefix = "lingnian-memory/local0915-" + binding[:8]

    def generate(name, graph, ext):
        output = root / (name + ext)
        receipt = root / (name + ".receipt.json")
        graph_hash = hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()
        if receipt.exists():
            old = read(receipt)
            assert old["graph_sha256"] == graph_hash and old["sha256"] == sha(output)
            return output
        print("GENERATING", name, flush=True)
        result = client.run_workflow(graph, output_path=output, journal_path=root / (name + ".job.json"))
        assert result == output and output.exists()
        save(receipt, {"sha256": sha(output), "graph_sha256": graph_hash, "visual_accepted": False})
        print("GENERATED", name, flush=True)
        return output

    try:
        if phase == "master":
            generate("master", reference_graph(prompt=plan["master"], seed=915001,
                     prefix=prefix + "-master"), ".png")
            return
        master = root / "master.png"
        require_review(root / "master.review.json", master,
                       {"id": 0, "expected_action": "统一虚构人物与茉莉盆栽设定"}, "reference")
        def uploaded(path):
            addressed = root / (sha(path) + path.suffix)
            if not addressed.exists():
                shutil.copyfile(path, addressed)
            return client.upload_image(addressed)

        selected = [s for s in plan["shots"] if not only or s["id"] in only]
        if phase == "references":
            master_input = uploaded(master)
            for shot in selected:
                name = f"shot-{shot['id']:02d}-ref"
                anchor = master_input
                if shot.get("reference_file"):
                    local_anchor = root / shot["reference_file"]
                    assert local_anchor.resolve().parent == root.resolve()
                    assert sha(local_anchor) == shot["reference_sha256"]
                    anchor = uploaded(local_anchor)
                generate(name, reference_graph(prompt=shot["opening"], seed=915100 + shot["id"],
                         prefix=prefix + "-" + name, reference=anchor), ".png")
        elif phase == "motion":
            # Validate the entire requested batch before spending video GPU time.
            for shot in selected:
                name = f"shot-{shot['id']:02d}"
                require_review(root / (name + "-ref.review.json"), root / (name + "-ref.png"), shot, "reference")
            for shot in selected:
                name = f"shot-{shot['id']:02d}"
                ref = uploaded(root / (name + "-ref.png"))
                generate(name, motion_graph(prompt=shot["motion"], seed=shot.get("motion_seed", 915200 + shot["id"]),
                         prefix=prefix + "-" + name, reference=ref), ".mp4")
        elif phase == "verify-reviews":
            for shot in plan["shots"]:
                name = f"shot-{shot['id']:02d}"
                require_review(root / (name + "-ref.review.json"), root / (name + "-ref.png"), shot, "reference")
                require_review(root / (name + ".review.json"), root / (name + ".mp4"), shot, "motion")
            hashes = [sha(root / f"shot-{s['id']:02d}.mp4") for s in plan["shots"]]
            assert len(set(hashes)) == len(hashes), "Repeated raw clips"
            save(root / "review-coverage.json", {"reviewed_seconds": 60, "all_shots_reviewed": True,
                 "film_assembled": False, "film_accepted": False, "raw_sha256": hashes})
        else:
            raise ValueError(phase)
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--phase", choices=["master", "references", "motion", "verify-reviews"], required=True)
    parser.add_argument("--only", nargs="*", type=int)
    args = parser.parse_args()
    run(args.plan, args.root, args.phase, args.only)
