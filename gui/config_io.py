"""config.yaml の読み書き（ruamel.yaml でコメント保持）。"""
from __future__ import annotations

import os
from typing import Any, Dict

from ruamel.yaml import YAML


_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.normpath(os.path.join(_THIS_DIR, "..", "config.yaml"))

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)


def config_path() -> str:
    return _CONFIG_PATH


def load() -> Dict[str, Any]:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return _yaml.load(f) or {}


def save(data: Dict[str, Any]) -> None:
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        _yaml.dump(data, f)


def update(section_path: list[str], value: Any) -> None:
    """ドット階層相当の path で値を更新して保存。"""
    cfg = load()
    cur = cfg
    for k in section_path[:-1]:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]
    cur[section_path[-1]] = value
    save(cfg)
