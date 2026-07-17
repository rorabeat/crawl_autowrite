#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
크롤링된 UTF-8 txt(상단 메타 + 제목·본문)를 읽어 AI로 블로그 완성 글을 마크다운(.md)과 HTML(.html)로 함께 저장한다.

환경변수: 프로젝트(스크립트·exe와 같은 폴더)의 .env 에 두면 load_project_dotenv()로 자동 로드된다.
- BLOG_AI_PROVIDER: "openai" | "gemini" | "auto" (기본 auto — OPENAI_API_KEY가 있으면 OpenAI, 없으면 GEMINI_API_KEY)
- OPENAI_API_KEY, GEMINI_API_KEY: API 키(하드코딩 금지)
- OPENAI_MODEL: GUI 미선택 시 기본 gpt-5.4 (GUI 모델 콤보가 우선; OpenAI는 gpt-5.4, gpt-5.4-mini 지원)
- GEMINI_MODEL: GUI 미선택 시 기본 gemini-2.0-flash
- BLOG_AI_TEMPERATURE: GUI 미지정 시 보조(소수). 0.75~1.0 범위에서 0.05 간격 허용 값만 적용
- 글작성 API 성공 시 `api_usage_stats.json`에 호출 횟수 누적(오늘/전체)

마크다운 제목 규칙(모델 출력 강제):
- 문서 맨 앞 최상위 제목은 정확히 한 줄의 `# 제목`만 사용한다.
- 소제목은 `##`, 필요 시 `###`까지 계층을 명확히 한다.
- `#`/`##` 앞뒤로 빈 줄을 두어 렌더러에서 제목이 눈에 띄게 구분되게 한다.

GUI 연동(참고):
- 「크롤링 시작」: 크롤만 수행.
- 「글작성하기」: (1) 폴더 내 .txt가 있으면 원문 통합 1편 생성.
  (2) 키워드 폴더가 없거나 .txt가 없으면 참고 원문 없이 주제·지침만으로 AI가 직접 1편 작성.
  완성본은 동일 타임스탬프의 `.md`와 `.html`로 저장한 뒤, 같은 폴더의 `*_completed_*.md` 전부를 HTML로 맞춤(md→html).
- 「자동화」(GUI): 키워드마다 크롤 → (txt 있으면 통합 글 / 없으면 무원문 생성) → md→html 정리 → 브라우저 순으로 처리 후 다음 키워드.

