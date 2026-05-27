"""Token-based chunking + context rolling。借鏡 bbook_maker accumulated_num 機制。"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        return len(_ENC.encode(text))
except Exception:
    def count_tokens(text: str) -> int:
        return max(1, len(text) // 3)


@dataclass
class Chunk:
    idx: int
    paragraph_indices: list[int]
    text: str
    token_count: int


PARA_OPEN = "[[PARA_{}]]"


@dataclass
class Chunker:
    max_tokens: int = 1500

    def build(self, paragraphs) -> list[Chunk]:
        chunks: list[Chunk] = []
        buf_texts: list[str] = []
        buf_idx: list[int] = []
        buf_tokens = 0

        for p in paragraphs:
            t = count_tokens(p.text)
            if buf_texts and buf_tokens + t > self.max_tokens:
                chunks.append(self._flush(len(chunks), buf_idx, buf_texts, buf_tokens))
                buf_texts, buf_idx, buf_tokens = [], [], 0
            buf_texts.append(p.text)
            buf_idx.append(p.idx)
            buf_tokens += t

            if t > self.max_tokens:
                chunks.append(self._flush(len(chunks), buf_idx, buf_texts, buf_tokens))
                buf_texts, buf_idx, buf_tokens = [], [], 0

        if buf_texts:
            chunks.append(self._flush(len(chunks), buf_idx, buf_texts, buf_tokens))
        return chunks

    def _flush(self, idx, indices, texts, tokens) -> Chunk:
        # 段落用 [[PARA_N]] marker 包，讓 LLM 翻譯後可按 marker 切回對齊
        body = "\n\n".join(f"{PARA_OPEN.format(i+1)}\n{t}" for i, t in enumerate(texts))
        return Chunk(idx=idx, paragraph_indices=list(indices),
                     text=body, token_count=tokens)


@dataclass
class ContextWindow:
    """rolling 前 N 對 (英, 中)，注入下次 prompt 當 context。"""
    max_pairs: int = 5
    _pairs: deque = field(default_factory=lambda: deque(maxlen=5))

    def __post_init__(self):
        self._pairs = deque(maxlen=self.max_pairs)

    def push(self, src: str, dst: str) -> None:
        self._pairs.append((src, dst))

    def render(self) -> str:
        if not self._pairs:
            return "(no previous context)"
        out = []
        for en, zh in self._pairs:
            en_snip = en[:200] + ("…" if len(en) > 200 else "")
            zh_snip = zh[:200] + ("…" if len(zh) > 200 else "")
            out.append(f"EN: {en_snip}\nZH: {zh_snip}")
        return "\n---\n".join(out)
