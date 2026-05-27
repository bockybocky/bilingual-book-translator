# bilingual_book_maker 方法論萃取

source: github.com/yihong0618/bilingual_book_maker (main, 2026-05-13 抓)
purpose: 為自寫 bilingual-book-translator skill 萃取可借鏡的設計，不直接 fork

---

## 1. 架構分層

```
loader/      檔案格式解析（epub/pdf/txt/md/srt）— 不碰 LLM
translator/  LLM provider（claude / openai / gemini / ollama / ...）— 不碰檔案
__main__.py + cli.py  組合 loader + translator
```

可借鏡：**loader/translator 解耦**。我們只要寫 1 個 translator（`claude -p` subprocess），loader 用 ebooklib 直接寫，不必裝 bbook_maker。

## 2. epub_loader 核心招式

| 招 | 做法 | 為什麼重要 |
|---|---|---|
| **結構保留** | `ebooklib.read_epub()` + monkey patch；保留 metadata / spine / TOC / 圖片 / CSS | 雙語 epub 在 Apple Books / Kindle 開得開 |
| **可譯標籤白名單** | 預設只翻 `<p>`；可加 `h1/h2/...`；`exclude_translate_tags` 排除 `<code>/<sup>` | 不去動程式碼塊、圖片 caption、註腳上標 |
| **filter_nest_list()** | 同段落已選的父 tag，跳過子 tag | 避免重譯導致段落破裂 |
| **`_is_special_text()`** | 跳純數字 / 空白 / 標點 / URL-like | 省 LLM 呼叫 |
| **token-based chunking** | `accumulated_num` 用 token 算，到門檻才 flush 一批 | 不依長度 char count，避開 token 邊界爆掉 |
| **context_paragraph_limit=5** | rolling window 把前 5 段 source+translated 餵下一次當 context | 保上下文連貫（重要！單段翻譯會丟人名、it/this 指代） |
| **進度持久化** | pickle 到 `.{book}.temp.bin`；每 20 段存一次；resume 跳過 index < p_to_save_len | 翻一半當機可續、API quota 撞了可重來 |
| **temp epub 即時寫** | 每存 20 段同時寫 `_bilingual_temp.epub` | 可邊翻邊預覽品質，不必等全跑完 |

## 3. claude_translator 招式

- **prompt 簡單到驚人**：
  ```
  user: Help me translate the text within triple backticks into {language}
        and provide only the translated result.
        ```{text}```
  system: (empty)
  ```
- **history rolling window**：context_flag=True 時，把前 N 對 source+translation 串成 `\n\n` 拼前面當 messages history
- **無 retry / 無 rotate_key**：原版 retry 邏輯沒寫（`rotate_key()` 是空 pass）— 這是缺口，我們要補
- **參數**：model=claude-haiku-4-5 / temperature=1.0 / max_tokens=4096 / timeout=20s

## 4. 缺口（我們要補的）

bbook_maker 不夠的地方：

1. **glossary / 專有名詞表完全沒做**（金融書術語亂翻很痛苦：option 翻「選擇」/ tail risk 翻「尾巴風險」）
2. **retry / rate limit 沒寫**
3. **CC subscription 不支援**（只走 API key 按量計費）
4. **multi-pass review 沒做**（譯完一段不會回看是否符合上下文 / glossary）

## 5. 我們 skill 的設計借鏡 vs 創新

### 借鏡（直接抄）
- loader / translator 解耦
- epub 結構保留招式（ebooklib + 白名單 `<p>` + exclude `<code>`）
- token-based chunking
- context rolling window（前 5 段）
- 每 20 段存 pickle + temp.epub 預覽

### 創新（補缺口）
- LLM call 走 `claude -p` subprocess，吃 CC 訂閱額度（核心目標）
- 加 glossary.yaml：金融術語 / 人名地名 / 已固定譯法
- 加 retry：subprocess 失敗 / 空回應 / claude rate limit 訊息 → 指數退避重試
- 加 multi-pass：先全篇粗譯 → 抽 glossary 候選 → User 校 glossary → 二次精譯
- 失敗模式：所有跑過的 prompt + response 落地 `~/.claude/skills/bilingual-book-translator/runs/{book}/log/`，可事後復盤

## 6. 開發優先序建議

P0（MVP，跑得起來就好）：
- epub only（先不碰 pdf）
- 單 pass 翻譯
- claude -p subprocess + 簡單 retry
- 結構保留 + context rolling

P1：
- glossary 注入
- temp.epub 預覽
- pickle resume

P2：
- pdf / txt / md / srt
- multi-pass review

---

*Task 1 deliverable, 2026-05-13*