지침(사용자 프롬프트)은 GUI의 `ai_guidelines.txt`(스크립트 폴더)에 「지침 저장하기」로 기록한다.
시스템 프롬프트는 `ai_system_prompt.txt`(같은 폴더)에서 읽으며, 없거나 비어 있으면 내장 기본 문구를 쓴다.
"""

from __future__ import annotations

import html
import json
import os
import re
import threading
from collections import OrderedDict
from datetime import date, datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from blogcontentsClawring import get_base_dir

AI_SYSTEM_PROMPT_FILE_NAME = "ai_system_prompt.txt"


def default_system_prompt_path() -> Path:
    """프로젝트(스크립트·exe) 폴더의 기본 시스템 프롬프트 txt 경로."""
    return get_base_dir() / AI_SYSTEM_PROMPT_FILE_NAME


def _decode_ai_text_file_bytes(raw: bytes) -> Optional[str]:
    """메모장(UTF-8 BOM)·UTF-8·CP949 순으로 디코딩. 모두 실패하면 None."""
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return raw.decode(enc).replace("\r\n", "\n")
        except UnicodeDecodeError:
            continue
    return None


def embedded_system_prompt_default() -> str:
    """`ai_system_prompt.txt`가 없거나 비어 있을 때 사용하는 내장 기본 시스템 프롬프트."""
    return (
        "당신은 한국어 블로그 글 편집자입니다. 사용자가 제공한 크롤링 원문(메타·제목·본문)을 바탕으로 "
        "새로운 완성 블로그 글을 마크다운으로만 출력합니다.\n\n"
        "마크다운 구조(반드시 준수):\n"
        "- 문서 맨 앞에 최상위 제목(H1)은 `# 제목` 형식의 줄을 딱 한 번만 씁니다(단일 `#` + 공백으로 시작하는 줄은 이 한 줄뿐).\n"
        "- 그 아래 소제목은 반드시 `##`, 필요하면 `###`만 사용합니다(`##`는 H1이 아닙니다).\n"
        "- 각 `#`/`##`/`###` 줄 앞에는 빈 줄을 두고, 단락 사이에도 빈 줄로 가독성을 확보합니다.\n"
        "- HTML 태그, 코드펜스(```)로 전체를 감싸지 마세요. 순수 마크다운 텍스트만 반환합니다.\n"
        "- 원문을 표절하지 말고 재구성·요약·보완하여 자연스러운 글을 씁니다."
    )


def read_system_prompt_for_api(
    explicit: Optional[str] = None,
    *,
    file_path: Optional[Path] = None,
) -> str:
    """
    API system 역할에 넣을 문자열.
    explicit가 비어 있지 않으면 그대로 사용. 그렇지 않으면 file_path(기본: ai_system_prompt.txt)를 읽고,
    실패·빈 내용이면 embedded_system_prompt_default().
    """
    if explicit is not None and explicit.strip():
        return explicit.strip()
    p = file_path if file_path is not None else default_system_prompt_path()
    if p.is_file():
        try:
            raw = p.read_bytes()
            dec = _decode_ai_text_file_bytes(raw)
            if dec is not None:
                s = dec.strip()
                if s:
                    return s
        except OSError:
            pass
    return embedded_system_prompt_default()


# OpenAI 글작성에 허용하는 모델만(콤보·검증 공통; API 키·플랜에 따라 실제 호출은 제한될 수 있음)
OPENAI_MODEL_CHOICES: Tuple[str, ...] = ("gpt-5.4", "gpt-5.4-mini")
# Responses API 내장 웹 검색(https://platform.openai.com/docs/guides/tools-web-search)
OPENAI_RESPONSES_TOOLS_WEB_SEARCH: List[dict] = [{"type": "web_search"}]
GEMINI_MODEL_CHOICES: Tuple[str, ...] = (
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
    "gemini-1.5-pro",
)

# 글작성 temperature 후보: 0.75 ~ 1.0, 0.05 간격 (GUI 콤보·검증 공통)
AI_TEMPERATURE_CHOICES_FLOAT: Tuple[float, ...] = tuple(
    round(0.75 + i * 0.05, 2) for i in range(6)
)


def _format_temperature_ui(x: float) -> str:
    x = round(float(x), 2)
    return "1.0" if abs(x - 1.0) < 1e-9 else f"{x:.2f}"


AI_TEMPERATURE_CHOICES_UI: Tuple[str, ...] = tuple(
    _format_temperature_ui(t) for t in AI_TEMPERATURE_CHOICES_FLOAT
)


def coerce_ai_temperature(value: Optional[object]) -> float:
    """허용 목록에 맞는 float 반환(미지정·오류 시 첫 번째 후보)."""
    choices = AI_TEMPERATURE_CHOICES_FLOAT
    default = choices[0]
    if value is None or value == "":
        return default
    try:
        if isinstance(value, (int, float)):
            v = float(value)
        else:
            v = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return default
    for c in choices:
        if abs(v - c) < 1e-6:
            return c
    if 0.75 <= v <= 1.0:
        step = 0.05
        k = int(round((v - 0.75) / step))
        snapped = round(0.75 + k * step, 2)
        snapped = max(0.75, min(1.0, snapped))
        for c in choices:
            if abs(snapped - c) < 1e-6:
                return c
    return default


def format_ai_temperature_ui(t: float) -> str:
    return _format_temperature_ui(coerce_ai_temperature(t))


API_USAGE_STATS_PATH = Path(get_base_dir()) / "api_usage_stats.json"
_usage_stats_lock = threading.Lock()

# 상대 import 없이 blogcontentsClawring의 결과 경로 규칙과 맞춘다
def _result_base_path() -> Path:
    from blogcontentsClawring import get_result_dir

    return Path(get_result_dir(date_included=False))


def default_crawl_txt_folder() -> Optional[Path]:
    """
    result/YYYYMMDD/<YYYYMMDD_키워드> 중, 그 안의 .txt 중 가장 최근 수정 시각이 가장 늦은 폴더를 반환.
    없으면 None.
    """
    result_base = _result_base_path()
    if not result_base.is_dir():
        return None

    date_dirs = sorted(
        [p for p in result_base.iterdir() if p.is_dir() and re.fullmatch(r"\d{8}", p.name)],
        key=lambda p: p.name,
        reverse=True,
    )
    best_folder: Optional[Path] = None
    best_mtime = 0.0
    for d in date_dirs:
        for sub in d.iterdir():
            if not sub.is_dir():
                continue
            for f in sub.glob("*.txt"):
                try:
                    m = f.stat().st_mtime
                except OSError:
                    continue
                if m >= best_mtime:
                    best_mtime = m
                    best_folder = sub
    return best_folder


def list_txt_files_in_folder(folder: Path) -> List[Path]:
    """폴더 바로 아래의 .txt만(하위 폴더 제외), 이름순."""
    if not folder.is_dir():
        return []
    return sorted(folder.glob("*.txt"), key=lambda p: p.name.lower())


def resolve_source_folder(custom_folder: Optional[str]) -> Optional[Path]:
    """custom_folder가 유효하면 그 경로, 아니면 default_crawl_txt_folder()."""
    if custom_folder:
        p = Path(custom_folder)
        if p.is_dir():
            return p.resolve()
    return default_crawl_txt_folder()


def _strip_markdown_fences(text: str) -> str:
    t = text.strip()
    if not t.startswith("```"):
        return t
    lines = t.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_markdown_title(md: str) -> str:
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("# ") and not s.startswith("## "):
            return s[2:].strip() or "블로그 완성 글"
    return "블로그 완성 글"


def _markdown_to_html_fragment(md: str) -> str:
    import markdown

    return markdown.markdown(
        md.strip(),
        extensions=["extra"],
        output_format="html5",
    )


def wrap_blog_markdown_as_html(markdown_body: str) -> str:
    """마크다운 본문을 나눔고딕·본문 15px·제목 강조 스타일의 완전한 HTML 문서로 감싼다."""
    fragment = _markdown_to_html_fragment(markdown_body)
    title = _extract_markdown_title(markdown_body)
    esc_title = html.escape(title, quote=True)
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc_title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Nanum+Gothic:wght@400;700;800&display=swap" rel="stylesheet">
<style>
  :root {{
    --blog-font-family: "Nanum Gothic", "Malgun Gothic", "Apple SD Gothic Neo", "맑은 고딕", sans-serif;
  }}
  html {{
    font-size: 15px;
    font-family: var(--blog-font-family);
  }}
  body {{
    font-family: inherit;
    font-size: 15px;
    line-height: 1.75;
    color: #1a1a1a;
    max-width: 48rem;
    margin: 0 auto;
    padding: 1.5rem 1.25rem 3rem;
  }}
  article :where(
    p, ul, ol, li, blockquote, table, thead, tbody, tr, th, td,
    h1, h2, h3, h4, h5, h6, strong, em, a, span
  ) {{
    font-family: inherit;
  }}
  article h1 {{
    font-size: 2.15rem;
    font-weight: 800;
    margin: 0 0 1.25rem;
    line-height: 1.35;
  }}
  article h2 {{
    font-size: 1.65rem;
    font-weight: 700;
    margin: 2rem 0 0.85rem;
    line-height: 1.4;
  }}
  article h3 {{
    font-size: 1.35rem;
    font-weight: 700;
    margin: 1.5rem 0 0.65rem;
    line-height: 1.45;
  }}
  article p {{ margin: 0 0 1rem; }}
  article ul, article ol {{ margin: 0 0 1rem; padding-left: 1.5rem; }}
  article li {{ margin: 0.35rem 0; }}
  article blockquote {{
    margin: 1rem 0;
    padding-left: 1rem;
    border-left: 3px solid #ccc;
  }}
  article code,
  article pre,
  article pre * {{
    font-family: ui-monospace, Consolas, monospace;
  }}
  article code {{ font-size: 0.92em; }}
  article pre {{
    overflow-x: auto;
    padding: 0.75rem 1rem;
    background: #f5f5f5;
    border-radius: 4px;
  }}
</style>
</head>
<body>
<article>
{fragment}
</article>
</body>
</html>
"""


