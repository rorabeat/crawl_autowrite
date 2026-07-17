"""PostResult/AGENTS.md 조회/편집 모듈.

docs/PRD.md 4.2, docs/ROADMAP.md Task 004("AGENTS.md 조회/편집 UI 구현")에서 실제 로직을
구현했다.

주의: 이 모듈은 PostResult/AGENTS.md의 "내용"(페르소나/문체/폴더 규칙)을 프로그램이
자동으로 생성·재구성하지 않는다 — 사용자가 입력한 문자열을 그대로 읽고 쓸 뿐이다
(shrimp-rules.md 4절/7절).
"""

import logging
from pathlib import Path

import config

logger = logging.getLogger(__name__)

# config.AGENTS_MD_PATH는 config.py(_WORK_ROOT) 기준 상대경로라 프로그램 실행 위치(CWD)와
# 무관하게 항상 crawl_autowrite/PostResult/AGENTS.md를 가리킨다.
AGENTS_MD_PATH = config.AGENTS_MD_PATH


def load_agents_md(path: Path = AGENTS_MD_PATH) -> str:
    """PostResult/AGENTS.md 내용을 읽어 반환한다."""
    content = path.read_text(encoding="utf-8")
    logger.info("agents_editor.load_agents_md: %s (%d자)", path, len(content))
    return content


def save_agents_md(content: str, path: Path = AGENTS_MD_PATH) -> None:
    """편집된 내용을 PostResult/AGENTS.md에 그대로 저장한다(내용 재구성 없음)."""
    path.write_text(content, encoding="utf-8")
    logger.info("agents_editor.save_agents_md: %s (%d자)", path, len(content))
