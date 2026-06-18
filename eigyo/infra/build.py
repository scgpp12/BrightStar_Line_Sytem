#!/usr/bin/env python3
"""Lambda 用アセットを組み立てるビルドスクリプト.

生成物（infra/ 配下）:
  build/lambda/   関数コード = handler.py + transit/ + staff/ + stations.json
  build/layer/python/   依存（requests, beautifulsoup4 ほか）の Linux wheel

依存レイヤは Windows の pip でも `--platform manylinux2014_x86_64 --only-binary=:all:`
で Linux 用 wheel を取得するので Docker/WSL 不要。Lambda は x86_64 / Python 3.12 を想定。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

INFRA = Path(__file__).resolve().parent
ROOT = INFRA.parent
BUILD = INFRA / "build"
LAMBDA_DIR = BUILD / "lambda"
LAYER_DIR = BUILD / "layer"

PY_RUNTIME = "3.12"
PLATFORM = "manylinux2014_x86_64"
DEPS = ["requests>=2.31", "beautifulsoup4>=4.12"]

_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.db")


def _copytree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=_IGNORE)


def build_function() -> None:
    if LAMBDA_DIR.exists():
        shutil.rmtree(LAMBDA_DIR)
    LAMBDA_DIR.mkdir(parents=True)
    shutil.copy2(INFRA / "lambda_app" / "handler.py", LAMBDA_DIR / "handler.py")
    shutil.copy2(INFRA / "lambda_app" / "line_handler.py", LAMBDA_DIR / "line_handler.py")
    shutil.copy2(INFRA / "lambda_app" / "authlib.py", LAMBDA_DIR / "authlib.py")
    shutil.copy2(ROOT / "stations.json", LAMBDA_DIR / "stations.json")
    _copytree(ROOT / "transit", LAMBDA_DIR / "transit")
    _copytree(ROOT / "staff", LAMBDA_DIR / "staff")
    print(f"[build] function -> {LAMBDA_DIR}")


def build_layer() -> None:
    target = LAYER_DIR / "python"
    if LAYER_DIR.exists():
        shutil.rmtree(LAYER_DIR)
    target.mkdir(parents=True)
    cmd = [
        sys.executable, "-m", "pip", "install",
        "--platform", PLATFORM,
        "--python-version", PY_RUNTIME,
        "--only-binary=:all:",
        "--target", str(target),
        *DEPS,
    ]
    print("[build] layer pip:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    # 不要な dist-info の肥大を少し削減（任意）
    for d in target.glob("*.dist-info"):
        shutil.rmtree(d, ignore_errors=True)
    print(f"[build] layer -> {target}")


if __name__ == "__main__":
    build_function()
    build_layer()
    print("[build] done")