def _user_prompt(raw_txt: str, guidelines: str, keyword_extra: str = "") -> str:
    parts = [
        "아래는 크롤링된 원문입니다. 이를 참고해 완성 블로그 글을 마크다운으로 작성하세요.\n\n",
        "--- 원문 시작 ---\n",
        raw_txt.strip(),
        "\n--- 원문 끝 ---\n",
    ]
    if guidelines.strip():
        parts.append("\n추가 지침(사용자):\n")
        parts.append(guidelines.strip())
        parts.append("\n")
    if (keyword_extra or "").strip():
        parts.append("\n키워드별 추가 요청:\n")
        parts.append(keyword_extra.strip())
        parts.append("\n")
    return "".join(parts)


def _user_prompt_no_crawl_source(
    guidelines: str, topic_hint: str, keyword_extra: str = ""
) -> str:
    """크롤 원문 없이 주제·지침만으로 글을 쓰도록 하는 사용자 메시지."""
    parts = [
        "아래 요청에는 크롤링된 참고 원문(txt)이 없습니다. "
        "주제 힌트와 사용자 지침만을 바탕으로 완성 블로그 글을 마크다운으로 직접 작성하세요.\n\n",
    ]
    th = (topic_hint or "").strip()
    if th:
        parts.append(f"[주제·키워드]\n{th}\n\n")
    if guidelines.strip():
        parts.append("[작성 지침]\n")
        parts.append(guidelines.strip())
        parts.append("\n")
    else:
        parts.append(
            "[작성 지침]\n"
            "별도 지침이 없으면 위 주제에 맞는 유용한 정보성 한국어 블로그 글을 한 편 완성하세요.\n"
        )
    if (keyword_extra or "").strip():
        parts.append("\n[키워드별 추가 요청]\n")
        parts.append(keyword_extra.strip())
        parts.append("\n")
    return "".join(parts)


