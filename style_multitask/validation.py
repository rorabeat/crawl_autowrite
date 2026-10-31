"""Deterministic gates before publication; semantic originality needs independent review."""

from math import ceil, floor
from pathlib import Path
import re

from PIL import Image, UnidentifiedImageError

from .profile import markdown_body_text, visible_char_count


IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
VISITED_RE = re.compile(r"(?:다녀왔|가봤|방문했|직접\s*(?:가서|먹어|봤|촬영)|제가\s*(?:본|먹은|찍은))")


def validate_article(md_path: Path, source_body: str, source_chars: int,
                     image_target: int, generated_paths: list[Path]) -> list[str]:
    if not md_path.is_file():
        return ["생성된 Markdown 글이 없습니다."]
    text = md_path.read_text(encoding="utf-8")
    errors = []
    if not re.search(r"^#\s+\S", text, flags=re.M):
        errors.append("H1 제목이 없습니다.")
    body = markdown_body_text(text)
    length = visible_char_count(body)
    low, high = ceil(source_chars * 0.9), floor(source_chars * 1.1)
    if not low <= length <= high:
        errors.append(f"본문 길이 {length}자가 목표 {low}~{high}자를 벗어났습니다.")
    if VISITED_RE.search(body):
        errors.append("직접 방문·체험한 듯한 표현이 있습니다.")

    normalized_source = re.sub(r"\s+", "", source_body)
    normalized_body = re.sub(r"\s+", "", body)
    if len(normalized_source) >= 50:
        for start in range(0, len(normalized_source) - 49):
            if normalized_source[start:start + 50] in normalized_body:
                errors.append("원문과 동일한 긴 구절이 있습니다.")
                break

    refs = IMAGE_RE.findall(text)
    ref_paths: set[Path] = set()
    for ref in refs:
        if ref.startswith(("http://", "https://", "data:")):
            errors.append("원격 이미지 참조가 있습니다.")
            continue
        path = (md_path.parent / ref.split()[0]).resolve()
        try:
            relative = path.relative_to(md_path.parent.resolve())
        except ValueError:
            errors.append("작업 폴더 밖 이미지 참조가 있습니다.")
            continue
        if not relative.parts or relative.parts[0] != "images":
            errors.append("이미지는 images/ 폴더에서 참조해야 합니다.")
        if not path.is_file() or path.stat().st_size == 0:
            errors.append("존재하지 않거나 빈 이미지 파일 참조가 있습니다.")
        ref_paths.add(path)
    actual = set()
    for path in generated_paths:
        if not path.is_file() or path.stat().st_size == 0:
            errors.append("생성 이미지 파일이 비어 있거나 존재하지 않습니다.")
            continue
        try:
            with Image.open(path) as image:
                image.verify()
        except (OSError, UnidentifiedImageError, ValueError):
            errors.append("디코딩할 수 없는 생성 이미지 파일이 있습니다.")
            continue
        actual.add(path.resolve())
    if len(refs) != image_target or len(ref_paths) != image_target or len(actual) != image_target:
        errors.append(f"AI 이미지 {image_target}장과 본문 참조·실제 파일 수가 일치하지 않습니다.")
    if ref_paths != actual:
        errors.append("본문 이미지가 이번 시도에 생성된 이미지와 일치하지 않습니다.")
    return errors
