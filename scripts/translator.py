"""claude -p subprocess wrapper — 吃 CC 訂閱額度。

4 道防線（照 reference_claude_cli_cron_sop.md，2026-05-13 verified）：
  ① CLAUDE_HOOK_BYPASS env → patched hooks 看到立即 exit
  ② --settings empty hooks
  ③ --system-prompt override Iris persona
  ④ stdin pipe 避 cmd.exe arg 截斷
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from chunker import Chunk, ContextWindow

import shutil

def _detect_claude_bin() -> str:
    """Cross-platform claude CLI 偵測. 支援 Mac / Linux / Windows."""
    # Priority 1: env var override
    if os.environ.get("BILINGUAL_CLAUDE_BIN"):
        return os.environ["BILINGUAL_CLAUDE_BIN"]
    # Priority 2: PATH lookup (Mac/Linux/Windows 通用)
    found = shutil.which("claude")
    if found:
        return found
    # Priority 3: 平台特定 fallback
    candidates = []
    if sys.platform == "darwin":  # Mac
        candidates = [
            "/usr/local/bin/claude",
            "/opt/homebrew/bin/claude",
            str(pathlib.Path.home() / ".claude/local/claude"),
        ]
    elif sys.platform == "linux":
        candidates = [
            "/usr/local/bin/claude",
            str(pathlib.Path.home() / ".claude/local/claude"),
        ]
    elif sys.platform == "win32":
        candidates = [
            str(pathlib.Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd"),
        ]
    for p in candidates:
        if pathlib.Path(p).exists():
            return p
    raise RuntimeError(
        "Cannot find claude CLI binary. Install Claude Code first, "
        "or set BILINGUAL_CLAUDE_BIN env var to claude executable path."
    )

import sys, pathlib
CLAUDE_BIN = _detect_claude_bin()
CLEAN_SETTINGS = str(pathlib.Path(__file__).resolve().parent.parent / "clean_settings.json")

MODEL_MAP = {
    "haiku":  "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus":   "claude-opus-4-7",
}

DEFAULT_QUOTA_SLEEP_S = 5 * 3600 + 300
QUOTA_RESUME_GRACE_S = 5 * 60
RESET_TIME_RE = re.compile(
    r"\b(?:resets|try again at)\s+(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)\s*\(([^)]+)\)",
    re.IGNORECASE,
)

SYSTEM_PROMPT_TEMPLATE = """You are a professional book translator for business/popular-science books.
Translate English to {target_lang} with a NARRATIVE, READABLE tone — NOT academic.

CRITICAL FORMAT RULES:
- The input contains paragraph markers [[PARA_1]] ... [[PARA_N]].
- For each [[PARA_N]] block, output [[PARA_N]] on its own line, then the translated paragraph below.
- Output EXACTLY N blocks for N input blocks. Do NOT merge, split, or skip blocks.
- Preserve markers verbatim. Separate blocks with a blank line.

TRANSLATION STYLE:
- Tone: business / popular-science narrative voice. NOT academic, NOT stiff. Read smoothly aloud.
- First-person ("I asked the AI...") MUST stay first-person ("我問了 AI...") — preserve author's voice.
- Technical acronyms on FIRST appearance in a chunk: include both 英文 / 中文 — e.g., LLM / 大型語言模型, RLHF / 人類回饋強化學習, AGI / 通用人工智慧, GPT / 生成式預訓練轉換器. Subsequent appearances: keep English acronym only (LLM, RLHF).
- Proper nouns (people / places / companies): 中文（English）on first appearance — e.g., 克勞德・夏農（Claude Shannon）, 谷歌（Google）. Subsequent: 中文 only.
- Poetry / verse / limericks / song lyrics: translate keeping the playful tone, rhythm if possible. Don't translate to formal prose.
- Quoted dialogue / AI conversations / example prompts: preserve speaker's voice, humor, casualness.
- Blockquotes, lists, captions: translate them — never skip.
- Numbers / years: Arabic digits (2023, not 二〇二三).

