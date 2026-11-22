"""Summarize Codex CLI events without recording prompts or generated text."""

import json
import re
from collections.abc import Callable


_ERROR_TYPES = (
    ("인증 오류", r"\b(?:401|403) (?:unauthorized|forbidden)\b|authentication (?:failed|required)|token refresh failed"),
    ("사용량 제한", r"\b429 too many requests\b|rate limit|usage limit|quota exceeded"),
    ("연결 오류", r"connection (?:refused|reset|closed|failed)|network (?:error|unreachable)|"
                  r"failed to (?:connect|lookup address)|dns (?:error|lookup failed)|"
                  r"stream disconnected|error sending request|\b(?:econnrefused|econnreset|etimedout|enotfound)\b"),
    ("시간 초과", r"timed out|timeout"),
    ("권한 오류", r"permission denied|access is denied|sandbox (?:denied|violation)"),
    ("서비스 오류", r"\b(?:500 internal server error|502 bad gateway|503 service unavailable|504 gateway timeout)\b"),
)
_TOOL_LABELS = {
    "command_execution": "명령 실행",
    "mcp_tool_call": "MCP 도구",
    "file_change": "파일 변경",
    "web_search": "웹 검색",
}


class CodexDiagnostics:
    def __init__(self, report: Callable[[str], None]):
        self.report = report
        self.error_type = None
        self.turn_failed = False
        self.turn_completed = False

    def _error(self, message, fallback):
        category = fallback
        if isinstance(message, str):
            category = next(
                (label for label, pattern in _ERROR_TYPES if re.search(pattern, message, re.I)),
                fallback,
            )
        if category != fallback:
            self.error_type = category
        self.report(f"Codex {category}")

    def capture(self, line: str, is_stderr: bool):
        if is_stderr:
            retry = re.search(r"\breconnecting(?:\.\.\.)?\s+(\d{1,3})/(\d{1,3})\b", line, re.I)
            if retry:
                self.report(f"Codex 재연결 시도 {retry[1]}/{retry[2]}")
            elif re.search(r"\b(?:error|warn(?:ing)?)\b|^\s*failed to", line, re.I):
                self._error(line, "표준오류 메시지 감지")
            return

        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
            return
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        if kind == "thread.started":
            self.report("Codex 세션 시작")
        elif kind == "turn.started":
            self.report("Codex 처리 시작")
        elif kind == "turn.completed":
            self.turn_completed = True
            self.report("Codex 처리 완료")
        elif kind == "turn.failed":
            self.turn_failed = True
            error = event.get("error")
            self._error(error.get("message") if isinstance(error, dict) else error, "처리 실패")
        elif kind == "error":
            message = event.get("message")
            retry = re.search(r"\breconnecting(?:\.\.\.)?\s+(\d{1,3})/(\d{1,3})\b", message, re.I) if isinstance(message, str) else None
            if retry:
                self.report(f"Codex 재연결 시도 {retry[1]}/{retry[2]}")
            else:
                self._error(message, "오류 이벤트 감지")
        elif kind in ("item.started", "item.completed"):
            item = event.get("item")
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                return
            label = _TOOL_LABELS.get(item["type"])
            if label is None:
                return
            rc = item.get("exit_code")
            if kind == "item.started":
                self.report(f"Codex {label} 시작")
            elif type(rc) is int:
                self.report(f"Codex {label} 종료 코드 {rc}")
            else:
                status = "실패" if item.get("status") == "failed" or item.get("success") is False else "종료"
                self.report(f"Codex {label} {status}")
