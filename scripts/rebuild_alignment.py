"""不打 claude，用更寬鬆對齊邏輯把已存 progress.pkl 的 chunk_raw 重新切回 paragraph 寫 epub。

兩層對齊 fallback：
  P1: 嚴格 [[PARA_N]] marker 對齊（同 state._align）
  P2: 若對齊率 < 80%，用 \\n\\n 切段 + **比例分配**到 chunk.paragraph_indices
      （比現行「塞第一段」好很多，每段都有中文，雖然不精確）

用法：
    python rebuild_alignment.py --book <epub> [--out <new_epub>]
"""
from __future__ import annotations

import argparse
import pickle
import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from loader import EpubLoader
from chunker import Chunker, Chunk

MARKER_RE = re.compile(r"\[\[\s*PARA[\s_]*(\d+)\s*\]\]\s*", re.IGNORECASE)


def align_strict(chunk: Chunk, translated: str) -> dict[int, str]:
    indices = chunk.paragraph_indices
    parts = MARKER_RE.split(translated)
    out: dict[int, str] = {}
    for i in range(1, len(parts), 2):
        try:
            n = int(parts[i]) - 1
        except ValueError:
            continue
        if i + 1 >= len(parts):
            continue
        content = parts[i + 1].strip()
        if 0 <= n < len(indices) and content:
            out[indices[n]] = content
    return out


def align_proportional(chunk: Chunk, translated: str) -> dict[int, str]:
    """fallback：把整段中文按 \\n\\n 切後，比例 / 平均分配到 paragraphs。

    若切出 N1 段對齊 N2 paragraph：
      - N1 == N2: 一對一
      - N1 < N2: 把 N1 段平均填到 N2 paragraph 位置（每幾個 paragraph 共享一段）
      - N1 > N2: 多餘段合併到最後一段
    """
    indices = chunk.paragraph_indices
    if not indices:
        return {}
    # 移除 marker 殘留
    cleaned = MARKER_RE.sub("", translated)
    parts = [p.strip() for p in re.split(r"\n\s*\n", cleaned) if p.strip()]
    if not parts:
        return {indices[0]: translated.strip()}

    n1, n2 = len(parts), len(indices)
    out: dict[int, str] = {}
    if n1 == n2:
        return dict(zip(indices, parts))
    if n1 < n2:
        # 每個 paragraph 對應 parts[round(i * n1 / n2)]
        for i, p_idx in enumerate(indices):
            j = min(int(i * n1 / n2), n1 - 1)
            out[p_idx] = parts[j]
        return out
    # n1 > n2：把多餘段合併到最後
    chunk_size = n1 // n2
    for i, p_idx in enumerate(indices):
        start = i * chunk_size
        end = (i + 1) * chunk_size if i < n2 - 1 else n1
        out[p_idx] = "\n\n".join(parts[start:end])
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--book", required=True)
    p.add_argument("--out", default=None, help="輸出路徑，預設覆蓋既有 *_bilingual.epub")
    p.add_argument("--chunk-tokens", type=int, default=1500)
    args = p.parse_args()

    book = Path(args.book).expanduser().resolve()
    run_dir = SKILL_DIR / "runs" / book.stem
    pkl = run_dir / "progress.pkl"
    if not pkl.exists():
        print(f"ERROR: no progress.pkl at {pkl}", file=sys.stderr)
        return 2

    data = pickle.loads(pkl.read_bytes())
    chunk_raw = data.get("chunk_raw", {})
    print(f"[load] {len(chunk_raw)} chunks raw response")

    loader = EpubLoader(book)
    paragraphs = loader.extract_paragraphs()
    chunker = Chunker(max_tokens=args.chunk_tokens)
    chunks = chunker.build(paragraphs)
    print(f"[chunker] rebuilt {len(chunks)} chunks / {len(paragraphs)} paragraphs")

    para_trans: dict[int, str] = {}
    n_strict_ok = n_prop_used = n_empty = 0
    for chunk in chunks:
        raw = chunk_raw.get(chunk.idx, "")
        if not raw:
            n_empty += 1
            continue
        strict = align_strict(chunk, raw)
        if len(strict) >= max(1, int(len(chunk.paragraph_indices) * 0.8)):
            para_trans.update(strict)
            n_strict_ok += 1
        else:
            prop = align_proportional(chunk, raw)
            para_trans.update(prop)
            n_prop_used += 1

    print(f"[align] strict OK: {n_strict_ok} / proportional fallback: {n_prop_used} / empty raw: {n_empty}")
    print(f"[align] 涵蓋 paragraphs: {len(para_trans)} / {len(paragraphs)} = {len(para_trans)/max(len(paragraphs),1)*100:.1f}%")

    out_path = Path(args.out) if args.out else book.with_name(f"{book.stem}_bilingual.epub")
    loader.write_final_bilingual(para_trans, out_path)
    print(f"[done] {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
