# bilingual-book-translator — Skill Spec v0.1

status: DRAFT（等 User ack 後進 Task 3 用 skillify 實作）
date: 2026-05-13

---

## 1. 觸發詞與 args

**Skill 名**：`bilingual-book-translator`

**觸發詞**：
- 「翻譯這本書 {path}」
- 「epub 雙語化」
- 「/bilingual-book-translator {path}」
- 「把 {path} 翻成繁中雙語」

**args**（CLI 形式給腳本用）：

| arg | 必填 | 預設 | 說明 |
|---|---|---|---|
| `--book` | ✅ | — | epub 路徑（P0 只支援 epub） |
| `--lang` | ❌ | `Traditional Chinese` | 目標語言；可填 `Simplified Chinese` |
| `--model` | ❌ | `sonnet` | `haiku` / `sonnet` / `opus` 三選一；alias 對應到 claude-haiku-4-5-20251001 / claude-sonnet-4-6 / claude-opus-4-7 |
| `--out` | ❌ | `{book}_bilingual.epub` 同目錄 | 輸出檔路徑 |
| `--glossary` | ❌ | 無 | yaml 路徑，e.g. `~/.claude/skills/bilingual-book-translator/glossary/finance.yaml` |
| `--chunk-tokens` | ❌ | `1500` | 每 chunk token 上限（估 5-15s/chunk） |
| `--context-paragraphs` | ❌ | `5` | rolling window |
| `--resume` | ❌ | `true` | 從 `.{book}.temp.bin` 續跑 |
| `--dry-run` | ❌ | `false` | 只 parse + chunk 不打 claude，估 chunk 數 + 預估時間 |

## 2. 使用流程（用戶角度）

```
User: 翻譯 ~/Documents/books/superforecasting.epub 成繁中
Iris:    [估算] 287 頁 / 約 124k token / 切 83 chunk / 預估 12-25 分鐘
         確認跑嗎？
User: 跑
Iris:    [跑 dry-run 驗 chunk 邊界 OK]
         [跑真實翻譯，每 20 chunk 寫 temp.epub + 進度回報]
         [完成] superforecasting_bilingual.epub 已寫出
         glossary 候選詞抽出 → ~/glossary/superforecasting_candidates.yaml
         要校 glossary 跑第二 pass 嗎？
User: （選） 校完跑 / 不用
```

## 3. 內部架構

```
~/.claude/skills/bilingual-book-translator/
├── SKILL.md              # 觸發 SOP（給 Claude Code 讀）
├── METHODOLOGY.md        # 已存在（Task 1 deliverable）
├── SPEC.md               # 本檔
├── scripts/
│   ├── translate.py      # 主入口（CLI + args parsing）
│   ├── loader.py         # epub 解析 / 結構保留 / 段落抽取
│   ├── translator.py     # claude -p subprocess wrapper（吃訂閱額度）
│   ├── chunker.py        # token-based chunking + context rolling
│   ├── glossary.py       # yaml load + 注入 prompt + 候選抽取
│   └── state.py          # pickle 進度持久化 + temp.epub 預覽
├── glossary/
│   ├── finance.yaml      # 金融術語（option=選擇權 / tail risk=尾部風險 ...）
│   └── _TEMPLATE.yaml    # 空模板
└── runs/                 # 跑過的 log（每本書一個 dir）
    └── {book_stem}/
        ├── progress.pkl
        ├── temp_bilingual.epub
        └── log/{timestamp}_chunk_{N}.json   # prompt + response 落地
```

## 4. claude -p call 規格（核心）

照 `reference_claude_cli_cron_sop.md` 4 道防線（24 天前寫，Task 3 開工前 verify）：

```python
CLAUDE_BIN = r"C:\Users\User\AppData\Roaming\npm\claude.cmd"
CLEAN_SETTINGS = r"C:\Users\User\scripts\line\claude_clean_settings.json"

SYSTEM_PROMPT = """You are a professional book translator. Translate
English text to {target_lang}, preserving meaning, tone, and technical
terms. Output ONLY the translation, no explanation, no quotes, no
markdown fences. Maintain paragraph breaks exactly as input."""

USER_PROMPT_TEMPLATE = """[Context from previous paragraphs - for continuity only, do NOT translate]
{context_pairs}

[Glossary - use these exact translations if encountered]
{glossary_subset}

[Translate the following to {target_lang}]
```
{chunk_text}
```
"""

MODEL_MAP = {
    "haiku":  "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus":   "claude-opus-4-7",       # 預設（Charles 21 本實戰用此）
}

env["CLAUDE_HOOK_BYPASS"] = "1"
subprocess.run([CLAUDE_BIN, "-p",
                "--model", MODEL_MAP[args.model],
                "--system-prompt", SYSTEM_PROMPT,
                "--settings", CLEAN_SETTINGS,
                "--no-session-persistence",
                "--disable-slash-commands"],
               input=user_prompt, ...)
```

**為什麼這樣 prompt**：
- 加 `[Context]` `[Glossary]` `[Translate]` 三段標記，避免 LLM 把 context / glossary 也翻一遍
- 三重 backtick 包 chunk，明確邊界
- system prompt 強調「只輸出翻譯，無解釋」防 LLM 加 「Here is the translation:」前綴

