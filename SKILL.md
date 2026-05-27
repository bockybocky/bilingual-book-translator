---
name: bilingual-book-translator
description: 把英文 epub 電子書翻成中英雙語 epub，LLM call 走 `claude -p` subprocess 吃 CC 訂閱額度（不必 API key）。借鏡 yihong0618/bilingual_book_maker 方法論：結構保留 + token chunking + context rolling 5 段 + pickle resume。預設 Opus 4.7（最準，Charles 21 本實戰用此），可選 Sonnet 4.6 / Haiku 4.5。觸發詞：「翻譯這本書 {path}」「epub 雙語化」「/bilingual-book-translator {path}」「把 {path} 翻成繁中雙語」。當 Charles 提供 epub 路徑要轉雙語、或想用 CC 額度跑書籍翻譯時觸發。P0 MVP：epub only / 單 pass / 手動 glossary。
type: tool
---

# bilingual-book-translator

把英文 epub 翻成中英雙語 epub，吃 CC 訂閱額度（走 `claude -p` subprocess），不必 API key。

## 為什麼有這個 skill

`yihong0618/bilingual_book_maker` 是業界標竿，但只支援 API key 按量計費。User 已付 CC 訂閱，沒道理另花錢。本 skill 借鏡 bbook_maker 方法論，把 LLM call 改走 `claude -p` 訂閱通道。

## 使用範例

```
User: 翻譯 ~/Documents/books/superforecasting.epub 成繁中
Iris:    [run translate.py --book ~/Documents/books/superforecasting.epub --dry-run]
         287 頁 / 約 124k token / 切 83 chunk / 預估 12-25 分鐘（Sonnet 4.6）
         確認跑嗎？
User: 跑
Iris:    [run translate.py 真實翻譯]
         chunk 1/83 ✓ (8s) ...
         chunk 20/83 ✓ 已寫 temp_bilingual.epub 預覽
         ...
         完成。superforecasting_bilingual.epub 已寫出。
         log: ~/.claude/skills/bilingual-book-translator/runs/superforecasting/log/
```

## 觸發 SOP（給 Claude Code 讀）

1. 從用戶訊息抽 epub 路徑（必要）+ 模型偏好（選填）+ glossary 偏好（選填）
2. 跑 `python scripts/translate.py --book {path} --dry-run` 估 chunk 數 + 時間
   - **已翻檢測自動跑**：translate.py 內建掃多個常見位置（同源資料夾 / env var `BILINGUAL_BOOKS_DIR` 指定的目錄 / `~/Downloads/books/**` 預設 / `runs/{stem}/temp_bilingual.epub`）找已存在的 `{stem}_bilingual.epub`，找到就直接 skip 印位置+size+mtime，省 token。要強制重翻加 `--force`
   - 自訂搜尋位置：`export BILINGUAL_BOOKS_DIR="~/Documents/books:~/Downloads/books"`（Mac/Linux，`:` 分隔）/ `set BILINGUAL_BOOKS_DIR=D:\books;C:\Users\Me\Downloads\books`（Windows，`;` 分隔）
3. 報估算（含已翻檢測結果），問用戶確認
4. 跑 `python scripts/translate.py --book {path} [--model X] [--glossary Y]`
5. 過程中每 20 chunk 印一次進度
6. 完成後**必跑** `patch_missing_paragraphs.py --dry-run` 掃漏譯（見下節）— LLM 對短引述/口語段有跳譯傾向，`rebuild_alignment` 看不出來
7. 若漏譯 > 0，用 `patch_missing_paragraphs.py` 補翻（無 `--dry-run`）
8. 回報輸出路徑 + log dir + 漏譯掃描結果

### 批次跑多本：已翻檢測讓「重跑無痛」

跑批次（如 `batch_translate.py`）時，已翻過的 epub 全部 0 秒 skip — 從 dry-run 階段就攔下不打 claude。實戰：5/27 batch 6 本中 5 本之前翻過，實際只跑 1 本新書 + 半本剩餘，省下數小時 + 數百 chunks token。

## Post-translation QA（補翻漏譯段）

