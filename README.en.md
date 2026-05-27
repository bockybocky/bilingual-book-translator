# bilingual-book-translator

[繁體中文](README.md) | **English**

> 📖 **This project primarily serves Chinese-speaking readers; default README is in Traditional Chinese.**
> 本專案主要面向華人讀者，預設 README 為繁體中文。

> ⚠️ **Copyright Notice**
>
> This tool provides **translation technology only** and ships with no book content.
>
> **All translation output is for personal reference only.** Original books are copyrighted by their authors and publishers.
>
> **Please respect intellectual property: if you genuinely value an author's work, buy the original to support continued creation.**
>
> Do not distribute, resell, or upload translated output to any public platform. The tool authors are not responsible for any user actions that violate copyright law.

---

A Claude Code skill that turns English EPUB books into **bilingual (English + Traditional Chinese) EPUBs** using your Claude Code subscription — **no API key required**.

Built on the methodology of [`yihong0618/bilingual_book_maker`](https://github.com/yihong0618/bilingual_book_maker), but routes every LLM call through `claude -p` subprocess so it consumes your existing CC Pro/Max subscription quota instead of pay-per-token API credits.

**Production-validated across multiple book genres** including literature, philosophy, business, popular science, and history.

## Why

`bilingual_book_maker` is the gold standard for bilingual EPUB generation, but it requires API key billing. If you already pay for Claude Code subscription, there's no reason to pay twice. This skill borrows the structural approach (paragraph-level alignment, token chunking, rolling context, pickle resume) and ports the LLM call to the subscription channel.

## Features

- **Structure-preserving** — Each English paragraph followed by its Chinese translation, preserving HTML/EPUB structure (chapters, blockquotes, lists, footnotes)
- **Smart chunking** — Groups paragraphs into ~1500-token chunks for context, never splits a paragraph
- **Rolling context** — 5-paragraph rolling window for cross-paragraph coherence
- **Pickle resume** — Crashes / quota hits / reboots → next run picks up exactly where it stopped
- **Quota-aware sleep** — Parses Anthropic's reset time from `claude -p` stderr, sleeps to `reset + 5min`, then auto-resumes (fallback: fixed 5h05min)
- **Already-translated detection** — Scans common output locations before running; skips zero-token if the bilingual EPUB already exists. Saves hours on batch reruns
- **Post-translation patch** — `patch_missing_paragraphs.py` catches LLM skip-translation behavior (block quotes, dialogue, prompt-style sentences) that strict alignment can't detect
- **Anti-prompt-injection prompt** — Hardened SYSTEM_PROMPT treats source text as content-to-translate even when it looks like an instruction ("Put yourself in X's position" → translates, doesn't comply)
- **Cross-platform claude CLI detection** — Mac (Homebrew / user-local) / Linux / Windows (npm-installed) via `shutil.which` + env var override
- **Glossary support** — YAML-based term consistency (finance glossary included; template provided)
- **3 model tiers** — Opus 4.7 (default, best quality), Sonnet 4.6 (faster, fewer quota hits), Haiku 4.5 (fastest, for novels / pop sci)

## Installation

### Prerequisites

- [Claude Code](https://www.anthropic.com/claude-code) installed and authenticated
- Python 3.10+
- Dependencies:

```bash
pip install -r requirements.txt
```

### Install as a Claude Code skill

Clone into your skills directory:

```bash
# Mac / Linux
git clone https://github.com/bockybocky/bilingual-book-translator.git \
  ~/.claude/skills/bilingual-book-translator

# Windows
git clone https://github.com/bockybocky/bilingual-book-translator.git ^
  %USERPROFILE%\.claude\skills\bilingual-book-translator
```

After installation, the skill is available as `/bilingual-book-translator` in Claude Code, or triggers on phrases like "translate this book {path}", "epub 雙語化".

### Or run as a standalone script

```bash
python scripts/translate.py --book mybook.epub --model opus
```

## Quick Start

### Translate a single book

```bash
python scripts/translate.py --book ~/Documents/books/mybook.epub
```

Output: `mybook_bilingual.epub` in the same directory as the source.

### Dry-run (estimate chunks + time, no API calls)

```bash
python scripts/translate.py --book mybook.epub --dry-run
```

### Use a glossary for term consistency

```bash
python scripts/translate.py --book trading_book.epub --glossary glossary/finance.yaml
```

### Catch missed paragraphs after translation

```bash
# Dry-run first to see what's missing
python scripts/patch_missing_paragraphs.py --book mybook_bilingual.epub --dry-run

# Then patch (auto-backs up to .epub.bak.patch)
python scripts/patch_missing_paragraphs.py --book mybook_bilingual.epub --model opus
```

### Skip books already translated (save tokens on reruns)

`translate.py` automatically detects existing `{book}_bilingual.epub` files. Set `BILINGUAL_BOOKS_DIR` to point at your batch output directories:

```bash
# Mac / Linux (colon-separated)
export BILINGUAL_BOOKS_DIR="$HOME/Documents/books:$HOME/Downloads/books"

# Windows (semicolon-separated)
set BILINGUAL_BOOKS_DIR=D:\books;C:\Users\Me\Downloads\books
```

Force a re-translation with `--force`.

## Methodology

See [`METHODOLOGY.md`](METHODOLOGY.md) for the design rationale extracted from `bilingual_book_maker`'s source — paragraph extraction strategy, chunking heuristics, retry policy, glossary multi-pass design.

See [`SPEC.md`](SPEC.md) for the full skill specification (args, architecture, failure modes, cost model).

## Authors

- **[Charles](https://github.com/bockybocky)** — Original skill design, methodology extraction, Windows / Iris integration, 21-book production validation
- **[Fred Chu](https://github.com/fredchu)** — `patch_missing_paragraphs.py` post-translation QA tool, cross-platform `claude` CLI detection, quota sleep refactor, anti-prompt-injection SYSTEM_PROMPT, generalization for public release

## License

MIT — see [LICENSE](LICENSE).

## Related

- [`yihong0618/bilingual_book_maker`](https://github.com/yihong0618/bilingual_book_maker) — The original API-based bilingual EPUB generator. Use this if you prefer pay-per-token API billing or need providers other than Claude.

## Contributing

Issues and PRs welcome. Especially appreciated:

- More glossaries (medicine, law, philosophy, etc.)
- PDF / MOBI support beyond P0 EPUB
- Second-pass glossary refinement (collect proper-noun candidates → user-curate → re-translate with locked glossary)
- Translation quality benchmarks across model tiers