def _today_usage_key() -> str:
    return date.today().strftime("%Y%m%d")


def get_blog_api_usage_stats() -> dict:
    """딕셔너리: total_calls, today_calls, day_key(YYYYMMDD)."""
    day_key = _today_usage_key()
    with _usage_stats_lock:
        if not API_USAGE_STATS_PATH.is_file():
            return {"total_calls": 0, "today_calls": 0, "day_key": day_key}
        try:
            data = json.loads(API_USAGE_STATS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {"total_calls": 0, "today_calls": 0, "day_key": day_key}
        total = int(data.get("total_calls", 0))
        by_day = data.get("by_day") or {}
        if not isinstance(by_day, dict):
            by_day = {}
        today_calls = int(by_day.get(day_key, 0))
        return {"total_calls": total, "today_calls": today_calls, "day_key": day_key}


def _record_blog_api_completion_unlocked() -> None:
    day_key = _today_usage_key()
    total = 0
    by_day: dict = {}
    if API_USAGE_STATS_PATH.is_file():
        try:
            data = json.loads(API_USAGE_STATS_PATH.read_text(encoding="utf-8"))
            total = int(data.get("total_calls", 0))
            raw_bd = data.get("by_day") or {}
            if isinstance(raw_bd, dict):
                by_day = {str(k): int(v) for k, v in raw_bd.items()}
        except Exception:
            pass
    total += 1
    by_day[day_key] = int(by_day.get(day_key, 0)) + 1
    API_USAGE_STATS_PATH.write_text(
        json.dumps({"total_calls": total, "by_day": by_day}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def record_blog_api_completion() -> None:
    """블로그 글 AI 호출이 정상 완료된 뒤 1회 호출."""
    with _usage_stats_lock:
        _record_blog_api_completion_unlocked()


def _resolve_provider(explicit: Optional[str]) -> str:
    raw = (explicit or os.getenv("BLOG_AI_PROVIDER", "auto") or "auto").strip().lower()
    if raw not in ("auto", "openai", "gemini"):
        raw = "auto"
    if raw == "auto":
        if os.getenv("OPENAI_API_KEY", "").strip():
            return "openai"
        if os.getenv("GEMINI_API_KEY", "").strip():
            return "gemini"
        raise RuntimeError(
            "BLOG_AI_PROVIDER=auto인데 OPENAI_API_KEY와 GEMINI_API_KEY가 모두 비어 있습니다."
        )
    if raw == "openai" and not os.getenv("OPENAI_API_KEY", "").strip():
        raise RuntimeError("OPENAI_API_KEY가 설정되어 있지 않습니다.")
    if raw == "gemini" and not os.getenv("GEMINI_API_KEY", "").strip():
        raise RuntimeError("GEMINI_API_KEY가 설정되어 있지 않습니다.")
    return raw


def _emit_detail_log(
    detail_log: Optional[Callable[[str], None]],
    *blocks: str,
) -> None:
    if not detail_log:
        return
    for b in blocks:
        detail_log(b)


def generate_blog_markdown(
    raw_txt: str,
    user_guidelines: str = "",
    provider: Optional[str] = None,
    *,
    system_prompt: Optional[str] = None,
    system_prompt_file: Optional[Path] = None,
    openai_model: Optional[str] = None,
    gemini_model: Optional[str] = None,
    temperature: Optional[float] = None,
    openai_web_search: bool = True,
    detail_log: Optional[Callable[[str], None]] = None,
    user_message_override: Optional[str] = None,
    keyword_extra: str = "",
) -> str:
    """원문 txt 전체 문자열을 받아 완성 마크다운 문자열을 반환."""
    from blogcontentsClawring import load_project_dotenv

    load_project_dotenv()
    prov = _resolve_provider(provider)
    system = read_system_prompt_for_api(system_prompt, file_path=system_prompt_file)
    ke = (keyword_extra or "").strip()
    if user_message_override is not None:
        user = user_message_override.strip()
        if ke:
            user = user.rstrip() + "\n\n[키워드별 추가 요청]\n" + ke + "\n"
    else:
        user = _user_prompt(raw_txt, user_guidelines, keyword_extra)

    temp_raw = temperature
    if temp_raw is None:
        env_t = os.getenv("BLOG_AI_TEMPERATURE", "").strip()
        temp_raw = env_t if env_t else None
    temp_f = coerce_ai_temperature(temp_raw)

    if prov == "openai":
        from openai import OpenAI

        model = (
            (openai_model or "").strip()
            or os.getenv("OPENAI_MODEL", "").strip()
            or "gpt-5.4"
        )
        if model not in OPENAI_MODEL_CHOICES:
            prev = model
            model = "gpt-5.4"
            _emit_detail_log(
                detail_log,
                f"[안내] OpenAI는 {', '.join(OPENAI_MODEL_CHOICES)} 만 지원합니다. "
                f"'{prev}' → '{model}' 로 바꿉니다.",
            )
        _emit_detail_log(
            detail_log,
            "========== [API 글쓰기 요청] OpenAI ==========",
            f"모델: {model}",
            "엔드포인트: responses"
            + (" (tools=web_search)" if openai_web_search else " (tools: none)"),
            f"temperature: {temp_f}",
            f"--- instructions(시스템) ({len(system)}자) ---",
            system,
            f"--- input(user) ({len(user)}자) ---",
            user,
            "========== [요청 본문 끝] ==========",
        )
        client = OpenAI()
        req = {
            "model": model,
            "instructions": system,
            "input": user,
            "temperature": temp_f,
        }
        if openai_web_search:
            req["tools"] = OPENAI_RESPONSES_TOOLS_WEB_SEARCH
        resp = client.responses.create(**req)
        text = (resp.output_text or "").strip()
        st = getattr(resp, "status", None)
        inc = getattr(resp, "incomplete_details", None)
        _emit_detail_log(
            detail_log,
            "========== [API 응답] OpenAI ==========",
            f"status: {st}",
            f"incomplete_details: {inc}",
            f"--- assistant 본문 ({len(text)}자) ---",
            text if text else "(빈 문자열)",
            "========== [응답 끝] ==========",
        )
    else:
        import google.generativeai as genai

        model_name = (
            (gemini_model or "").strip()
            or os.getenv("GEMINI_MODEL", "").strip()
            or "gemini-2.0-flash"
        )
        _emit_detail_log(
            detail_log,
            "========== [API 글쓰기 요청] Google Gemini ==========",
            f"모델: {model_name}",
            "방식: generate_content (system_instruction + user 텍스트)",
            f"temperature: {temp_f}",
            f"--- system_instruction ({len(system)}자) ---",
            system,
            f"--- user 입력 ({len(user)}자) ---",
            user,
            "========== [요청 본문 끝] ==========",
        )
        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        mdl = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=system,
        )
        resp = mdl.generate_content(
            user,
            generation_config={"temperature": temp_f},
        )
        try:
            text = (resp.text or "").strip()
        except Exception as ex:
            raise RuntimeError(f"Gemini 응답을 텍스트로 읽을 수 없습니다: {ex}") from ex
        _emit_detail_log(
            detail_log,
            "========== [API 응답] Gemini ==========",
            f"--- 모델 출력 텍스트 ({len(text)}자) ---",
            text if text else "(빈 문자열)",
            "========== [응답 끝] ==========",
        )

    record_blog_api_completion()
    return _strip_markdown_fences(text)


def save_completed_blog_outputs(
    source_txt_path: str,
    markdown_body: str,
) -> Tuple[str, str]:
    """
    원본 txt와 같은 부모 폴더에 동일 타임스탬프의 `<stem>_completed_TS.md` / `.html` 저장.
    반환: (md_path, html_path)
    """
    src = Path(source_txt_path)
    out_dir = src.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = src.stem
    md_path = out_dir / f"{stem}_completed_{ts}.md"
    html_path = out_dir / f"{stem}_completed_{ts}.html"
    md_path.write_text(markdown_body, encoding="utf-8")
    html_path.write_text(wrap_blog_markdown_as_html(markdown_body), encoding="utf-8")
    return str(md_path.resolve()), str(html_path.resolve())


def _sanitize_folder_filename(name: str, max_len: int = 100) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if len(s) > max_len:
        s = s[:max_len].rstrip(" .")
    return s or "blog"


def save_combined_folder_blog_outputs(
    folder: Path,
    markdown_body: str,
) -> Tuple[str, str]:
    """통합 완성 글을 동일 타임스탬프의 `.md` / `.html`로 한 쌍 저장. 반환: (md_path, html_path)."""
    out_dir = folder.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _sanitize_folder_filename(folder.name)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = out_dir / f"{stem}_completed_{ts}.md"
    html_path = out_dir / f"{stem}_completed_{ts}.html"
    md_path.write_text(markdown_body, encoding="utf-8")
    html_path.write_text(wrap_blog_markdown_as_html(markdown_body), encoding="utf-8")
    return str(md_path.resolve()), str(html_path.resolve())


def count_distinct_txt_parent_folders(txt_paths: List[str]) -> int:
    """세션 txt 경로들이 속한 서로 다른 부모(키워드) 폴더 개수."""
    if not txt_paths:
        return 0
    return len({str(Path(p).resolve().parent) for p in txt_paths})


def _combined_raw_from_txts(txt_paths: List[Path]) -> str:
    blocks: List[str] = []
    n = len(txt_paths)
    for i, path in enumerate(txt_paths, 1):
        raw = path.read_text(encoding="utf-8")
        blocks.append(f"=== 원문 {i}/{n} (파일: {path.name}) ===\n{raw.strip()}")
    header = (
        "[안내] 아래는 동일 주제 폴더에서 크롤링한 여러 게시물의 원문입니다. "
        "이들을 종합·재구성하여 하나의 자연스러운 완성 블로그 글(단일 흐름, 불필요한 반복 제거)로 작성하세요.\n\n"
    )
    return header + "\n\n".join(blocks)


def process_folder_combined(
    folder: Path,
    txt_paths: List[str],
    user_guidelines: str,
    log: Optional[Callable[[str], None]] = None,
    provider: Optional[str] = None,
    *,
    system_prompt: Optional[str] = None,
    system_prompt_file: Optional[Path] = None,
    openai_model: Optional[str] = None,
    gemini_model: Optional[str] = None,
    temperature: Optional[float] = None,
    openai_web_search: bool = True,
    detail_log: Optional[Callable[[str], None]] = None,
    progress: Optional[Callable[[float, str], None]] = None,
    keyword_extra: str = "",
) -> Optional[Tuple[str, str]]:
    """
    폴더 내 txt 전체를 한 번에 API에 넘겨 완성 md·html 1쌍 저장. 성공 시 (md_path, html_path), 실패 시 None.
    """
    paths = [Path(p) for p in txt_paths]
    paths = [p for p in paths if p.is_file()]
    if not paths:
        if log:
            log("[AI 글작성] 처리할 txt가 없습니다.")
        return None
    paths.sort(key=lambda p: p.name.lower())

    def _p(pct: float, msg: str) -> None:
        if progress:
            progress(pct, msg)

    try:
        _p(5.0, "원문 txt 준비")
        if log:
            log(
                f"[AI 글작성] 소스 {len(paths)}개 txt를 통합하여 1편 생성 중… ({folder.name})"
            )
        combined = _combined_raw_from_txts(paths)
        _p(22.0, f"{len(paths)}개 원문 병합 완료")
        _p(35.0, "API로 블로그 글 생성 요청 중…")
        md = generate_blog_markdown(
            combined,
            user_guidelines,
            provider=provider,
            system_prompt=system_prompt,
            system_prompt_file=system_prompt_file,
            openai_model=openai_model,
            gemini_model=gemini_model,
            temperature=temperature,
            openai_web_search=openai_web_search,
            detail_log=detail_log,
            keyword_extra=keyword_extra,
        )
        _p(88.0, "MD·HTML 저장 중…")
        md_path, html_path = save_combined_folder_blog_outputs(folder, md)
        _p(100.0, "저장 완료")
        if log:
            log(f"[AI 글작성] MD 저장: {md_path}")
            log(f"[AI 글작성] HTML 저장: {html_path}")
        return (md_path, html_path)
    except Exception as e:
        if log:
            log(f"[AI 글작성] 실패: {e}")
        return None


def process_folder_combined_direct(
    folder: Path,
    user_guidelines: str,
    topic_hint: str,
    log: Optional[Callable[[str], None]] = None,
    provider: Optional[str] = None,
    *,
    system_prompt: Optional[str] = None,
    system_prompt_file: Optional[Path] = None,
    openai_model: Optional[str] = None,
    gemini_model: Optional[str] = None,
    temperature: Optional[float] = None,
    openai_web_search: bool = True,
    detail_log: Optional[Callable[[str], None]] = None,
    progress: Optional[Callable[[float, str], None]] = None,
    keyword_extra: str = "",
) -> Optional[Tuple[str, str]]:
    """
    참고 txt 없이 주제·지침만으로 완성 md·html 1쌍 저장. 성공 시 (md_path, html_path), 실패 시 None.
    """
    out_dir = folder.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    def _p(pct: float, msg: str) -> None:
        if progress:
            progress(pct, msg)

    try:
        _p(5.0, "무원문 모드 준비")
        if log:
            log(
                f"[AI 글작성] 참고 txt 없이 주제만으로 1편 생성 중… ({folder.name})"
            )
        base_system = read_system_prompt_for_api(
            system_prompt, file_path=system_prompt_file
        )
        enhanced_system = (
            "이번 요청에는 크롤링·참고용 원문(txt)이 제공되지 않습니다. "
            "사용자 메시지의 주제·키워드와 지침만을 바탕으로 완성 블로그 글을 마크다운으로 직접 작성하세요.\n\n"
        ) + base_system
        user_msg = _user_prompt_no_crawl_source(
            user_guidelines, topic_hint, keyword_extra
        )
        _p(35.0, "API로 블로그 글 생성 요청 중…")
        md = generate_blog_markdown(
            "",
            "",
            provider=provider,
            system_prompt=enhanced_system,
            system_prompt_file=None,
            openai_model=openai_model,
            gemini_model=gemini_model,
            temperature=temperature,
            openai_web_search=openai_web_search,
            detail_log=detail_log,
            user_message_override=user_msg,
            keyword_extra="",
        )
        _p(88.0, "MD·HTML 저장 중…")
        md_path, html_path = save_combined_folder_blog_outputs(out_dir, md)
        _p(100.0, "저장 완료")
        if log:
            log(f"[AI 글작성] MD 저장: {md_path}")
            log(f"[AI 글작성] HTML 저장: {html_path}")
        return (md_path, html_path)
    except Exception as e:
        if log:
            log(f"[AI 글작성] 실패: {e}")
        return None


def process_txt_paths(
    txt_paths: List[str],
    user_guidelines: str,
    log: Optional[Callable[[str], None]] = None,
    provider: Optional[str] = None,
    *,
    system_prompt: Optional[str] = None,
    system_prompt_file: Optional[Path] = None,
    openai_model: Optional[str] = None,
    gemini_model: Optional[str] = None,
    temperature: Optional[float] = None,
    openai_web_search: bool = True,
    detail_log: Optional[Callable[[str], None]] = None,
) -> List[Tuple[str, str]]:
    """
    각 txt를 읽어 AI 완성 후 md·html 저장. 성공한 (md_path, html_path) 목록 반환.
    log가 있으면 한 줄씩 호출.
    (CLI·구버전 호환용 — GUI는 process_folder_combined 사용)
    """
    out: List[Tuple[str, str]] = []
    for i, p in enumerate(txt_paths, 1):
        path = Path(p)
        try:
            raw = path.read_text(encoding="utf-8")
        except Exception as e:
            if log:
                log(f"[AI 글작성] ({i}/{len(txt_paths)}) 읽기 실패 {path}: {e}")
            continue
        try:
            if log:
                log(f"[AI 글작성] ({i}/{len(txt_paths)}) 생성 중: {path.name}")
            md = generate_blog_markdown(
                raw,
                user_guidelines,
                provider=provider,
                system_prompt=system_prompt,
                system_prompt_file=system_prompt_file,
                openai_model=openai_model,
                gemini_model=gemini_model,
                temperature=temperature,
                openai_web_search=openai_web_search,
                detail_log=detail_log,
            )
            md_p, html_p = save_completed_blog_outputs(str(path), md)
            out.append((md_p, html_p))
            if log:
                log(f"[AI 글작성] ({i}/{len(txt_paths)}) MD: {md_p}")
                log(f"[AI 글작성] ({i}/{len(txt_paths)}) HTML: {html_p}")
        except Exception as e:
            if log:
                log(f"[AI 글작성] ({i}/{len(txt_paths)}) 실패 {path.name}: {e}")
    return out


def process_session_txt_paths_combined(
    session_txt_paths: List[str],
    user_guidelines: str,
    log: Optional[Callable[[str], None]] = None,
    provider: Optional[str] = None,
    *,
    system_prompt: Optional[str] = None,
    system_prompt_file: Optional[Path] = None,
    openai_model: Optional[str] = None,
    gemini_model: Optional[str] = None,
    temperature: Optional[float] = None,
    openai_web_search: bool = True,
    detail_log: Optional[Callable[[str], None]] = None,
    folder_progress: Optional[Callable[[int, int, str], None]] = None,
    progress_factory: Optional[
        Callable[[int, int], Optional[Callable[[float, str], None]]]
    ] = None,
    on_each_saved: Optional[Callable[[str, str], None]] = None,
) -> List[Tuple[str, str]]:
    """
    크롤 세션에서 모은 txt 경로를 부모 폴더별로 묶어, 폴더당 통합 글 1개씩 생성.
    반환: 저장된 (md_path, html_path) 목록.

    folder_progress(done_idx, total_folders, folder_name): 각 폴더 처리 직후(done_idx 1..total).
    progress_factory(folder_index_1based, total_folders) -> process_folder_combined용 progress 콜백.
    on_each_saved(md_path, html_path): 한 편(md+html) 저장 직후 호출(스레드 안전은 호출 측에서 처리).
    """
    by_parent: OrderedDict[str, List[str]] = OrderedDict()
    for p in session_txt_paths:
        parent = str(Path(p).resolve().parent)
        by_parent.setdefault(parent, []).append(p)

    items = list(by_parent.items())
    total = len(items)
    saved: List[Tuple[str, str]] = []

    for folder_i, (parent, paths) in enumerate(items, start=1):
        folder = Path(parent)
        prog_cb = (
            progress_factory(folder_i, total) if progress_factory else None
        )
        out = process_folder_combined(
            folder,
            paths,
            user_guidelines,
            log=log,
            provider=provider,
            system_prompt=system_prompt,
            system_prompt_file=system_prompt_file,
            openai_model=openai_model,
            gemini_model=gemini_model,
            temperature=temperature,
            openai_web_search=openai_web_search,
            detail_log=detail_log,
            progress=prog_cb,
        )
        if folder_progress:
            folder_progress(folder_i, total, folder.name)
        if out:
            saved.append(out)
            if on_each_saved:
                on_each_saved(out[0], out[1])
    return saved


def list_completed_md_files_in_folder(folder: Path) -> List[Path]:
    """폴더 바로 아래 `*_completed_*.md`만(하위 폴더 제외), 이름순."""
    if not folder.is_dir():
        return []
    return sorted(
        (p for p in folder.glob("*_completed_*.md") if p.is_file()),
        key=lambda p: p.name.lower(),
    )


def export_html_from_completed_md_in_folder(
    folder: Path,
    log: Optional[Callable[[str], None]] = None,
    on_each_html: Optional[Callable[[str], None]] = None,
) -> List[str]:
    """
    `*_completed_*.md` 각각을 읽어 동일 파일명(확장자만 .html)으로 저장. API 미사용.
    반환: 생성·덮어쓴 HTML 파일 절대 경로 목록.
    on_each_html(html_abs_path): 각 HTML 저장 직후 호출.
    """
    saved: List[str] = []
    for md_path in list_completed_md_files_in_folder(folder):
        try:
            body = md_path.read_text(encoding="utf-8")
            html_path = md_path.with_suffix(".html")
            html_path.write_text(wrap_blog_markdown_as_html(body), encoding="utf-8")
            resolved = str(html_path.resolve())
            saved.append(resolved)
            if log:
                log(f"[HTML 생성] {html_path.name}")
            if on_each_html:
                on_each_html(resolved)
        except Exception as e:
            if log:
                log(f"[HTML 생성] 실패 {md_path.name}: {e}")
    return saved