### 為什麼需要
跑 `translate.py` 後 LLM 可能跳譯某些段落，特別是：
- block quote / 對白引述（小說台詞、訪談、引用詩文）
- 短句加腳注編號（如 `Go to Father.1`）
- prompt-style 短句（"Put yourself in X's position. What is she thinking?"）— `claude -p` 會誤判為對自己的指令
- 名詞定義清單（如 `bluff: Amy wants Brad to believe...`）
- 整章推薦語（praise.xhtml）

`rebuild_alignment.py` 會回報 100% strict OK 但實際上 LLM 整段沒翻 — marker 對齊正常但中文是空的，這種情況無法用對齊修。

### 使用
```bash
# 1. dry-run 掃漏譯
python scripts/patch_missing_paragraphs.py --book <bilingual.epub> --dry-run

# 2. 實際補翻（會自動 backup 為 .epub.bak.patch）
python scripts/patch_missing_paragraphs.py --book <bilingual.epub> --model opus
```

### 行為
- 掃所有 xhtml，找「連續兩個真實英文段（>60 字 + ≥10 單字）」位置
- 排除 bibliography / endnotes / cover / praise 等 metadata 章節（filename heuristic + content heuristic）
- 每個漏譯位置呼叫 `claude -p` 翻譯該段，插入 `<p class="patch-translated">中文</p>` 在原段後
- 二次跑會先 cleanup 前次失敗的 patch（內容非中文的 `<p class="patch-translated">`）— 處理 prompt-style 短句被 claude 拒譯的情況
- 跑完重打包覆寫 epub（mimetype 仍為 STORED）

### 預估
- 100 段 opus ≈ 30-50 min（每段獨立 `claude -p` 呼叫，不會走 chunk batching）
- 不太會撞 quota — 單段 token 量小

### 已知 false positive（可忽略）
- 純數字段（`6.35%`、`7.29%`）
- `***` 等分隔符
- 表格 cell（短英文標題）
- 已被 SKIP_FILES regex 過濾的章節（endnotes/bibliography/index 等）— 看到 7 處全在 `-19.xhtml` 之類 endnote 檔，是書目引註不需翻

## args 參考（詳見 SPEC.md §1）

| arg | 預設 | 說明 |
|---|---|---|
| `--book` | 必填 | epub 路徑 |
| `--lang` | `Traditional Chinese` | 目標語言 |
| `--model` | `opus` | `haiku` / `sonnet` / `opus` |
| `--out` | `{book}_bilingual.epub` | 輸出路徑 |
| `--glossary` | 無 | yaml 路徑（金融書建議用 `glossary/finance.yaml`） |
| `--chunk-tokens` | `1500` | 每 chunk token 上限 |
| `--context-paragraphs` | `5` | rolling window |
| `--resume` | `true` | 從 pickle 續跑 |
| `--dry-run` | `false` | 只 parse 不打 claude |

## 模型選擇提示

- 小說 / 科普 / 快速試譯 → `haiku`
- 金融書 / 一般技術書 / 一般非小說 → `sonnet`（較快、撞 5h 上限機會低）
- 文學 / 哲學味重（Taleb / Mandelbrot）/ 深度作品 → `opus`（**預設**，Charles 21 本實戰用此；長書會撞 5h 額度但 quota sleep + pickle resume 自動接得回）

## 文件索引

- `METHODOLOGY.md` — 從 bilingual_book_maker source 萃取的方法論
- `SPEC.md` — 完整 skill spec（args / 架構 / failure modes / cost）
- `scripts/` — Python 實作（translate.py 主入口 + loader / translator / chunker / glossary / state + `patch_missing_paragraphs.py` 補翻漏譯 + `rebuild_alignment.py` 對齊修復 + `cleanup_invalid_chunks.py` 清壞 chunk）
- `glossary/finance.yaml` — 金融術語起手包
- `glossary/_TEMPLATE.yaml` — 空模板
- `runs/{book}/` — 每本書的 pickle + temp epub + log

## 依賴

```bash
pip install EbookLib beautifulsoup4 lxml tiktoken PyYAML
```

## 觸發詞

「翻譯這本書 {path}」、「epub 雙語化」、「/bilingual-book-translator {path}」、「把 {path} 翻成繁中雙語」、「bbook 翻譯」
