"""Deploy a project (WaitLess or Mouldproof) to a Hugging Face Docker Space.

Reads HF_TOKEN, NEBIUS_API_KEY and TAVILY_API_KEY from .env; the API keys are
stored as Space secrets, never in the uploaded files.

Usage: python scripts/deploy_hf.py [space-name] [project-root]
"""
import os
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

SPACES = {
    "waitless": ("WaitLess", "Find a shorter NHS wait with NVIDIA Nemotron",
                 "WaitLess finds NHS-funded providers in England that can see you sooner, using NHS England waiting-time data, "
                 "NVIDIA Nemotron models on Nebius Token Factory and Tavily.", "https://github.com/mahum24/waitless"),
    "mouldproof": ("Mouldproof", "Awaab's Law case builder with NVIDIA Nemotron",
                   "Mouldproof turns photos of damp and mould into Awaab's Law deadlines, a verified landlord record and letters, "
                   "using NVIDIA Nemotron models on Nebius Token Factory and Tavily.", "https://github.com/mahum24/mouldproof"),
}

SPACE_README = """---
title: {title}
emoji: 🏠
colorFrom: green
colorTo: blue
sdk: docker
app_port: 8000
pinned: false
license: mit
short_description: {short}
---

{body}

Source: {repo}
"""

FILES = ["Dockerfile", "requirements.txt", "LICENSE", "data/providers.json"]
DIRS = ["app", "static"]


def main(name: str, root: Path):
    api = HfApi(token=os.environ["HF_TOKEN"])
    user = api.whoami()["name"]
    repo_id = f"{user}/{name}"
    api.create_repo(repo_id, repo_type="space", space_sdk="docker", exist_ok=True)
    for key in ("NEBIUS_API_KEY", "TAVILY_API_KEY"):
        api.add_space_secret(repo_id, key, os.environ[key])

    with tempfile.TemporaryDirectory() as tmp:
        readme = Path(tmp) / "README.md"
        title, short, body, repo = SPACES[name]
        readme.write_text(SPACE_README.format(title=title, short=short, body=body, repo=repo))
        api.upload_file(path_or_fileobj=str(readme), path_in_repo="README.md", repo_id=repo_id, repo_type="space")
    for f in FILES:
        if (root / f).exists():
            api.upload_file(path_or_fileobj=str(root / f), path_in_repo=f, repo_id=repo_id, repo_type="space")
    for d in DIRS:
        api.upload_folder(folder_path=str(root / d), path_in_repo=d, repo_id=repo_id, repo_type="space",
                          ignore_patterns=["__pycache__/*", "*.pyc"])
    sub = repo_id.replace("/", "-").replace("_", "-").lower()
    print(f"Space: https://huggingface.co/spaces/{repo_id}")
    print(f"App:   https://{sub}.hf.space")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "waitless", Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT)
