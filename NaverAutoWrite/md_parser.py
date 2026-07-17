# 구현 기능: F004
"""마크다운 파싱 모듈.

md 파일에서 제목(첫 H1), 본문 줄 리스트, 이미지 경로 목록을 추출하여
editor.py/image_uploader.py에 전달할 ParsedMarkdown으로 변환한다.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import mistune


@dataclass
class ImageRef:
    path: str
    is_local: bool


@dataclass
class ParsedMarkdown:
    title: str
    body_lines: list[str]
    images: list[ImageRef]


def _extract_title(tokens: list[dict]) -> str | None:
    for token in tokens:
        if token.get("type") == "heading" and token.get("attrs", {}).get("level") == 1:
            return "".join(
                child.get("raw", "") for child in token.get("children", [])
            )
    return None


def _extract_images(tokens: list[dict]) -> list[ImageRef]:
    """마크다운 토큰에서 이미지 참조를 뽑는다.

    mistune은 이미지 목적지(url)를 파싱할 때 한글 등 비-ASCII 문자와 백슬래시(\\)를
    퍼센트 인코딩한다(예: "테스트.jpg" -> "%ED%85%8C..."). editor.py는 이 path를 원본
    md 줄 텍스트와 부분 문자열로 비교해 로컬 이미지 위치를 찾는데, 인코딩된 문자열은
    원본 줄과 절대 일치하지 않아 이미지가 실제로는 삽입되지 않고 마크다운 문법
    텍스트가 그대로 타이핑되는 버그가 있었다(실측 확인). unquote()로 원본 문자열
    그대로 복원해 이 문제를 없앤다.
    """
    images: list[ImageRef] = []
    for token in tokens:
        for child in token.get("children") or []:
            if child.get("type") == "image":
                url = unquote(child.get("attrs", {}).get("url", ""))
                is_local = urlparse(url).scheme not in ("http", "https")
                images.append(ImageRef(path=url, is_local=is_local))
    return images


def parse_markdown(md_path: str) -> ParsedMarkdown:
    """md 파일을 읽어 ParsedMarkdown을 반환한다 (F004).

    - title: 첫 번째 H1(`# 제목`) 텍스트
    - body_lines: 본문을 줄 단위로 분리한 리스트(제목 줄 제외)
    - images: `![alt](path)` 문법에서 추출한 이미지 경로 목록(로컬/원격 구분 포함)
    """
    path = Path(md_path)
    if not path.exists():
        print(f"에러: md 파일을 찾을 수 없습니다: {md_path}", file=sys.stderr)
        sys.exit(1)

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    markdown = mistune.create_markdown(renderer=None)
    tokens = markdown(text)

    title = _extract_title(tokens)
    if title is None:
        print(f"에러: md 파일에서 제목(H1)을 찾을 수 없습니다: {md_path}", file=sys.stderr)
        sys.exit(1)

    title_line = f"# {title}"
    body_lines = [line for line in lines if line.strip() != title_line]

    images = _extract_images(tokens)

    return ParsedMarkdown(title=title, body_lines=body_lines, images=images)
