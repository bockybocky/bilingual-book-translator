"""Glossary yaml load + lookup_in_text。

yaml 結構：
    terms:
      option: 選擇權
      tail risk: 尾部風險
    proper_nouns:
      John Doe: 約翰道
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


@dataclass
class Glossary:
    path: Path | None
    _entries: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.path is None:
            return
        if yaml is None:
            raise RuntimeError("PyYAML 未裝；pip install PyYAML")
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        for section in ("terms", "proper_nouns"):
            for k, v in (data.get(section) or {}).items():
                if isinstance(k, str) and isinstance(v, str):
                    self._entries[k] = v

    def lookup_in_text(self, text: str) -> dict[str, str]:
        if not self._entries:
            return {}
        hits: dict[str, str] = {}
        lower = text.lower()
        for k, v in self._entries.items():
            pattern = r"\b" + re.escape(k.lower()) + r"\b"
            if re.search(pattern, lower):
                hits[k] = v
        return hits

    def __len__(self) -> int:
        return len(self._entries)
