# bilingual-book-translator — Mac CC 安裝指引

## 1. 解壓到 skills 目錄

```bash
# 確認 ~/.claude/skills/ 存在
mkdir -p ~/.claude/skills/

# 解壓到該目錄
unzip bilingual-book-translator-mac.zip -d ~/.claude/skills/
```

確認結果：`ls ~/.claude/skills/bilingual-book-translator/` 應該看到 `SKILL.md / scripts/ / glossary/`。

## 2. Python 套件依賴

```bash
cd ~/.claude/skills/bilingual-book-translator/
pip3 install -r requirements.txt
```

依賴：EbookLib / beautifulsoup4 / lxml / PyYAML / tiktoken

如果遇到 `lxml` 編譯問題（Mac M1/M2）：
```bash
brew install libxml2 libxslt
pip3 install --no-binary lxml lxml
```

## 3. 確認 Claude CLI 存在

```bash
which claude
# 應該回 /usr/local/bin/claude 或 ~/.claude/local/claude
```

如果 `which claude` 找不到 → 確認 Claude Code 已安裝。如需手動指定路徑：
```bash
export BILINGUAL_CLAUDE_BIN=/path/to/claude
```

## 4. 測試

```bash
python3 ~/.claude/skills/bilingual-book-translator/scripts/translate.py --help
```

應該印出 usage。

## 5. 跑第一本書

```bash
# 假設書放在 ~/Documents/books/example.epub
python3 ~/.claude/skills/bilingual-book-translator/scripts/translate.py \
    --book ~/Documents/books/example.epub
# 不指定 --model 即吃預設 opus（Charles 9 本實戰用法）
```

**預設模型 Opus 4.7**（Charles 9 本實戰用此 model，最準翻譯品質）。
可選 `--model sonnet`（較快、撞額度上限機會低）或 `--model haiku`（最快、品質較弱）。

⚠️ Opus 注意：211 chunks 量級可能撞 5h monthly limit → script 會自動 sleep 5h05min 恢復（不需手動）。如果你 CC 訂閱配額較緊，先試 sonnet。

## 6. 中斷恢復

- 每 chunk 自動寫 progress.pkl
- 中斷後再跑同樣命令 → 自動從上次 chunk 接續
- 撞 monthly limit → 自動 sleep 5h05min 後恢復

## 7. 結果

翻好的 epub 寫在原書同目錄下，檔名 `<原書>_bilingual.epub`。

## 注意

- **Mac 路徑用 `~` 或 `/Users/<你的 user>/`**，不要用 Windows 風格 `D:/` 或 `C:\`
- **訂閱額度**：本 skill 走 `claude -p` 子程序，吃 Claude Code 訂閱額度（不需 API key）
- **Claude.md 干擾**：4 道防線會自動 bypass 你的 hooks / system-prompt，不會被你的 Iris persona 等污染

## 問題回報

跑出問題 → 提供 `~/.claude/skills/bilingual-book-translator/runs/<book name>/log` 內容給原作者（Charles）。
