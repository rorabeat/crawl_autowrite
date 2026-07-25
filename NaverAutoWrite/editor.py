# 구현 기능: F003, F005, F006, F008, F012
"""네이버 블로그 글쓰기 에디터 조작 모듈.

글쓰기 페이지 진입/초기화, 제목/본문 입력, 발행 처리를 담당한다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from image_uploader import upload_image
from md_parser import ParsedMarkdown
from naver_login import DEFAULT_TIMEOUT_MS, wait_for_selector

WRITE_URL_TEMPLATE = "https://blog.naver.com/{blog_id}?Redirect=Write&"
OPTIONAL_POPUP_SELECTORS = (".se-popup-button-cancel", ".se-help-panel-close-button")
POPUP_CHECK_TIMEOUT_MS = 3_000
TYPE_DELAY_MS = 30
# 소제목 입력 직후 안전 대기(ms). image_uploader.UPLOAD_SAFETY_DELAY_MS와 같은 이유 —
# 소제목 문단이 끝난 직후 곧바로 다음 줄을 타이핑하면 에디터가 새 문단을 아직 완전히
# 안정시키지 못한 상태라 type()이 멈추는 문제가 실측 확인됐다(md의 문단 구분용 빈 줄을
# 건너뛰게 하면서 우연히 있던 완충 시간이 사라진 것).
HEADING_SAFETY_DELAY_MS = 500
PUBLISHED_URL_PATTERN = re.compile(r"blog\.naver\.com/[^/]+/\d+")
HEADING_PREFIX = "## "
SUBHEADING_STYLE_TIMEOUT_MS = 3_000
# 실측 DOM(사용자 제공): 인용구 스타일 옵션(인용구 3 = 말풍선형)은
# button[data-group="documentToolbar"][data-name="quotation"][data-role="option"][data-value="quotation_bubble"]
# 형태다. 트리거 버튼은 옵션과 같은 data-group/data-name을 쓰지만 data-role="option"이
# 없어(:not()으로 옵션과 구분) 열림 버튼만 고를 수 있다.
QUOTATION_TRIGGER_SELECTOR = '[data-group="documentToolbar"][data-name="quotation"]:not([data-role="option"])'
QUOTATION_BUBBLE_OPTION_SELECTOR = (
    '[data-group="documentToolbar"][data-name="quotation"]'
    '[data-role="option"][data-value="quotation_bubble"]'
)

# 링크 처리: 본문 줄이 마크다운 링크(`[텍스트](url)`) 또는 단독 URL 한 줄인 경우,
# 화면에는 "[링크 클릭]"만 타이핑해두고 문서 전체 타이핑이 끝난 뒤 실제 URL을 연결한
# 하이퍼링크로 바꾼다(소제목과 같은 이유로 지연 처리 — 서식 적용 직후 커서의
# "다음 입력 서식"이 뒤에 오는 텍스트에 번지는 문제를 피한다).
LINK_PLACEHOLDER = "[링크 클릭]"
MD_LINK_LINE_PATTERN = re.compile(r"^\[[^\]]*\]\((https?://[^)\s]+)\)$")
BARE_URL_LINE_PATTERN = re.compile(r"^(https?://\S+)$")
LINK_STYLE_TIMEOUT_MS = 5_000
# 실측 DOM(사용자 제공): 링크 버튼은 documentToolbar의 oglink 버튼이고, 클릭하면 뜨는
# 팝업의 URL 입력창은 input.se-popup-oglink-input, 확인 버튼은
# button.se-popup-button-confirm이다(값이 비어 있으면 disabled 상태).
LINK_BUTTON_SELECTOR = '[data-group="documentToolbar"][data-name="oglink"]'
LINK_URL_INPUT_SELECTOR = "input.se-popup-oglink-input"
LINK_CONFIRM_BUTTON_SELECTOR = "button.se-popup-button-confirm"


class EditorError(Exception):
    """에디터 진입/조작 중 복구 불가능한 오류가 발생했을 때 발생한다."""


def _dismiss_optional_popups(frame: Any) -> None:
    """존재하는 경우에만 팝업/헬프패널을 닫는다 (F012). 없으면 조용히 넘어간다."""
    for selector in OPTIONAL_POPUP_SELECTORS:
        try:
            frame.locator(selector).first.click(timeout=POPUP_CHECK_TIMEOUT_MS)
        except Exception:
            continue


def _dismiss_draft_recovery_popup(frame: Any) -> None:
    """"작성 중인 글이 있습니다"(이전 임시저장 이어쓰기 확인) 팝업이 뜨면 "취소"를 눌러
    이전 글을 이어쓰지 않고 새 글쓰기를 그대로 진행한다(자동화가 이 팝업에서 멈추는
    문제, 실측 확인). 해시 클래스 대신 버튼 이름(텍스트/role)으로 찾는다 — 팝업이
    없으면 조용히 넘어간다.
    """
    try:
        frame.get_by_role("button", name="취소", exact=True).click(timeout=POPUP_CHECK_TIMEOUT_MS)
    except Exception:
        pass


def open_editor(page: Any, blog_id: str) -> Any:
    """글쓰기 페이지로 이동해 #mainFrame으로 전환하고 팝업/헬프패널을 정리한다.

    (F003) blog.naver.com/{blog_id}?Redirect=Write& 이동 및 #mainFrame 전환.
    (F012) "작성 중인 글이 있습니다" 팝업은 취소로 닫고(새 글쓰기 진행),
    .se-popup-button-cancel, .se-help-panel-close-button 존재 시에만 클릭.
    """
    page.goto(WRITE_URL_TEMPLATE.format(blog_id=blog_id))

    try:
        wait_for_selector(page, "#mainFrame")
    except Exception as exc:
        raise EditorError(f"#mainFrame을 찾을 수 없습니다 (blog_id={blog_id}).") from exc

    frame = page.frame_locator("#mainFrame")
    _dismiss_draft_recovery_popup(frame)
    _dismiss_optional_popups(frame)
    return frame


def input_title(frame: Any, title: str) -> None:
    """.se-section-documentTitle에 제목을 글자 단위 지연 타이핑한다 (F005)."""
    title_locator = frame.locator(".se-section-documentTitle")
    title_locator.click()
    title_locator.type(title, delay=TYPE_DELAY_MS)


def input_body(frame: Any, parsed: ParsedMarkdown, base_dir: Path) -> None:
    """.se-section-text에 본문을 줄 단위 지연 타이핑한다 (F006).

    본문 줄이 로컬 이미지 참조 경로를 포함하면 image_uploader.upload_image를
    호출해 해당 위치에 실제 이미지를 삽입하고(대체 텍스트/마크다운 문법은 타이핑하지
    않음), "## "로 시작하는 소제목 줄은 접두어만 뗀 순수 텍스트로 그대로 입력한다.
    줄 전체가 마크다운 링크(`[텍스트](url)`) 또는 단독 URL이면 화면에는
    LINK_PLACEHOLDER("[링크 클릭]")만 타이핑하고 실제 URL은 나중에 하이퍼링크로
    연결한다. 각 줄 처리 후 Enter로 줄바꿈한다. md의 문단 구분용 빈 줄은 타이핑하지
    않고 건너뛴다(그대로 타이핑하면 문장 사이마다 빈 문단이 하나 더 생겨 간격이
    두 배로 벌어진다). 대신 소제목(##) 줄 앞에는 Enter를 한 번 더 눌러 이전 문단과의
    간격을 의도적으로 한 줄 더 띄운다. 소제목 문단을 끝낸 직후에는
    HEADING_SAFETY_DELAY_MS만큼 대기한다(에디터가 새 문단을 안정시키기 전에 바로
    다음 줄을 타이핑하면 그 타이핑이 멈춰버리는 문제가 실측 확인됨).

    md 안의 이미지 경로는 "images/파일명"처럼 md 파일 자신을 기준으로 한 상대경로로
    적혀 있을 수 있다(사용자가 md를 직접 열어봐도 알아보기 쉽도록). 이 경로를 실제
    파일로 찾을 때는 프로세스의 현재 작업 디렉터리가 아니라 base_dir(=md 파일이 있는
    폴더, main.py가 --md 인자에서 계산해 넘겨줌) 기준으로 해석해야 한다 — 그래야
    main.py를 어느 디렉터리에서 실행하든 항상 올바른 파일을 찾는다.

    타이핑 도중에는 인용구/링크 등 서식을 절대 건드리지 않는다 — 서식을 적용한
    직후의 커서는 "다음 입력 서식"으로 방금 서식을 그대로 물려받아서, 소제목/링크
    다음에 오는 본문까지 그 서식을 물려받는 문제가 있었다(소제목 기준 실측 확인,
    커서 위치에서 명시적으로 되돌리는 시도도 안정적이지 않았다). 그래서 본문 전체를
    순수 텍스트(링크는 LINK_PLACEHOLDER)로 다 입력한 뒤, 함수 마지막에 소제목
    텍스트/링크 URL을 모아 한 번에 _apply_subheading_style / _apply_link로 서식을
    적용한다 — 이 시점 이후로는 더 타이핑할 내용이 없으므로 "다음 입력 서식 오염"
    문제 자체가 발생할 수 없다.

    .se-section-text locator를 한 번만 잡아 루프 내내 재사용하지 않고 매번
    _current_text_section(frame)으로 새로 조회한다 — 이미지를 삽입하면 네이버
    에디터가 그 아래에 새 .se-section-text 블록을 추가해 문서 안에 이 클래스를 가진
    요소가 2개 이상 존재하게 되고, 캐시해둔 locator로 이후 type()/press()를 호출하면
    "strict mode violation: resolved to 2 elements"로 죽는다(실측 확인).
    """
    _current_text_section(frame).click()

    local_image_paths = [img.path for img in parsed.images if img.is_local]
    heading_texts: list[str] = []
    link_urls: list[str] = []
    # 이번 실행에서 새로 타이핑할 LINK_PLACEHOLDER보다 앞서 문서에 이미 존재하는
    # 개수. 플레이스홀더 텍스트가 모든 링크에서 동일하므로, 텍스트만으로는 어떤
    # 인스턴스인지 구분할 수 없어 이 개수를 기준으로 nth() 인덱스를 계산한다.
    existing_link_count = frame.get_by_text(LINK_PLACEHOLDER, exact=True).count()

    for line in parsed.body_lines:
        stripped = line.strip()
        if stripped == "":
            continue

        matched_path = next(
            (path for path in local_image_paths if path in line), None
        )
        md_link_match = MD_LINK_LINE_PATTERN.match(stripped)
        bare_url_match = BARE_URL_LINE_PATTERN.match(stripped)
        if matched_path:
            candidate = Path(matched_path)
            resolved_path = str(candidate if candidate.is_absolute() else (base_dir / candidate).resolve())
            upload_image(frame, resolved_path)
        elif line.startswith(HEADING_PREFIX):
            _current_text_section(frame).press("Enter")
            heading_text = line[len(HEADING_PREFIX) :]
            _current_text_section(frame).type(heading_text, delay=TYPE_DELAY_MS)
            heading_texts.append(heading_text)
            _current_text_section(frame).press("Enter")
            frame.owner.page.wait_for_timeout(HEADING_SAFETY_DELAY_MS)
            continue
        elif md_link_match or bare_url_match:
            url = md_link_match.group(1) if md_link_match else bare_url_match.group(1)
            _current_text_section(frame).type(LINK_PLACEHOLDER, delay=TYPE_DELAY_MS)
            link_urls.append(url)
        else:
            _current_text_section(frame).type(line, delay=TYPE_DELAY_MS)
        _current_text_section(frame).press("Enter")

    for heading_text in heading_texts:
        _apply_subheading_style(frame, heading_text)

    for index, url in enumerate(link_urls):
        _apply_link(frame, existing_link_count + index, url)


def _current_text_section(frame: Any) -> Any:
    """현재 타이핑 대상인 .se-section-text를 매번 새로 조회해서 반환한다.

    이미지 삽입 등으로 .se-section-text가 여러 개 생겼을 때 항상 가장 마지막(최신,
    보통 커서가 가 있는) 섹션을 골라야 하므로 .last를 쓴다.
    """
    return frame.locator(".se-section-text").last


def _apply_quotation_bubble(frame: Any) -> None:
    """상단 인용구 드롭다운을 열어 "인용구 3"(말풍선형, quotation_bubble)을 선택한다.

    실측 DOM(사용자 제공)에 정확히 맞춘 선택자를 쓴다: 트리거 버튼(QUOTATION_TRIGGER_SELECTOR)을
    클릭해 옵션 목록을 연 뒤, data-value="quotation_bubble"인 option 버튼을 클릭한다.
    """
    frame.locator(QUOTATION_TRIGGER_SELECTOR).first.click(timeout=SUBHEADING_STYLE_TIMEOUT_MS)
    frame.locator(QUOTATION_BUBBLE_OPTION_SELECTOR).click(timeout=SUBHEADING_STYLE_TIMEOUT_MS)


def _apply_subheading_style(frame: Any, heading_text: str) -> None:
    """이미 입력이 끝난 소제목 문단에 커서를 두고 "인용구 3"(말풍선형) 서식을 적용한다.

    input_body가 본문 전체 타이핑을 다 끝낸 뒤에만 이 함수를 호출한다. 타이핑 중간에
    바로 서식을 적용하는 방식은 두 가지 실측 버그가 있었다: (1) 타이핑 직후 에디터
    내부 렌더링이 안정되기 전에 서식 조작을 하면 레이스 컨디션으로 그 줄 자체가
    삭제되는 경우가 있었고, (2) 서식 적용 직후 커서의 "다음 입력 서식"이 그대로 남아
    이어서 타이핑하는 본문까지 소제목 서식을 물려받았다.

    인용구 서식은 (글자 크기/굵게와 달리) 텍스트를 선택하지 않고 문단 안에 커서만
    있어도 해당 블록 전체에 적용되는 블록 단위 서식이다(실측 확인). 그래서 문단
    텍스트를 한 번만 클릭해 커서를 둔 뒤 인용구 드롭다운에서 옵션을 선택한다. 동일
    텍스트가 이전 실행에서 누적돼 여러 개 있을 수 있으므로 .last(가장 최근에 입력된
    것)를 쓴다.
    """
    try:
        target = frame.get_by_text(heading_text, exact=True).last
        target.click(timeout=SUBHEADING_STYLE_TIMEOUT_MS)
        _apply_quotation_bubble(frame)
    except Exception:
        print(f"경고: 소제목 스타일 적용 실패({heading_text!r}), 일반 글자로 남습니다.", file=sys.stderr)


def _apply_link(frame: Any, index: int, url: str) -> None:
    """index번째 LINK_PLACEHOLDER("[링크 클릭]") 문단을 url로 연결된 하이퍼링크로 바꾼다.

    input_body가 본문 전체 타이핑을 다 끝낸 뒤에만 이 함수를 호출한다(이유는
    _apply_subheading_style 참조 — 서식 적용 직후 "다음 입력 서식"이 뒤에 오는
    텍스트에 번지는 문제를 피하기 위함).

    플레이스홀더 텍스트가 모든 링크에서 동일해 텍스트만으로는 어떤 인스턴스인지
    구분할 수 없으므로, get_by_text(...).last 대신 input_body가 계산해 넘겨준
    절대 인덱스(.nth(index))로 정확한 문단을 고른다. 실측 DOM(사용자 제공) 기준
    흐름: 문단 텍스트 3연속 클릭으로 전체 선택 -> 링크 버튼(oglink) 클릭 -> 팝업의
    URL 입력창에 url 입력 -> 확인 버튼(초기 disabled, 값이 채워지면 활성화) 클릭.
    """
    try:
        target = frame.get_by_text(LINK_PLACEHOLDER, exact=True).nth(index)
        target.click(click_count=3, timeout=LINK_STYLE_TIMEOUT_MS)
        frame.locator(LINK_BUTTON_SELECTOR).click(timeout=LINK_STYLE_TIMEOUT_MS)
        frame.locator(LINK_URL_INPUT_SELECTOR).fill(url)
        frame.locator(LINK_CONFIRM_BUTTON_SELECTOR).click(timeout=LINK_STYLE_TIMEOUT_MS)
    except Exception:
        print(f"경고: 링크 적용 실패({url!r}), 일반 텍스트로 남습니다.", file=sys.stderr)


def publish(page: Any, category: str | None) -> None:
    """텍스트/role 기반 셀렉터로 발행 버튼을 클릭해 옵션 패널을 연 뒤 최종 발행한다 (F008).

    해시 클래스 셀렉터는 사용하지 않으며, 발행 버튼 -> 카테고리/공개설정 패널
    -> 최종 발행 버튼의 2단계 흐름으로 처리한다. 발행 버튼과 패널은 페이지가
    아니라 `#mainFrame` 내부에 위치하고(role="dialog"가 아님), "발행" 텍스트는
    상단 버튼과 패널 확인 버튼에 모두 존재하므로 first/last로 구분한다.
    카테고리 드롭다운은 고정 aria-label("카테고리 목록 버튼")로 식별한다.
    """
    frame = page.frame_locator("#mainFrame")

    frame.get_by_role("button", name="발행", exact=True).first.click()

    category_dropdown = frame.get_by_role("button", name="카테고리 목록 버튼")
    category_dropdown.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)

    if category:
        print(f"진행 상황: 카테고리 선택 - {category}")
        category_dropdown.click()
        frame.get_by_text(category, exact=True).first.click()

    frame.get_by_role("button", name="발행", exact=True).last.click()

    try:
        page.wait_for_url(PUBLISHED_URL_PATTERN, timeout=DEFAULT_TIMEOUT_MS)
    except Exception as exc:
        raise EditorError(
            f"발행 완료를 확인할 수 없습니다 (기대 URL 패턴: {PUBLISHED_URL_PATTERN.pattern})."
        ) from exc


def save_draft(page: Any) -> None:
    """실제 발행 대신 임시저장으로 글을 남긴다.

    publish()와 달리 옵션 패널/최종 확인 없이 상단 툴바의 "저장" 버튼(실측: 접근성
    이름이 "저장"이고 옆의 숫자 배지는 별도 요소) 한 번으로 끝나는 단발 동작이다.
    자동화가 실수로 바로 발행해버리는 사고를 막기 위해 main.py는 이 함수를 기본으로
    호출한다(발행은 사람이 네이버 블로그 글쓰기 화면에서 직접 확인 후 진행).

    "임시저장되었습니다" 토스트로 완료 확인을 시도하지만, 실측 결과 이 문구가 뜨지
    않거나 다른 문구/타이밍이라 못 찾는 경우가 있었다. 저장 버튼 클릭 자체는
    확실히 일어났고 네이버 에디터는 어차피 주기적으로 자동 임시저장도 하므로,
    토스트를 못 찾았다고 EditorError를 던져 전체 실행을 실패로 만들지 않고
    경고만 남긴다.
    """
    frame = page.frame_locator("#mainFrame")

    frame.get_by_role("button", name="저장", exact=True).click()

    try:
        frame.get_by_text("임시저장되었습니다").wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
    except Exception:
        print(
            "경고: \"저장\" 버튼은 클릭했지만 임시저장 확인 토스트를 찾지 못했습니다 "
            "(저장 자체는 됐을 가능성이 높음 — 네이버 블로그에서 직접 확인하세요).",
            file=sys.stderr,
        )
