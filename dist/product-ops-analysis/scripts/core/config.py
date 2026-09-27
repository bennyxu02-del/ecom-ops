"""读取方法内核（methods/）。平台与 Skill 共用。"""
from __future__ import annotations

import copy
import json
import os
from functools import lru_cache
from pathlib import Path


def methods_dir() -> Path:
    env = os.environ.get("METHODS_DIR")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parent
    for cand in (here.parent / "methods", here.parent / "config", here / "methods"):
        if cand.exists():
            return cand
    raise FileNotFoundError("找不到 methods 目录，请设置 METHODS_DIR")


def _read(path: Path):
    """平台读 YAML；Skill 包里的配置在打包时转成了同名 JSON，运行时不依赖 PyYAML。"""
    if path.suffix == ".yaml" and not path.exists() and path.with_suffix(".json").exists():
        path = path.with_suffix(".json")
    with open(path, encoding="utf-8") as f:
        if path.suffix == ".json":
            return json.load(f)
        import yaml
        return yaml.safe_load(f)


def _load(name: str):
    return _read(methods_dir() / name)


@lru_cache(maxsize=None)
def metrics() -> dict:
    return _load("metrics.yaml")


@lru_cache(maxsize=None)
def tiering_cfg() -> dict:
    return _load("tiering.yaml")


@lru_cache(maxsize=None)
def alert_rules() -> dict:
    return _load("alert_rules.yaml")


@lru_cache(maxsize=None)
def action_library() -> dict:
    return _load("action_library.yaml")


@lru_cache(maxsize=None)
def tree() -> dict:
    return _load("tree.yaml")


@lru_cache(maxsize=None)
def calendar() -> dict:
    p = methods_dir() / "calendar.yaml"
    return _load("calendar.yaml") if p.exists() or p.with_suffix(".json").exists() else {"festivals": []}


def read_text(name: str) -> str:
    return (methods_dir() / name).read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def _profiles() -> dict:
    out = {}
    d = methods_dir() / "category_profiles"
    for p in sorted(list(d.glob("*.yaml")) + list(d.glob("*.json"))):
        out.setdefault(p.stem, _read(p))
    return out


def _deep_merge(base: dict, over: dict) -> dict:
    res = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(res.get(k), dict):
            res[k] = _deep_merge(res[k], v)
        else:
            res[k] = copy.deepcopy(v)
    return res


def profile(category: str | None) -> dict:
    """返回合并后的品类配置：通用默认 + 品类专属。匹配不到时 matched=False。"""
    profs = _profiles()
    base = profs["default"]
    key = (category or "").strip()
    for pid, p in profs.items():
        if pid == "default":
            continue
        if key == pid or key in (p.get("match") or []):
            merged = _deep_merge(base, p)
            merged["matched"] = True
            return merged
    merged = copy.deepcopy(base)
    merged["matched"] = False
    return merged


def cause_name(cause: str) -> str:
    return action_library().get("cause_names", {}).get(cause, cause)


def channel_name(ch: str) -> str:
    return metrics().get("channels", {}).get(ch, ch)
