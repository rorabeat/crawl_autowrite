"""agents/AGENTS.md(기본 템플릿) 조회/편집 모듈.

docs/PRD.md 4.2, docs/ROADMAP.md Task 004("AGENTS.md 조회/편집 UI 구현")에서 실제 로직을
구현했다.

주의: 이 모듈은 AGENTS.md의 "내용"(페르소나/문체/폴더 규칙)을 프로그램이 자동으로
생성·재구성하지 않는다 — 사용자가 입력한 문자열을 그대로 읽고 쓸 뿐이다
(shrimp-rules.md 4절/7절).
"""

import logging
import re
from pathlib import Path

import config

logger = logging.getLogger(__name__)

# config.AGENTS_MD_PATH는 config.py(_WORK_ROOT) 기준 상대경로라 프로그램 실행 위치(CWD)와
# 무관하게 항상 crawl_autowrite/agents/AGENTS.md(기본 템플릿)를 가리킨다.
AGENTS_MD_PATH = config.AGENTS_MD_PATH


def create_agents_md(name: str) -> Path:
    """agents/ 안에 빈 템플릿을 생성한다. 기존 파일은 덮어쓰지 않는다."""
    name = name.strip()
    if name.lower().endswith(".md"):
        name = name[:-3]
    if (
        not name or name.endswith((".", " "))
        or re.search(r'[\\/:*?"<>|\x00-\x1f]', name)
        or name in {".", ".."}
        or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", name.split(".")[0])
    ):
        raise ValueError("폴더 경로나 특수문자 없이 올바른 파일 이름을 입력해 주세요.")
    config.AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.AGENTS_DIR / f"{name}.md"
    with path.open("x", encoding="utf-8"):
        pass
    return path


def load_agents_md(path: Path = AGENTS_MD_PATH) -> str:
    """agents/AGENTS.md(기본 템플릿) 내용을 읽어 반환한다."""
    content = path.read_text(encoding="utf-8")
    logger.info("agents_editor.load_agents_md: %s (%d자)", path, len(content))
    return content


def save_agents_md(content: str, path: Path = AGENTS_MD_PATH) -> None:
    """편집된 내용을 agents/AGENTS.md(기본 템플릿)에 그대로 저장한다(내용 재구성 없음)."""
    path.write_text(content, encoding="utf-8")
    logger.info("agents_editor.save_agents_md: %s (%d자)", path, len(content))