## 5. Retry / 失敗模式

| 失敗 | 偵測 | 動作 |
|---|---|---|
| subprocess timeout | `subprocess.TimeoutExpired` | 退避 5/15/45s 重試 3 次，仍失敗→pickle 標 chunk N 失敗，繼續下一 chunk，最後彙整 |
| 空回應 | `stdout.strip() == ""` | 同上 |
| claude rate limit | stdout 含 "rate limit" 關鍵字 | 退避 60s 重試 |
| 翻譯回 Iris persona | stdout 含「我是 Iris」 | 4 道防線檢查 fail，停跑寫 ERROR，要 User 介入 |
| epub parse fail | ebooklib exception | 立即停，寫 ERROR.md，不寫部分輸出 |
| chunk 過大爆 token | 預估 > max_tokens | chunker.py 內預檢，過大則 sentence-split 再切 |

**核心鐵律**：所有 chunk 的 prompt + response 都 JSON 落地 `runs/{book}/log/`。失敗可離線 replay 不必重新 parse epub。

## 6. Glossary 機制

**P0**：手動 yaml，跑前注入。格式：
```yaml
# ~/.claude/skills/bilingual-book-translator/glossary/finance.yaml
terms:
  option: 選擇權
  tail risk: 尾部風險
  black swan: 黑天鵝
  alpha: 超額報酬
  drawdown: 回撤
proper_nouns:
  Nassim Taleb: 塔雷伯
  Benoit Mandelbrot: 曼德博
```

只把 chunk 內出現的 term subset 注入 prompt（避免 5000 條 glossary 全塞）。

**P1**：跑完抽 glossary 候選 → 同英文重複出現 ≥3 次的人名 / 大寫片語 / 技術詞 → 列 `{book}_candidates.yaml` 等 User 校 → 跑第二 pass 精譯。

## 7. 成本估算（CC 訂閱額度）

假設條件：
- 一本 300 頁金融書 ≈ 80k 英文字 ≈ 104k input token
- 切 70 chunks × 1500 token
- 每 chunk response ~600 token Chinese
- claude -p 訂閱模式：算 message count + 模型分計，不直接算 token

**1 本 300p 書 message 估算（不含 retry）：~70-100 messages**

| 模型 | 5h 額度撞限風險 | 適合場景 |
|---|---|---|
| `haiku` | 低（幾乎不會撞） | 小說 / 科普 / 快速試譯 |
| `sonnet` | 中（約吃 1/3 5h 額度） | 金融書 / 一般技術書 |
| `opus`（預設）| 高（1 本書可能撞 5h 上限，但 quota sleep + pickle resume 自動接得回，Charles 21 本實戰） | 文學 / 哲學味重（Taleb / Mandelbrot）/ 深度作品 |

**Iris 跑前必檢**：
- Opus 模式 → 警告 5h 額度可能撞限，建議「短書（<150p）或分段跑」
- dry-run 階段印 model + chunk 數 + 預估 message 數 + 5h 額度建議

Task 3 開工後實測一本短書（~50p）抓真實速率，落地 `runs/{book}/log/_stats.json`。

## 8. 開發優先序（P0 MVP）

**P0 = 跑得起來、品質夠用、不爆**：
1. epub only
2. 單 pass 翻譯
3. context rolling 5 段
4. glossary yaml 手動載入 + 注入
5. retry 3 次
6. pickle resume + temp.epub 預覽
7. log 落地

**不在 P0**：
- pdf / txt / md / srt
- multi-pass review
- glossary 自動抽候選
- Discord 通知（用 stdout 進度條就好）

**P0 預估**：~250 行 Python（loader 80 / translator 60 / chunker 40 / state 40 / main 30）+ SKILL.md + 1 個 test。

## 9. 風險 & 待 User 確認

| 點 | 我的傾向 | 等 User 確認 |
|---|---|---|
| 預設目標語 | 繁體中文 | OK 嗎？（User 設定 zh-TW） |
| Chunk size | 1500 token | OK or 改 1000 求保險？ |
| glossary 預設載入 | 不預設，跑前 User 指定 `--glossary finance.yaml` | OK or 預設自動載 finance.yaml？ |
| temp.epub 寫頻率 | 每 20 chunks | OK or 改 10？ |
| skill 觸發詞 | `/bilingual-book-translator` + 中文觸發詞 4 個 | 加 short alias 如 `/bbm`？ |
| log 隱私 | runs/ 落地全 prompt + response | User 是否在意？要不要 .gitignore？|

## 10. Out of Scope（明確不做）

- 不 fork bilingual_book_maker（純借鏡方法論）
- 不裝 PyPI `bbook_maker`（避免 dependency 衝突）
- 不支援 Claude API key（強制走訂閱）
- 不支援 OpenAI / Gemini / DeepL（單一 provider 維護成本最低）
- 不做 GUI / web UI（CLI + skill 觸發足夠）

---

*Task 2 deliverable, 2026-05-13*
