"""掃 progress.pkl 找出壞 chunks（quota error 訊息 / 無 CJK），清出 done set + raw + para_translations，
讓 translate.py --resume 重跑。

用法：
    python cleanup_invalid_chunks.py --book <epub-path>
    python cleanup_invalid_chunks.py --book <epub-path> --dry-run
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
from chunker import Chunker

QUOTA_MARKERS = [
    "5-hour limit", "5 hour limit",
    "usage limit reached", "claude usage limit reached",
    "monthly usage limit", "weekly usage limit",
    "you've hit your", "you have hit your",
    "try again at", "rate_limit_exceeded", "quota exceeded",
]
CJK_RE = re.compile(r'[一-鿿]')


def is_invalid(raw: str) -> tuple[bool, str]:
    if not raw:
        return True, "empty"
    low = raw.lower()
    for m in QUOTA_MARKERS:
        if m in low:
            return True, f"quota_marker: {m}"
    if not CJK_RE.search(raw):
        return True, "no_cjk"
    return False, ""


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--book", required=True)
    p.add_argument("--chunk-tokens", type=int, default=1500)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    book = Path(args.book).expanduser().resolve()
    run_dir = SKILL_DIR / "runs" / book.stem
    pkl = run_dir / "progress.pkl"
    if not pkl.exists():
        print(f"ERROR: no progress.pkl at {pkl}", file=sys.stderr)
        return 2

    data = pickle.loads(pkl.read_bytes())
    chunk_done = set(data.get("chunk_done", []))
    chunk_raw = data.get("chunk_raw", {})
    para_trans = data.get("para_translations", {})
    print(f"[load] {len(chunk_done)} chunks done / {len(para_trans)} paragraph translations")

    # 重建 chunker 以得到 chunk.idx → paragraph_indices 映射
    loader = EpubLoader(book)
    paragraphs = loader.extract_paragraphs()
    chunker = Chunker(max_tokens=args.chunk_tokens)
    chunks = chunker.build(paragraphs)
    chunk_to_paras = {c.idx: c.paragraph_indices for c in chunks}
    print(f"[chunker] rebuilt {len(chunks)} chunks")

    bad_chunks: list[tuple[int, str]] = []
    for idx in sorted(chunk_done):
        raw = chunk_raw.get(idx, "")
        invalid, reason = is_invalid(raw)
        if invalid:
            bad_chunks.append((idx, reason))

    print(f"\n[scan] 壞 chunks: {len(bad_chunks)} / {len(chunk_done)}")
    if bad_chunks:
        # 統計 reason
        from collections import Counter
        c = Counter(r for _, r in bad_chunks)
        for reason, cnt in c.most_common():
            print(f"   {cnt:4d}× {reason}")

    if args.dry_run:
        print("\n[dry-run] 不修改 progress.pkl")
        return 0

    if not bad_chunks:
        print("[ok] 沒有壞 chunk，不動 progress.pkl")
        return 0

    # 清除
    paras_to_remove: set[int] = set()
    for idx, _ in bad_chunks:
        chunk_done.discard(idx)
        chunk_raw.pop(idx, None)
        for p_idx in chunk_to_paras.get(idx, []):
            paras_to_remove.add(p_idx)
    for p_idx in paras_to_remove:
        para_trans.pop(p_idx, None)

    backup = pkl.with_suffix(".pkl.bak")
    pkl.rename(backup)
    pkl.write_bytes(pickle.dumps({
        "para_translations": para_trans,
        "chunk_done": list(chunk_done),
        "chunk_raw": chunk_raw,
    }))
    print(f"\n[cleanup] 清除 {len(bad_chunks)} chunks + {len(paras_to_remove)} paragraph translations")
    print(f"[backup]  舊 progress.pkl 備份到 {backup.name}")
    print(f"[next]    跑 `translate.py --book <path>` 會自動 resume 補翻這些 chunks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
