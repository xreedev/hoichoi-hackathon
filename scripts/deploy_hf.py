"""Deploy to a Hugging Face Space (Docker SDK).

Needs HF_TOKEN (write). Forwards GEMINI_API_KEY / SARVAM_API_KEY / TYPESAFE_API_KEY, when set, as Space secrets.
Ships the code, the brand catalogue and the dev sample video (never the held-out one).
"""

import os
import shutil
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parent.parent
SPACE = os.environ.get("HF_SPACE", "hoichoi-adbreaks")
SAMPLES = ["bhojon_bilashi.mp4"]
FILES = ["Dockerfile", ".dockerignore", "requirements.txt", "config.yaml", "assets/brands.json"]
DIRS = ["app", "config"]
README = """---
title: hoichoi ad breaks
emoji: 🎬
colorFrom: red
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---
Context-aware ad-break placement for long-form Bengali drama. Source: see the GitHub repository.
"""


def main() -> None:
    api = HfApi(token=os.environ["HF_TOKEN"])
    user = api.whoami()["name"]
    repo = f"{user}/{SPACE}"
    api.create_repo(repo, repo_type="space", space_sdk="docker", exist_ok=True)
    for key in ("GEMINI_API_KEY", "SARVAM_API_KEY", "TYPESAFE_API_KEY"):
        if os.environ.get(key):
            api.add_space_secret(repo, key, os.environ[key])
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        for d in DIRS:
            shutil.copytree(ROOT / d, stage / d, ignore=shutil.ignore_patterns("__pycache__"))
        for f in FILES + [f"assets/{s}" for s in SAMPLES if (ROOT / "assets" / s).exists()]:
            (stage / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / f, stage / f)
        (stage / "README.md").write_text(README)
        api.upload_folder(repo_id=repo, repo_type="space", folder_path=stage,
                          commit_message=f"deploy {os.environ.get('GITHUB_SHA', 'local')[:7]}")
    sub = f"{user}-{SPACE}".lower().replace("_", "-").replace(".", "-")
    print(f"Space: https://huggingface.co/spaces/{repo}\nApp:   https://{sub}.hf.space")


if __name__ == "__main__":
    main()