OUTPUT RULES:
- Output ONLY the translation. No explanation, no commentary, no markdown fences.
- Do not introduce yourself as any persona."""

USER_PROMPT_TEMPLATE = """[Context from previous paragraphs - reference only, do NOT translate]
{context}

[Glossary - use these exact translations if encountered]
{glossary}

[Translate to {target_lang}. Preserve every [[PARA_N]] marker. Output N blocks for N input blocks.]
{chunk_text}"""


def _compact_log_message(text: str, limit: int = 600) -> str:
    compact = " ".join(text.strip().split())
    return compact[:limit]


def _format_local_datetime(dt: datetime) -> str:
    tz_label = getattr(dt.tzinfo, "key", None) or dt.tzname() or ""
    return f"{dt:%Y-%m-%d %H:%M:%S} {tz_label}".strip()


def _zoneinfo_from_cli_tz(tz_name: str) -> ZoneInfo | None:
    raw = tz_name.strip().replace(" ", "_")
    candidates = []
    if raw.lower() == "utc":
        candidates.append("UTC")
    if "/" in raw:
        def title_part(part: str) -> str:
            return "_".join(piece[:1].upper() + piece[1:].lower() for piece in part.split("_"))

        candidates.append("/".join(title_part(part) for part in raw.split("/")))
    candidates.append(raw)

    for candidate in dict.fromkeys(candidates):
        try:
            return ZoneInfo(candidate)
        except ZoneInfoNotFoundError:
            continue
    return None


def _parse_quota_reset_at(message: str, now: datetime | None = None) -> datetime | None:
    match = RESET_TIME_RE.search(message)
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    ampm = match.group(3).replace(".", "").lower()
    tz = _zoneinfo_from_cli_tz(match.group(4))
    if tz is None or not (1 <= hour <= 12) or not (0 <= minute <= 59):
        return None

    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0

    now = now or datetime.now().astimezone()
    local_now = now.astimezone(tz)
    reset_at = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if reset_at + timedelta(seconds=QUOTA_RESUME_GRACE_S) <= local_now:
        reset_at += timedelta(days=1)
    return reset_at


@dataclass
class ClaudeTranslator:
    model_alias: str
    target_lang: str = "Traditional Chinese"
    context_paragraphs: int = 5
    log_dir: Path | None = None
    timeout_s: int = 180
    max_retries: int = 3

    _context: ContextWindow = field(init=False)
    _model_id: str = field(init=False)
    last_elapsed_s: float = 0.0
    n_retries: int = 0

    def __post_init__(self) -> None:
        if self.model_alias not in MODEL_MAP:
            raise ValueError(f"unknown model {self.model_alias}; pick from {list(MODEL_MAP)}")
        self._model_id = MODEL_MAP[self.model_alias]
        self._context = ContextWindow(max_pairs=self.context_paragraphs)
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)

    def push_context(self, src: str, dst: str) -> None:
        self._context.push(src, dst)

    def translate(self, chunk: Chunk, glossary_subset: dict[str, str], idx: int) -> str:
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(target_lang=self.target_lang)
        glossary_str = self._format_glossary(glossary_subset)
        user_prompt = USER_PROMPT_TEMPLATE.format(
            context=self._context.render(),
            glossary=glossary_str,
            target_lang=self.target_lang,
            chunk_text=chunk.text,
        )

        backoffs = [5, 15, 45]
        cli_missing_backoffs = [10, 30, 90, 300]
        transient_backoffs = [60, 180, 600, 1800]
        last_err: str | None = None
        attempt = 0
        cli_missing_attempt = 0
        transient_attempt = 0
        while attempt < self.max_retries:
            t0 = time.time()
            try:
                result = self._invoke_claude(system_prompt, user_prompt)
                self.last_elapsed_s = time.time() - t0
                translated = self._postprocess(result)
                self._log(idx, attempt, user_prompt, result, translated, self.last_elapsed_s)
                self._validate_no_persona_leak(translated)
                self._context.push(chunk.text, translated)
                return translated
            except QuotaExceededError as e:
                # 訂閱額度撞限：優先睡到 CLI 回報的 reset 時間 + 5 分鐘；解析失敗才退回固定 5h05m。
                quota_message = str(e)
                now = datetime.now().astimezone()
                reset_at = _parse_quota_reset_at(quota_message, now=now)
                if reset_at:
                    resume_at = reset_at + timedelta(seconds=QUOTA_RESUME_GRACE_S)
                    sleep_s = max(0, int((resume_at - now).total_seconds()))
                    print(
                        f"\n[QUOTA] chunk {idx} 暫停 (script PID alive)"
                        f"\n[QUOTA] reset at {_format_local_datetime(reset_at)}"
                        f"\n[QUOTA] resume at {_format_local_datetime(resume_at)}; self-sleep {sleep_s//60} 分鐘"
                        f"\n[QUOTA-RAW] {_compact_log_message(quota_message)}",
                        flush=True,
                    )
                else:
                    sleep_s = DEFAULT_QUOTA_SLEEP_S
                    resume_at = now + timedelta(seconds=sleep_s)
                    print(
                        f"\n[QUOTA] chunk {idx} 暫停 (script PID alive)"
                        f"\n[QUOTA] reset time parse failed; resume at {_format_local_datetime(resume_at)}; "
                        f"fallback self-sleep {sleep_s//60} 分鐘"
                        f"\n[QUOTA-RAW] {_compact_log_message(quota_message)}",
                        flush=True,
                    )
                time.sleep(sleep_s)
                self.n_retries += 1
                continue  # 不增 attempt，無限 retry 直到額度回來
            except FileNotFoundError as e:
                # claude.cmd 暫時不在（claude CLI 自我更新中）— 短 backoff retry
                wait = cli_missing_backoffs[min(cli_missing_attempt, len(cli_missing_backoffs) - 1)]
                cli_missing_attempt += 1
                print(f"\n[CLI-MISSING] claude.cmd 不在（可能自我更新），等 {wait}s 重試 chunk {idx}", flush=True)
                time.sleep(wait)
                self.n_retries += 1
                if cli_missing_attempt > 5:
                    raise RuntimeError(f"claude.cmd 連 5 次找不到，停跑請檢查 npm/claude 安裝: {e}") from e
                continue  # 不增 attempt，CLI 找不到不算正常 retry
            except TransientAPIError as e:
                # API 5xx server error (500/502/503/504) — 長 backoff retry，不算 max_retries
                wait = transient_backoffs[min(transient_attempt, len(transient_backoffs) - 1)]
                transient_attempt += 1
                print(f"[API-5xx] {type(e).__name__}: chunk {idx} attempt {transient_attempt}/10 wait {wait}s", flush=True)
                time.sleep(wait)
                self.n_retries += 1
                if transient_attempt > 10:
                    raise RuntimeError(f"API 5xx 連 10 次失敗，停跑請檢查 status.claude.com: {e}") from e
                continue  # 不增 attempt，API 抖動不算正常 retry
            except (subprocess.TimeoutExpired, EmptyResponseError, RateLimitError) as e:
                last_err = f"{type(e).__name__}: {e}"
                self.n_retries += 1
                if attempt < self.max_retries - 1:
                    time.sleep(backoffs[attempt])
                attempt += 1
                continue
            except AUPRefuseError:
                raise  # AUP refuse 不能 retry — propagate 到上層 translate.py 處理 skip
            except PersonaLeakError as e:
                raise RuntimeError(f"4 道防線 fail — claude 回 persona，停跑請使用者介入: {e}") from e

        raise RuntimeError(f"chunk {idx} 連 {self.max_retries} 次失敗: {last_err}")

    def _invoke_claude(self, system_prompt: str, user_prompt: str) -> str:
        env = os.environ.copy()
        env["CLAUDE_HOOK_BYPASS"] = "1"

        sandbox = Path(os.environ.get("TEMP", "/tmp")) / "claude_sandbox_bbm"
        sandbox.mkdir(parents=True, exist_ok=True)

        result = subprocess.run(
            [CLAUDE_BIN, "-p",
             "--model", self._model_id,
             "--system-prompt", system_prompt,
             "--settings", CLEAN_SETTINGS,
             "--no-session-persistence",
             "--disable-slash-commands"],
            input=user_prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_s,
            shell=False,
            cwd=str(sandbox),
            env=env,
        )
        out = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        combined_raw = (out + "\n" + stderr).strip()
        combined_lower = combined_raw.lower()
        # 訂閱額度撞限偵測（5h / weekly / monthly / org-level）
        quota_markers = [
            "5-hour limit",
            "5 hour limit",
            "usage limit reached",
            "claude usage limit reached",
            "monthly usage limit",
            "weekly usage limit",
            "you've hit your",
            "you have hit your",
            "try again at",
            "rate_limit_exceeded",
            "quota exceeded",
        ]
        if any(m in combined_lower for m in quota_markers):
            raise QuotaExceededError(combined_raw[:800])
        transient_api_markers = [
            "api error: 500",
            "api error: 502",
            "api error: 503",
            "api error: 504",
            "internal server error",
            "service unavailable",
            "bad gateway",
            "gateway timeout",
        ]
        if any(m in combined_lower for m in transient_api_markers):
            raise TransientAPIError(combined_raw[:400])
        aup_refuse_markers = [
            "violate our usage policy",
            "anthropic.com/legal/aup",
            "claude code is unable to respond",
        ]
        if any(m in combined_lower for m in aup_refuse_markers):
            raise AUPRefuseError(combined_raw[:400])
        if not out:
            raise EmptyResponseError(stderr or "empty stdout")
        if "rate limit" in out.lower() or "rate_limit" in out.lower():
            raise RateLimitError(out[:200])
        # CJK validation：翻譯結果應含中文，**但** endnotes / 書目 / 索引可能 LLM 決定不翻保留原文。
        # 規則：無 CJK + 含 [[PARA marker → 接受（LLM 決定保留原文）；無 CJK + 無 marker → fail。
        if not re.search(r'[一-鿿]', out):
            has_markers = bool(re.search(r'\[\[\s*PARA[\s_]*\d+\s*\]\]', out, re.IGNORECASE))
            if not has_markers:
                raise EmptyResponseError(f"no CJK + no markers (likely error/disclaimer): {out[:200]}")
            # 含 markers 但無 CJK：可能是書目 / endnotes，LLM 保留原文是合理的
        return out

    @staticmethod
    def _postprocess(text: str) -> str:
        t = text.strip()
        if t.startswith("```") and t.endswith("```"):
            lines = t.splitlines()
            if len(lines) >= 2:
                t = "\n".join(lines[1:-1]).strip()
        return t

    @staticmethod
    def _validate_no_persona_leak(text: str) -> None:
        leak_markers = ["我是 Iris", "我是Iris", "I'll wait for your", "Session handoff"]
        if any(m in text for m in leak_markers):
            raise PersonaLeakError(text[:200])

    @staticmethod
    def _format_glossary(g: dict[str, str]) -> str:
        if not g:
            return "(no glossary terms in this chunk)"
        return "\n".join(f"{k} = {v}" for k, v in g.items())

    def _log(self, idx, attempt, user_prompt, raw, translated, elapsed) -> None:
        if not self.log_dir:
            return
        path = self.log_dir / f"chunk_{idx:04d}_attempt_{attempt}.json"
        path.write_text(json.dumps({
            "idx": idx,
            "attempt": attempt,
            "model": self._model_id,
            "elapsed_s": round(elapsed, 2),
            "user_prompt": user_prompt,
            "raw_response": raw,
            "translated": translated,
        }, ensure_ascii=False, indent=2), encoding="utf-8")


class EmptyResponseError(Exception): ...
class RateLimitError(Exception): ...
class QuotaExceededError(Exception): ...
class TransientAPIError(Exception): ...
class AUPRefuseError(Exception): ...
class PersonaLeakError(Exception): ...
