"""進度持久化 — paragraph-level translation map + pickle resume。"""
from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from pathlib import Path

from chunker import Chunk


@dataclass
class State:
    run_dir: Path
    resume: bool = True
    _para_translations: dict[int, str] = field(default_factory=dict)
    _chunk_done: set[int] = field(default_factory=set)
    _chunk_raw: dict[int, str] = field(default_factory=dict)

    @property
    def _pkl(self) -> Path:
        return self.run_dir / "progress.pkl"

    def __post_init__(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        if self.resume and self._pkl.exists():
            try:
                data = pickle.loads(self._pkl.read_bytes())
                self._para_translations = data.get("para_translations", {})
                self._chunk_done = set(data.get("chunk_done", []))
                self._chunk_raw = data.get("chunk_raw", {})
            except Exception:
                self._para_translations = {}
                self._chunk_done = set()
                self._chunk_raw = {}

    def completed_indices(self) -> set[int]:
        return set(self._chunk_done)

    def get_translation(self, chunk_idx: int) -> str:
        return self._chunk_raw.get(chunk_idx, "")

    def save_chunk(self, chunk: Chunk, dst: str) -> None:
        self._chunk_done.add(chunk.idx)
        self._chunk_raw[chunk.idx] = dst
        for p_idx, zh in self._align(chunk, dst).items():
            self._para_translations[p_idx] = zh
        self._flush()

    def all_translations(self) -> dict[int, str]:
        """回傳 paragraph_idx → 中文 字典，給 EpubLoader.write_*_bilingual。"""
        return dict(self._para_translations)

    @staticmethod
    def _align(chunk: Chunk, translated: str) -> dict[int, str]:
        """按 [[PARA_N]] marker 切回，跟 chunk.paragraph_indices 對齊。

        Fallback chain:
        1. marker 對齊率 ≥ 80% → 走 marker 對齊（缺的段落不插中文）
        2. marker 全失敗但 \\n\\n 段數剛好對齊 → 走 \\n\\n
        3. 都失敗 → 整塊掛第一段（degrade gracefully，視覺差但不丟資料）
        """
        import re
        indices = chunk.paragraph_indices
        if not indices:
            return {}

        # P1: marker 對齊
        pat = re.compile(r"\[\[\s*PARA[\s_]*(\d+)\s*\]\]\s*", re.IGNORECASE)
        parts = pat.split(translated)
        # parts = [prefix, num1, content1, num2, content2, ...]
        out: dict[int, str] = {}
        for i in range(1, len(parts), 2):
            try:
                n = int(parts[i]) - 1  # 1-based → 0-based
            except ValueError:
                continue
            if i + 1 >= len(parts):
                continue
            content = parts[i + 1].strip()
            if 0 <= n < len(indices) and content:
                out[indices[n]] = content
        if len(out) >= max(1, int(len(indices) * 0.8)):
            return out

        # P2: \n\n 段數對得上
        nn_parts = [p.strip() for p in translated.split("\n\n") if p.strip()]
        if len(nn_parts) == len(indices):
            return dict(zip(indices, nn_parts))

        # P3: degrade gracefully
        return {indices[0]: translated}

    def _flush(self) -> None:
        self._pkl.write_bytes(pickle.dumps({
            "para_translations": self._para_translations,
            "chunk_done": list(self._chunk_done),
            "chunk_raw": self._chunk_raw,
        }))
