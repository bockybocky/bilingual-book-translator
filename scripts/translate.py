"""bilingual-book-translator 主入口

CLI 範例：
    python translate.py --book ~/Documents/books/foo.epub --model sonnet --glossary glossary/finance.yaml
    python translate.py --book foo.epub --dry-run
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from loader import EpubLoader
from chunker import Chunker
from translator import ClaudeTranslator, MODEL_MAP, AUPRefuseError
from glossary import Glossary
from state import State


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="EPUB 英→雙語翻譯（走 claude -p 訂閱額度）")
    p.add_argument("--book", required=True, help="epub 路徑")
    p.add_argument("--lang", default="Traditional Chinese")
    p.add_argument("--model", default="opus", choices=list(MODEL_MAP.keys()))
    p.add_argument("--out", default=None, help="輸出路徑，預設 {book}_bilingual.epub")
    p.add_argument("--glossary", default=None, help="yaml glossary 路徑")
    p.add_argument("--chunk-tokens", type=int, default=1500)
    p.add_argument("--context-paragraphs", type=int, default=5)
    p.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-chunks", type=int, default=0, help="只跑前 N chunks（smoke test 用，0=全跑）")
    p.add_argument("--force", action="store_true",
                   help="忽略已翻檢測，強制重跑（預設遇到已存在 _bilingual.epub 直接 skip 省 token）")
    return p.parse_args()


def find_existing_translations(book_path: Path, out_path: Path) -> list[Path]:
    """掃常見位置看 {stem}_bilingual.epub 是否已存在（省 token 已翻檢測）。

    搜尋順序：
    1. out_path 本身（同源資料夾）
    2. book_path 同目錄
    3. env var `BILINGUAL_BOOKS_DIR` 指定的目錄（多個用 os.pathsep 分隔；Mac/Linux=`:`, Win=`;`）
       例：Mac/Linux → `export BILINGUAL_BOOKS_DIR="$HOME/Documents/books:$HOME/Downloads/books"`
       例：Windows   → `set BILINGUAL_BOOKS_DIR=D:\\books;C:\\Users\\Me\\Downloads\\books`
       未設則預設只搜 `~/Downloads/books`
    4. SKILL/runs/{stem}/temp_bilingual.epub（半翻過的）
    """
    import os
    stem = book_path.stem
    target_name = f"{stem}_bilingual.epub"
    candidates = []

    if out_path.exists():
        candidates.append(out_path)

    same_dir = book_path.parent / target_name
    if same_dir.exists() and same_dir != out_path:
        candidates.append(same_dir)

    home = Path.home()
    env_dirs = os.environ.get("BILINGUAL_BOOKS_DIR", "")
    search_roots = [Path(p).expanduser() for p in env_dirs.split(os.pathsep) if p.strip()]
    if not search_roots:
        search_roots = [home / "Downloads/books"]

    for root in search_roots:
        if not root.exists():
            continue
        # 只掃 2 層深，避免太慢
        for level1 in root.iterdir():
            if not level1.is_dir():
                continue
            for level2 in [level1] + [d for d in level1.iterdir() if d.is_dir()]:
                cand = level2 / target_name
                if cand.exists() and cand not in candidates:
                    candidates.append(cand)

    temp = SKILL_DIR / "runs" / stem / "temp_bilingual.epub"
    if temp.exists():
        candidates.append(temp)

    return candidates


def estimate_messages(n_chunks: int) -> tuple[int, int]:
    """估算 claude -p message 數 (low, high) — 不含 retry。"""
    return n_chunks, int(n_chunks * 1.1)


def warn_opus_quota(n_chunks: int) -> str | None:
    if n_chunks > 80:
        return (
            f"⚠️ Opus 模式 + {n_chunks} chunks，可能撞 5h 額度上限。\n"
            "   建議：(a) 分 session 跑（resume 會接）/ (b) 改 --model sonnet"
        )
    return None


def main() -> int:
    args = parse_args()
    book_path = Path(args.book).expanduser().resolve()
    if not book_path.exists():
        print(f"ERROR: book not found: {book_path}", file=sys.stderr)
        return 2
    if book_path.suffix.lower() != ".epub":
        print(f"ERROR: P0 只支援 .epub（got {book_path.suffix}）", file=sys.stderr)
        return 2

    out_path = Path(args.out) if args.out else book_path.with_name(f"{book_path.stem}_bilingual.epub")
    run_dir = SKILL_DIR / "runs" / book_path.stem
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "log").mkdir(exist_ok=True)

    # 已翻檢測 — 省 token 鐵律：除非 --force，遇到已存在 _bilingual.epub 直接 skip
    if not args.force:
        from datetime import datetime
        existing = find_existing_translations(book_path, out_path)
        # 過濾掉 temp_bilingual.epub（半成品，不算 done）
        final = [p for p in existing if p.name != "temp_bilingual.epub"]
        if final:
            print(f"\n[已翻過] {book_path.name} 之前已翻譯，發現 {len(final)} 個 _bilingual.epub：")
            for p in final:
                sz_mb = p.stat().st_size / 1024 / 1024
                mt = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                print(f"    {p}  ({sz_mb:.1f} MB, {mt})")
            print(f"[skip] 不重跑省 token。如要強制重翻：加 --force")
            return 0
        # temp_bilingual 存在 = 半翻過 → 提示但繼續（resume 邏輯接得回）
        temp_only = [p for p in existing if p.name == "temp_bilingual.epub"]
        if temp_only:
            print(f"[half-translated] 偵測到 {temp_only[0]} — resume 會接續未完成的 chunks")

    print(f"[load] {book_path.name}")
    loader = EpubLoader(book_path)
    paragraphs = loader.extract_paragraphs()
    print(f"[load] {len(paragraphs)} translatable paragraphs across {loader.n_chapters} chapters")

    chunker = Chunker(max_tokens=args.chunk_tokens)
    chunks = chunker.build(paragraphs)
    n_chunks = len(chunks)
    n_tokens = sum(c.token_count for c in chunks)
    print(f"[chunk] {n_chunks} chunks / {n_tokens} tokens (avg {n_tokens // max(n_chunks,1)})")

    msg_low, msg_high = estimate_messages(n_chunks)
    eta_low = n_chunks * 6
    eta_high = n_chunks * 18
    print(f"[estimate] model={args.model} | messages: {msg_low}-{msg_high} | ETA: {eta_low//60}-{eta_high//60} min")

    if args.model == "opus":
        warning = warn_opus_quota(n_chunks)
        if warning:
            print(warning)

    if args.dry_run:
        print("[dry-run] done. 不打 claude。")
        return 0

    glossary = Glossary(Path(args.glossary)) if args.glossary else Glossary(None)
    state = State(run_dir, resume=args.resume)
    translator = ClaudeTranslator(
        model_alias=args.model,
        target_lang=args.lang,
        context_paragraphs=args.context_paragraphs,
        log_dir=run_dir / "log",
    )

    completed = state.completed_indices()
    if completed:
        print(f"[resume] 跳過已完成 {len(completed)} chunks，從 chunk {max(completed)+1} 起")

    t_start = time.time()
    for i, chunk in enumerate(chunks):
        if args.max_chunks and i >= args.max_chunks:
            print(f"[smoke] 已跑滿 --max-chunks {args.max_chunks}，提早停。")
            break
        if i in completed:
            translator.push_context(chunk.text, state.get_translation(i))
            continue

        elapsed = time.time() - t_start
        done = i - len(completed)
        rate = done / max(elapsed, 1e-6)
        eta_s = (n_chunks - i) / max(rate, 1e-6) if rate > 0 else 0
        print(f"[{i+1}/{n_chunks}] {chunk.token_count}t...", end="", flush=True)

        glossary_subset = glossary.lookup_in_text(chunk.text)
        try:
            zh = translator.translate(chunk, glossary_subset, idx=i)
        except AUPRefuseError as e:
            # AUP refuse — 該 chunk 跳過，epub 內保留原文 + 中文標註
            zh = f"[未翻譯-Claude 政策拒答，保留原文 / Section not translated due to AUP refuse]\n\n{chunk.text}"
            state.save_chunk(chunk, zh)
            print(f" ⚠️ AUP refuse, skipped (原文保留)")
            import json
            skip_log = run_dir / "AUP_SKIPPED.json"
            existing = json.loads(skip_log.read_text(encoding='utf-8')) if skip_log.exists() else []
            existing.append({"idx": i, "error": str(e)[:300]})
            skip_log.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding='utf-8')
            continue
        state.save_chunk(chunk, zh)
        print(f" ✓ ({translator.last_elapsed_s:.1f}s) ETA: {int(eta_s//60)}m")

        if (i + 1) % 20 == 0:
            loader.write_temp_bilingual(state.all_translations(), run_dir / "temp_bilingual.epub")
            print(f"[checkpoint] temp_bilingual.epub 寫出")

    loader.write_final_bilingual(state.all_translations(), out_path)
    elapsed = int(time.time() - t_start)
    print(f"\n[done] {out_path}  ({elapsed//60}m{elapsed%60}s, {translator.n_retries} retries)")
    print(f"[log]  {run_dir / 'log'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
