"""Extract only measurable style traits from the first eligible blog post."""

from collections import Counter
from dataclasses import asdict, dataclass
import html
import re
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup


ARTICLE_SELECTORS = (".se-main-container", "#postViewArea", ".post_ct")
EXCLUDED_IMAGE_CLASS = re.compile(r"profile|avatar|sticker|emoji|emoticon|advert|banner|icon", re.I)
MIN_BODY_CHARS = 80


class ArticleAnalysisError(ValueError):
    pass


def visible_char_count(text: str) -> int:
    """Shared final step for source HTML and generated Markdown body counts."""
    return len(re.sub(r"\s+", "", html.unescape(text)))


def markdown_body_text(markdown: str) -> str:
    text = re.sub(r"```.*?```", "", markdown, flags=re.S)
    text = re.sub(r"`[^`]*`", "", text)
    text = re.sub(r"!\[[^]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"</?[A-Za-z][^>]*>", "", text)
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("# ") or stripped.startswith("#\t"):
            continue
        if stripped.startswith("#") and not stripped.startswith("##"):
            continue
        if re.fullmatch(r"(?:#[^\s#]+\s*)+", stripped):
            continue
        line = re.sub(r"^\s{0,3}#{2,6}\s*", "", line)
        line = re.sub(r"[*_~`>|]", "", line)
        line = re.sub(r"https?://\S+", "", line)
        lines.append(line)
    return "\n".join(lines)


def _source_body_text(container: BeautifulSoup) -> tuple[str, list[str]]:
    clone = BeautifulSoup(str(container), "html.parser")
    for tag in clone.select("script, style, noscript, code, pre, img, iframe"):
        tag.decompose()
    paragraphs = []
    nodes = clone.select("h2, h3, .se-section-title, p, li")
    if not nodes:
        nodes = clone.select(".se-section-title, .se-text-paragraph, .se-text")
    selected_ids = {id(node) for node in nodes}
    for node in nodes:
        if any(id(parent) in selected_ids for parent in node.parents):
            continue
        text = node.get_text(" ", strip=True)
        if text and not re.fullmatch(r"(?:#[^\s#]+\s*)+", text):
            paragraphs.append(text)
    if not paragraphs:
        text = clone.get_text(" ", strip=True)
        if text:
            paragraphs = [text]
    return "\n".join(paragraphs), paragraphs


def _image_url(img) -> str | None:
    for key in ("data-lazy-src", "data-src", "data-original", "data-lazy", "src"):
        value = (img.get(key) or "").strip()
        if value.startswith("//"):
            value = "https:" + value
        if value.startswith(("https://", "http://")):
            return value
    return None


def _photo_count(container: BeautifulSoup) -> int:
    urls = set()
    for component in container.select(".se-component.se-image"):
        if not component.select_one("img"):
            raise ArticleAnalysisError("첫 검색글의 동적 본문 사진을 해석할 수 없습니다.")
    for img in container.select("img"):
        ancestors = [img, *list(img.parents)]
        classes = " ".join(" ".join(node.get("class", [])) for node in ancestors if getattr(node, "get", None))
        if EXCLUDED_IMAGE_CLASS.search(classes):
            continue
        width, height = img.get("width"), img.get("height")
        if width and height and str(width).isdigit() and str(height).isdigit() and min(int(width), int(height)) < 48:
            continue
        url = _image_url(img)
        if not url:
            raise ArticleAnalysisError("첫 검색글의 본문 사진 주소를 해석할 수 없습니다.")
        parsed = urlsplit(url)
        urls.add(urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")))
    return len(urls)


@dataclass(frozen=True)
class SourceProfile:
    body_text: str
    body_chars: int
    photo_count: int
    tone_endings: dict[str, int]
    average_paragraph_chars: int
    paragraph_count: int
    heading_count: int

    def public_metrics(self) -> dict:
        result = asdict(self)
        result.pop("body_text", None)
        return result


def analyze_first_html(page_html: str) -> SourceProfile:
    soup = BeautifulSoup(page_html, "html.parser")
    container = next((soup.select_one(selector) for selector in ARTICLE_SELECTORS if soup.select_one(selector)), None)
    if container is None:
        raise ArticleAnalysisError("첫 검색글의 본문 영역을 찾지 못했습니다.")
    body_text, paragraphs = _source_body_text(container)
    if "본문 내용을 찾을 수 없습니다" in body_text or "본문 내용이 없습니다" in body_text:
        raise ArticleAnalysisError("첫 검색글의 본문이 비어 있습니다.")
    chars = visible_char_count(body_text)
    if chars < MIN_BODY_CHARS:
        raise ArticleAnalysisError(f"첫 검색글 본문이 너무 짧아 스타일을 분석할 수 없습니다 ({chars}자).")
    endings = Counter()
    for ending in re.findall(r"(?:어요|아요|해요|네요|죠|니다|습니까|다)(?=[.!?…]|$)", body_text):
        endings[ending] += 1
    return SourceProfile(
        body_text=body_text,
        body_chars=chars,
        photo_count=_photo_count(container),
        tone_endings=dict(endings),
        average_paragraph_chars=round(sum(visible_char_count(p) for p in paragraphs) / len(paragraphs)),
        paragraph_count=len(paragraphs),
        heading_count=len(container.select("h2, h3, .se-section-title, .se-component.se-sectionTitle")),
    )
