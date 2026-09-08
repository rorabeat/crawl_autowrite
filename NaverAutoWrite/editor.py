# 구현 기능: F003, F005, F006, F008, F012
"""네이버 블로그 글쓰기 에디터 조작 모듈.

글쓰기 페이지 진입/초기화, 제목/본문 입력, 발행 처리를 담당한다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
# 인용구(소제목) 서식 적용 직후 안전 대기(ms). _apply_quotation_line 클릭 직후
# 곧바로 타이핑하면 에디터가 인용구 컴포넌트를 아직 완전히 생성하지 못한 상태라
# 글자가 일부 누락되는 현상이 실측 확인됐다(사용자 보고).
HEADING_FORMAT_APPLY_DELAY_MS = 1_000
PUBLISHED_URL_PATTERN = re.compile(r"blog\.naver\.com/[^/]+/\d+")
HEADING_PREFIX = "## "
SUBHEADING_STYLE_TIMEOUT_MS = 3_000
# 실측 DOM(라이브 디버깅으로 직접 확인): 인용구 스타일 옵션(인용구 2 = 버티컬 라인형)은
# button[data-group="documentToolbar"][data-name="quotation"][data-role="option"][data-value="quotation_line"]
# 형태다. data-role="option"이 없는 버튼이 트리거인데, 이 조건을 만족하는 버튼이
# 실제로는 "두 개" 있다 — (1) data-value="default"에 aria-haspopup="false"인 "인용구
# 추가"(퀵버튼, 클릭하면 드롭다운 없이 바로 기본 인용구가 삽입됨), (2) aria-haspopup="true"인
# "인용구 선택"(진짜 드롭다운 트리거). :not([data-role="option"])만으로는 이 둘을 구분하지
# 못해 .first가 (1)을 집어 클릭해버렸고, 그 결과 드롭다운 자체가 안 열려 옵션 버튼을 못 찾고
# 타임아웃이 났다(실측 확인 — main.py 라이브 실행 크래시 재현 후 디버그 스크립트로 DOM
# 덤프해서 확인). aria-haspopup="true" 조건을 추가해 (2)만 고른다.
QUOTATION_TRIGGER_SELECTOR = (
    '[data-group="documentToolbar"][data-name="quotation"]'
    ':not([data-role="option"])[aria-haspopup="true"]'
)
QUOTATION_LINE_OPTION_SELECTOR = (
    '[data-group="documentToolbar"][data-name="quotation"]'
    '[data-role="option"][data-value="quotation_line"]'
)
# 인용구는 텍스트 문단이 아니라 이미지처럼 독립된 se-component다(실측 확인). 안에서
# Enter를 아무리 눌러도(직접 확인함, 4번까지 시도) 컴포넌트를 못 벗어나고 인용구
# 내부에 새 줄만 계속 생긴다 — 출처 입력란까지 있는 하나의 블록이라 "끝"이 없다.
# 유일하게 확인된 탈출 방법은 컴포넌트의 bounding box 바로 아래 빈 영역을 마우스로
# 클릭하는 것(라이브 디버깅으로 확인 — 클릭 후 상단 문단 스타일 표시가 "인용구"에서
# "본문"으로 바뀌고, 그 지점에 새 일반 문단이 생겨 이어서 타이핑한 텍스트가 인용구
# 서식을 물려받지 않았다).
QUOTATION_COMPONENT_SELECTOR = ".se-component.se-quotation"
QUOTATION_EXIT_CLICK_MARGIN_PX = 15

# 링크 처리: 네이버 에디터의 "링크"(oglink) 버튼은 선택한 텍스트를 밑줄 하이퍼링크로
# "감싸는" 게 아니라, 선택 영역을 통째로 이미지/제목/설명이 있는 미리보기 카드로
# "바꿔버리는" 기능이다(라이브 디버깅으로 실측 확인 — 문장 중간 한 단어만 선택해서
# 링크를 걸었더니 그 단어가 사라지고 카드가 그 자리에 블록으로 삽입되면서 문장이
# 앞/뒤로 쪼개졌다). 그래서 "문장 중간 단어에만 밑줄 링크 걸기"는 이 에디터에서
# 애초에 불가능하고, 대신 링크가 있는 줄은 [링크 앞 문장]/[링크 카드]/[링크 뒤 문장]
# 3개의 별도 문단으로 쪼갠다(_split_line_by_links) — 원본 문장 내용은 그대로 남고
# URL도 화면에 노출되지 않으면서, 링크는 카드로 자연스럽게 표시된다.
#
# 카드 변환에 성공하면 그 문단에 무슨 텍스트가 있었는지는 최종 결과에 영향이 없지만
# (카드가 완전히 대체), 네트워크 문제 등으로 변환에 실패하면(_apply_link 참조) 그
# 텍스트가 그대로 화면에 남는다. 그래서 마크다운 링크(`[텍스트](url)`)는 앵커
# 텍스트를 그대로 쓰고(사용자 요청 — "[링크 클릭]" 같은 플레이스홀더 대신 링크의
# 적당한 단어로 표시), 앵커 텍스트가 없는 단독 URL만 도메인 기반 라벨
# (LINK_FALLBACK_LABEL_TEMPLATE)로 대체한다. 실제 하이퍼링크(카드 변환)는 문서
# 전체 타이핑이 끝난 뒤 연결한다(소제목과 같은 이유로 지연 처리 — 서식 적용 직후
# 커서의 "다음 입력 서식"이 뒤에 오는 텍스트에 번지는 문제를 피한다).
LINK_PLACEHOLDER = "[링크 클릭]"
LINK_FALLBACK_LABEL_TEMPLATE = "{domain} 바로가기"
INLINE_MD_LINK_FULL_PATTERN = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_LINE_LINK_PATTERN = re.compile(r"\[[^\]]+\]\(https?://[^)\s]+\)|https?://\S+")
# 단독 URL 뒤에 흔히 붙는(마크다운 문법이 아닌, 그냥 문장부호로 딸려온) 문자들 —
# URL 자체에는 포함시키지 않는다.
_TRAILING_URL_PUNCTUATION = ".,)]>\"'"
LINK_STYLE_TIMEOUT_MS = 5_000
# 네이버 링크 미리보기(OG) 조회는 외부 네트워크 요청이라 일시적으로 실패할 수 있어
# 돋보기(검색) 버튼을 최대 이 횟수만큼 다시 눌러본다(_apply_link 참조).
LINK_SEARCH_RETRY_COUNT = 3
# 실측 DOM(사용자 제공): 링크 버튼은 documentToolbar의 oglink 버튼이고, 클릭하면 뜨는
# 팝업의 URL 입력창은 input.se-popup-oglink-input, 확인 버튼은
# button.se-popup-button-confirm이다. 라이브 디버깅으로 확인한 사실: 확인 버튼은
# URL 입력창에 값만 채운다고 활성화되지 않는다 — 입력창 오른쪽의 돋보기(검색,
# se-popup-oglink-button) 버튼을 눌러 OG 미리보기 카드를 불러와야 비로소
# disabled가 풀린다(과거 코드는 이 단계 없이 바로 확인을 눌러서 계속 비활성 상태로
# 타임아웃 났었다).
LINK_BUTTON_SELECTOR = '[data-group="documentToolbar"][data-name="oglink"]'
LINK_URL_INPUT_SELECTOR = "input.se-popup-oglink-input"
LINK_SEARCH_BUTTON_SELECTOR = "button.se-popup-oglink-button"
LINK_CONFIRM_BUTTON_SELECTOR = "button.se-popup-button-confirm"
LINK_POPUP_CLOSE_BUTTON_SELECTOR = "button.se-popup-close-button"

# 하이라이트 처리: 본문 줄 안에 `**문구**`(마크다운 굵게 문법 재활용)로 감싼 구간을
# 가독성을 위한 배경색 강조 대상으로 본다. `**`는 항상 화면에서 벗겨내고(문자 그대로
# 노출하지 않음), 문서 전체에서 HIGHLIGHT_MAX_COUNT개까지만 실제로 배경색을 적용한다
# (그 이상은 마커만 벗기고 일반 텍스트로 남긴다 — 너무 많으면 가독성 향상이라는
# 목적과 반대로 작용하기 때문).
HIGHLIGHT_MARKER_PATTERN = re.compile(r"\*\*(.+?)\*\*")
HIGHLIGHT_MAX_COUNT = 5
# 사용자 제공 팔레트 기준 노란색 스와치.
HIGHLIGHT_COLOR_HEX = "#fff593"
HIGHLIGHT_TIMEOUT_MS = 5_000
# 실측 DOM(사용자 제공 + 라이브 디버깅으로 검증): 배경색 버튼은 텍스트를 선택했을 때만
# 뜨는 propertyToolbar(플로팅 툴바)에 있다. 색상 팔레트의 스와치 버튼은 "최근 사용한
# 색상" 영역과 "프리셋" 영역 두 곳에 동시에 존재해(같은 data-color) 매칭이 2개 나와서
# .first로 하나만 고른다(실측 확인 — strict mode violation 발생).
BACKGROUND_COLOR_BUTTON_SELECTOR = '[data-group="propertyToolbar"][data-name="background-color"]'
HIGHLIGHT_COLOR_SWATCH_SELECTOR = f'.se-color-palette[data-color="{HIGHLIGHT_COLOR_HEX}"]'
# 라이브 디버깅으로 확인한 사실: Range/Selection을 JS로만 설정하면(window.getSelection
# .addRange) 네이버 에디터 자체의 선택 동기화 로직이 갱신되지 않아 서식 버튼(배경색/
# 링크 모두)을 눌러도 아무 효과가 없었다. 그래서 JS로는 대상 문구의 화면 좌표
# (getClientRects)만 얻어오고, 실제 선택은 Playwright의 실제 마우스 드래그
# (mouse.move/down/up)로 만든다 — 이 방식만 동작을 확인했다. .se-section-text 안의
# 텍스트 노드만 훑는다 — 인용구(se-quotation)는 별도 컴포넌트라 소제목 텍스트는
# 검색 대상에서 자연히 제외된다. 하이라이트/링크 둘 다 이 함수를 공유한다
# (_select_text_occurrence).
_FIND_TEXT_RECT_JS = """
(phrase, occurrenceIndex) => {
    const sections = document.querySelectorAll('.se-section-text');
    let count = 0;
    for (const section of sections) {
        const walker = document.createTreeWalker(section, NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode())) {
            const idx = node.textContent.indexOf(phrase);
            if (idx !== -1) {
                if (count === occurrenceIndex) {
                    const range = document.createRange();
                    range.setStart(node, idx);
                    range.setEnd(node, idx + phrase.length);
                    const rects = range.getClientRects();
                    if (rects.length === 0) return null;
                    const first = rects[0];
                    const last = rects[rects.length - 1];
                    return {
                        startX: first.left,
                        startY: first.top + first.height / 2,
                        endX: last.right,
                        endY: last.top + last.height / 2,
                    };
                }
                count++;
            }
        }
    }
    return null;
}
"""


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
    줄 안(문장 중간 포함)에 마크다운 링크(`[텍스트](url)`) 또는 마크다운 문법 없는
    단독 URL이 있으면 _split_line_by_links로 줄을 [링크 앞 문장]/[링크]/[링크 뒤
    문장] 문단으로 쪼갠다(링크가 여러 개면 그만큼 더 쪼개진다) — 네이버 에디터의
    "링크"는 선택 영역을 밑줄 텍스트가 아니라 통째로 미리보기 카드로 바꾸는 기능이라
    문장 중간에 인라인으로 걸 수 없기 때문이다(_LINE_LINK_PATTERN 주석 참조). 각
    문단 처리 후 Enter로 줄바꿈한다. md의 문단 구분용 빈 줄은 타이핑하지
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

    본문 줄 안에 `**문구**`(마크다운 굵게 문법 재활용)가 있으면 `**`는 항상 벗겨내고
    (화면에 문자 그대로 노출하지 않음), 문서 전체에서 HIGHLIGHT_MAX_COUNT(5)개까지만
    가독성 강조용 배경색(노란색)을 적용한다 — 하이라이트는 링크와 달리 문장을 쪼개지
    않고 진짜 인라인 서식(문단 안 일부 텍스트에만 배경색)이 가능하다(실측 확인,
    _select_text_occurrence 참조). _consume_highlight_markers로 (문구, 등장순번)만
    모아두고 _apply_highlight는 본문 타이핑이 끝난 뒤 호출한다 — 이유는 아래 링크
    문단과 동일(서식 적용 직후 커서가 그 서식을 물려받아 다음 텍스트에 번지는 문제).

    링크(카드로 변환될 문단)의 텍스트("[링크 클릭]" 플레이스홀더)와 하이라이트
    서식은 절대 타이핑 도중 건드리지 않는다 — 서식을 적용한 직후의 커서는 "다음
    입력 서식"으로 방금 서식을 그대로 물려받아서, 다음에 오는 본문까지 그 서식을
    물려받는 문제가 있었다(실측 확인). 그래서 본문 전체를 순수 텍스트로 다 입력한
    뒤, 함수 마지막에 (텍스트, 등장 순번, ...)를 모아 한 번에 _apply_link /
    _apply_highlight로 서식/카드를 적용한다 — 이 시점 이후로는 더 타이핑할 내용이
    없으므로 "다음 입력 서식 오염" 문제 자체가 발생할 수 없다. 등장 순번은
    running_text(지금까지 타이핑된 순수 텍스트를 이어붙인 문자열)에서 대상 텍스트가
    몇 번째로 등장하는지 세서 구하며, 링크/하이라이트가 같은 running_text를
    공유한다 — 나중에 _FIND_TEXT_RECT_JS가 실제 DOM을 같은 순서로 훑으므로 인덱스가
    어긋나지 않는다.

    소제목(인용구)은 이 방식을 쓸 수 없다 — 실측 확인 결과 네이버 에디터의
    "인용구"는 글자크기/굵게처럼 이미 입력된 문단에 커서만 두고 적용하는 문단
    서식이 아니라, 이미지/코드블록처럼 별도 컴포넌트를 삽입하는 기능이다. 그래서
    타이핑이 끝난 뒤 되돌아가 서식을 적용하면 텍스트는 그대로 일반 글자로 남고
    빈 인용구 블록만 새로 생긴다. 따라서 소제목은 순서를 반대로 해서, 먼저
    _apply_quotation_line로 인용구(버티컬 라인) 블록을 연 뒤 그 안에 텍스트를
    타이핑한다. 서식 적용 직후 곧바로 타이핑하면 에디터가 인용구 컴포넌트를 아직
    완전히 생성하지 못한 상태라 글자가 일부 누락되는 현상이 있어(사용자 보고),
    타이핑 전에 HEADING_FORMAT_APPLY_DELAY_MS만큼 대기한다.

    .se-section-text locator를 한 번만 잡아 루프 내내 재사용하지 않고 매번
    _current_text_section(frame)으로 새로 조회한다 — 이미지를 삽입하면 네이버
    에디터가 그 아래에 새 .se-section-text 블록을 추가해 문서 안에 이 클래스를 가진
    요소가 2개 이상 존재하게 되고, 캐시해둔 locator로 이후 type()/press()를 호출하면
    "strict mode violation: resolved to 2 elements"로 죽는다(실측 확인).
    """
    _current_text_section(frame).click()

    local_image_paths = [img.path for img in parsed.images if img.is_local]

    # 링크/하이라이트 둘 다 지연 처리한다: 실제 서식 적용 없이 순수 텍스트로 먼저 다
    # 타이핑하고, (텍스트, 등장_순번, ...) 목록만 모아뒀다가 본문 타이핑이 전부 끝난
    # 뒤 한 번에 적용한다. 등장_순번은 running_text(지금까지 .se-section-text에
    # 타이핑된 순수 텍스트를 이어붙인 문자열)에서 대상 텍스트가 몇 번째로 등장하는지
    # 세서 구한다 — 나중에 _FIND_TEXT_RECT_JS가 실제 DOM을 같은 "등장 순서" 기준으로
    # 훑으므로 인덱스가 그대로 대응된다. 소제목(인용구) 텍스트는 .se-section-text가
    # 아닌 별도 컴포넌트에 들어가 검색 대상이 아니므로 running_text에 포함하지 않는다.
    link_requests: list[tuple[str, int, str]] = []
    highlight_requests: list[tuple[str, int]] = []
    running_text = ""

    for line in parsed.body_lines:
        stripped = line.strip()
        if stripped == "":
            continue

        matched_path = next(
            (path for path in local_image_paths if path in line), None
        )
        if matched_path:
            candidate = Path(matched_path)
            resolved_path = str(candidate if candidate.is_absolute() else (base_dir / candidate).resolve())
            upload_image(frame, resolved_path)
        elif line.startswith(HEADING_PREFIX):
            _current_text_section(frame).press("Enter")
            heading_text = HIGHLIGHT_MARKER_PATTERN.sub(r"\1", line[len(HEADING_PREFIX) :])
            # 소제목에 마크다운 링크가 섞여 있어도(정상적인 사용은 아니지만) 대괄호/
            # URL 문법이 그대로 노출되지 않도록 앵커 텍스트만 남긴다. 소제목은 링크
            # 서식(밑줄 등)까지는 적용하지 않는다 — 인용구 컴포넌트라 하이라이트/링크가
            # 훑는 .se-section-text 검색 대상이 아니라 지연 적용 방식 자체를 쓸 수 없다.
            heading_text = INLINE_MD_LINK_FULL_PATTERN.sub(r"\1", heading_text)
            _apply_quotation_line(frame)
            frame.owner.page.wait_for_timeout(HEADING_FORMAT_APPLY_DELAY_MS)
            _current_text_section(frame).type(heading_text, delay=TYPE_DELAY_MS)
            _exit_quotation_block(frame)
            frame.owner.page.wait_for_timeout(HEADING_SAFETY_DELAY_MS)
            continue
        else:
            # 링크가 있으면 이 줄은 여러 문단(링크 앞 문장/링크 카드/링크 뒤 문장)으로
            # 쪼개진다 — 링크가 없으면 세그먼트 1개(전체 줄)뿐이라 기존과 동일하게
            # 동작한다. 세그먼트마다 Enter를 직접 눌러 문단을 나누므로, 루프 끝의
            # 공통 Enter는 건너뛴다(continue).
            for segment in _split_line_by_links(line):
                if segment[0] == "text":
                    content = segment[1]
                    if content.strip() == "":
                        continue
                    clean_text, running_text = _consume_highlight_markers(content, running_text, highlight_requests)
                    _current_text_section(frame).type(clean_text, delay=TYPE_DELAY_MS)
                else:
                    _, anchor, url = segment
                    _current_text_section(frame).type(anchor, delay=TYPE_DELAY_MS)
                    link_requests.append((anchor, running_text.count(anchor), url))
                    running_text += anchor
                _current_text_section(frame).press("Enter")
            continue
        _current_text_section(frame).press("Enter")

    # 링크는 뒤(occurrence_index가 큰 것)에서부터 앞으로 적용한다 — 하이라이트와
    # 달리 링크는 적용에 성공하면 플레이스홀더 문단 전체가 카드 컴포넌트로 완전히
    # "대체"돼 텍스트 자체가 문서에서 사라진다(실측 확인). 앞에서부터 적용하면 첫
    # 링크가 사라지는 순간 그 뒤 플레이스홀더들의 실제 등장 순번이 한 칸씩 당겨져서
    # 타이핑 시점에 계산해둔 occurrence_index와 어긋나 못 찾는 문제가 있었다. 뒤에서
    # 부터 지우면 아직 처리 안 한 앞쪽 occurrence_index는 영향을 받지 않는다.
    for anchor_text, occurrence_index, url in reversed(link_requests):
        _apply_link(frame, anchor_text, occurrence_index, url)

    for phrase, occurrence_index in highlight_requests:
        _apply_highlight(frame, phrase, occurrence_index)


def _fallback_link_label(url: str) -> str:
    """앵커 텍스트가 없는 단독 URL에 쓸 표시용 라벨을 만든다.

    "[링크 클릭]" 같은 정체불명 플레이스홀더 대신, 도메인을 넣어 "무엇으로 이어지는
    링크인지" 짐작 가능한 텍스트로 남긴다(카드 변환 성공 시엔 어차피 안 보이지만,
    실패 시 화면에 남는 텍스트라 사용자 요청으로 이렇게 바꿨다). 도메인 파싱이
    실패하는 극단적인 경우에만 LINK_PLACEHOLDER로 폴백한다.
    """
    domain = urlparse(url).netloc
    if not domain:
        return LINK_PLACEHOLDER
    return LINK_FALLBACK_LABEL_TEMPLATE.format(domain=domain)


def _split_line_by_links(line: str) -> list[tuple[str, str] | tuple[str, str, str]]:
    """줄을 링크 앞뒤 일반 텍스트와 링크로 분리한다.

    각 항목은 ("text", 문단으로 타이핑할 내용) 또는 ("link", 앵커_텍스트, url)
    튜플이다. 링크가 하나도 없으면 [("text", line)] 하나만 돌려준다(기존 동작과
    동일). 마크다운 링크(`[텍스트](url)`)는 그 앵커 텍스트를 그대로 쓰고, 앵커
    텍스트가 없는 단독 URL은 _fallback_link_label로 도메인 기반 라벨을 만든다 —
    카드 변환에 성공하면 어느 쪽이든 최종 결과에는 안 보이지만, 네트워크 문제 등으로
    실패했을 때(_apply_link 참조) 화면에 남는 텍스트이므로 사용자가 알아볼 수 있는
    표현을 쓴다. 단독 URL 뒤에 흔히 붙는 문장부호(_TRAILING_URL_PUNCTUATION)는
    URL에서 제외하고 뒤 문장에 그대로 남긴다.
    """
    segments: list[tuple[str, str] | tuple[str, str, str]] = []
    pos = 0
    for match in _LINE_LINK_PATTERN.finditer(line):
        raw = match.group(0)
        start, end = match.start(), match.end()
        md_match = INLINE_MD_LINK_FULL_PATTERN.fullmatch(raw)
        if md_match:
            anchor, url = md_match.group(1), md_match.group(2)
        else:
            trimmed = raw.rstrip(_TRAILING_URL_PUNCTUATION)
            if not trimmed:
                continue
            end = start + len(trimmed)
            url = trimmed
            anchor = _fallback_link_label(url)

        before = line[pos:start]
        if before:
            segments.append(("text", before))
        segments.append(("link", anchor, url))
        pos = end

    tail = line[pos:]
    if tail:
        segments.append(("text", tail))
    if not segments:
        segments.append(("text", ""))
    return segments


def _consume_highlight_markers(
    line: str, running_text: str, highlight_requests: list[tuple[str, int]]
) -> tuple[str, str]:
    """줄 안의 `**문구**`를 모두 벗겨 순수 텍스트로 만들고, HIGHLIGHT_MAX_COUNT개까지만
    (phrase, occurrence_index)를 highlight_requests에 기록한다.

    occurrence_index는 running_text(지금까지 타이핑된 텍스트를 이어붙인 문자열,
    이 줄에서 이미 처리한 앞부분 포함)에서 phrase가 몇 번 등장했는지를 셈해서 구한다
    — _FIND_TEXT_RECT_JS가 실제 DOM을 같은 "등장 순서" 기준으로 찾으므로 이
    인덱스가 그대로 대응된다.
    """
    parts: list[str] = []
    last_end = 0
    for match in HIGHLIGHT_MARKER_PATTERN.finditer(line):
        before = line[last_end : match.start()]
        phrase = match.group(1)
        parts.append(before)
        parts.append(phrase)
        running_text += before
        if len(highlight_requests) < HIGHLIGHT_MAX_COUNT:
            highlight_requests.append((phrase, running_text.count(phrase)))
        running_text += phrase
        last_end = match.end()
    tail = line[last_end:]
    parts.append(tail)
    running_text += tail
    return "".join(parts), running_text


def _select_text_occurrence(frame: Any, phrase: str, occurrence_index: int) -> bool:
    """.se-section-text 안에서 phrase의 occurrence_index번째(0-based) 등장 위치를
    실제 마우스 드래그로 선택한다. 성공하면 True, 대상을 못 찾으면 False.

    하이라이트/링크 적용이 공유하는 선택 로직이다. JS(_FIND_TEXT_RECT_JS)로는 대상
    문구의 화면 좌표만 얻고, 실제 선택은 Playwright의 실제 마우스 드래그로 만든다 —
    Range/Selection을 JS로만 설정하면 네이버 에디터의 선택 동기화 로직이 갱신되지
    않아 서식 버튼이 아무 효과가 없었다(라이브 디버깅으로 확인).
    """
    page = frame.owner.page
    rect = frame.locator("body").evaluate(
        f"(el, [phrase, idx]) => ({_FIND_TEXT_RECT_JS})(phrase, idx)",
        [phrase, occurrence_index],
    )
    if rect is None:
        return False

    mainframe_box = page.locator("#mainFrame").bounding_box()
    if mainframe_box is None:
        return False

    start_x = mainframe_box["x"] + rect["startX"]
    start_y = mainframe_box["y"] + rect["startY"]
    end_x = mainframe_box["x"] + rect["endX"]
    end_y = mainframe_box["y"] + rect["endY"]
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move((start_x + end_x) / 2, start_y, steps=3)
    page.mouse.move(end_x, end_y, steps=3)
    page.mouse.up()
    return True


def _apply_highlight(frame: Any, phrase: str, occurrence_index: int) -> None:
    """본문에서 phrase의 occurrence_index번째(0-based) 등장 위치에 배경색(노란색)을 적용한다.

    input_body가 본문 전체 타이핑을 다 끝낸 뒤에만 호출한다(이유는 input_body의
    docstring 참조 — "다음 입력 서식 오염" 문제를 피하기 위함. 실측으로도 확인했다:
    Shift+화살표로 선택 후 배경색을 적용하고 커서를 이동해 이어서 타이핑하면 그
    다음 텍스트까지 배경색을 물려받았다).

    버튼 클릭에 force=True를 쓴다 — 라이브 디버깅으로 확인한 사실: 드래그로 만든
    네이티브 선택(window.getSelection())이 아주 짧은 시간 안에(수백 ms 이내) 저절로
    collapse된다(원인 불명 — 네이버 에디터가 자체 모델로 동기화한 뒤 네이티브
    선택을 정리하는 것으로 추정). Playwright의 기본 click()은 액션 전에 요소가
    "안정적"인지 등을 여러 번 폴링하며 기다리는데, 그 대기만으로도 선택이 collapse될
    시간을 벌어줘서 버튼을 눌러도 팝업이 안 열리는 경우가 실측 확인됐다. force=True로
    그 대기를 건너뛰고 최대한 빨리 클릭해야 선택이 살아있는 상태에서 클릭이 먹힌다.
    """
    try:
        if not _select_text_occurrence(frame, phrase, occurrence_index):
            print(f"경고: 하이라이트 대상 텍스트를 찾지 못함({phrase!r}), 강조 없이 넘어갑니다.", file=sys.stderr)
            return
        frame.locator(BACKGROUND_COLOR_BUTTON_SELECTOR).click(timeout=HIGHLIGHT_TIMEOUT_MS, force=True)
        frame.locator(HIGHLIGHT_COLOR_SWATCH_SELECTOR).first.click(timeout=HIGHLIGHT_TIMEOUT_MS)
    except Exception:
        print(f"경고: 하이라이트 적용 실패({phrase!r}), 강조 없이 일반 텍스트로 남습니다.", file=sys.stderr)


def _current_text_section(frame: Any) -> Any:
    """현재 타이핑 대상인 .se-section-text를 매번 새로 조회해서 반환한다.

    이미지 삽입 등으로 .se-section-text가 여러 개 생겼을 때 항상 가장 마지막(최신,
    보통 커서가 가 있는) 섹션을 골라야 하므로 .last를 쓴다.
    """
    return frame.locator(".se-section-text").last


def _apply_quotation_line(frame: Any) -> None:
    """상단 인용구 드롭다운을 열어 "인용구 2"(버티컬 라인형, quotation_line)를 선택한다.

    실측 DOM(사용자 제공)에 정확히 맞춘 선택자를 쓴다: 트리거 버튼(QUOTATION_TRIGGER_SELECTOR)을
    클릭해 옵션 목록을 연 뒤, data-value="quotation_line"인 option 버튼을 클릭한다.
    """
    # no_wait_after=True: 클릭 자체는 로그상 즉시 성공하는데도 Playwright가 클릭
    # 이후 "waiting for scheduled navigations to finish" 단계에서 SUBHEADING_STYLE_TIMEOUT_MS를
    # 다 써버리고 타임아웃하는 현상이 실측 확인됐다(네이버 에디터 SPA가 클릭 직후
    # 실제 페이지 이동 없는 내부 라우팅/히스토리 이벤트를 발생시켜 Playwright가
    # 네비게이션으로 오인하는 것으로 추정). 드롭다운 트리거/옵션 클릭은 페이지 이동을
    # 유발하지 않으므로 이 대기를 건너뛴다.
    frame.locator(QUOTATION_TRIGGER_SELECTOR).first.click(
        timeout=SUBHEADING_STYLE_TIMEOUT_MS, no_wait_after=True
    )
    frame.locator(QUOTATION_LINE_OPTION_SELECTOR).click(
        timeout=SUBHEADING_STYLE_TIMEOUT_MS, no_wait_after=True
    )


def _exit_quotation_block(frame: Any) -> None:
    """방금 텍스트를 입력한 인용구 컴포넌트를 벗어나 일반 문단으로 돌아간다.

    인용구 컴포넌트의 bounding box 바로 아래(마진 QUOTATION_EXIT_CLICK_MARGIN_PX)의
    빈 영역을 마우스로 클릭한다 — 라이브 디버깅으로 확인한 유일하게 동작하는 방법
    (Enter 키로는 컴포넌트를 못 벗어난다). 클릭한 지점에 새 "본문" 문단이 생기고,
    이후 타이핑하는 텍스트는 인용구 서식을 물려받지 않는다.
    """
    box = frame.locator(QUOTATION_COMPONENT_SELECTOR).last.bounding_box()
    if box is None:
        return
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] + QUOTATION_EXIT_CLICK_MARGIN_PX
    frame.owner.page.mouse.click(x, y)


def _apply_link(frame: Any, anchor_text: str, occurrence_index: int, url: str) -> None:
    """anchor_text(마크다운 링크의 앵커 텍스트, 또는 단독 URL이면 _fallback_link_label이
    만든 도메인 기반 라벨 — _split_line_by_links가 링크 문단에 심어둔 텍스트)의
    occurrence_index번째(0-based) 등장 위치를 url로 연결된 미리보기 카드로 바꾼다.
    같은 앵커 텍스트가 문서에 여러 번 나올 수 있으므로(같은 문구를 두 번 링크하거나,
    같은 도메인의 단독 URL이 여러 번 나오는 경우) _select_text_occurrence의 등장
    순번 기반 검색으로 정확한 문단을 고른다.

    input_body가 본문 전체 타이핑을 다 끝낸 뒤에만 이 함수를 호출한다(이유는
    input_body의 docstring 참조 — 서식 적용 직후 "다음 입력 서식"이 뒤에 오는
    텍스트에 번지는 문제를 피하기 위함). 실측 DOM 기준 흐름: 링크 버튼(oglink) 클릭
    -> 팝업의 URL 입력창에 url 입력 -> 돋보기(검색) 버튼 클릭해 OG 미리보기 로드 ->
    확인 버튼(미리보기가 로드돼야 활성화) 클릭.

    링크 버튼 클릭에 force=True를 쓴다 — _apply_highlight의 force=True와 같은 이유
    (라이브 디버깅으로 확인: 드래그로 만든 네이티브 선택이 수백 ms 안에 저절로
    collapse돼서, Playwright 기본 click()의 액션 전 대기 시간만으로도 팝업이 안 열리는
    경우가 실측 확인됨). 링크 버튼은 문서 상단 고정 툴바에 있어 마우스 이동 거리가
    더 길고 그만큼 collapse 위험도 커서 특히 중요하다.

    실패 시(예: url이 OG 메타데이터를 못 불러오는 경우 — 실측 확인, "링크 정보를
    불러오는 데 실패했습니다" 토스트가 뜨고 확인 버튼이 계속 비활성 상태로 남음)
    돋보기(검색)를 최대 LINK_SEARCH_RETRY_COUNT번까지 다시 눌러 재시도한다 —
    일시적인 네트워크 지연/오류일 수 있어서다. 그래도 계속 실패하면 팝업을
    닫아준다 — 안 닫으면 팝업이 화면을 덮고 있어서 다음 링크의
    _select_text_occurrence(마우스 드래그로 선택)가 그 팝업을 건드리게 되어 이후
    모든 링크 적용이 연쇄적으로 실패한다(실측 확인).
    """
    try:
        if not _select_text_occurrence(frame, anchor_text, occurrence_index):
            print(f"경고: 링크 앵커 텍스트를 찾지 못함({anchor_text!r}), 링크 없이 넘어갑니다.", file=sys.stderr)
            return
        frame.locator(LINK_BUTTON_SELECTOR).click(timeout=LINK_STYLE_TIMEOUT_MS, force=True)
        frame.locator(LINK_URL_INPUT_SELECTOR).fill(url)

        last_error: Exception | None = None
        for attempt in range(1, LINK_SEARCH_RETRY_COUNT + 1):
            try:
                frame.locator(LINK_SEARCH_BUTTON_SELECTOR).click(timeout=LINK_STYLE_TIMEOUT_MS)
                # 확인 버튼은 검색이 OG 미리보기를 불러와 disabled를 풀어줄 때까지
                # 비활성 상태다 — Locator.click()은 기본적으로 "enabled" 상태를
                # 기다렸다가 누르므로 별도 폴링 없이 타임아웃만 넉넉히 주면 된다
                # (네트워크 요청이라 버튼 클릭보다 오래 걸릴 수 있음).
                frame.locator(LINK_CONFIRM_BUTTON_SELECTOR).click(timeout=LINK_STYLE_TIMEOUT_MS * 2)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                if attempt < LINK_SEARCH_RETRY_COUNT:
                    print(
                        f"경고: 링크 미리보기 로드 실패({url!r}), 재시도 {attempt}/{LINK_SEARCH_RETRY_COUNT}",
                        file=sys.stderr,
                    )
        if last_error is not None:
            raise last_error
    except Exception:
        print(f"경고: 링크 적용 실패({url!r}), 일반 텍스트로 남습니다.", file=sys.stderr)
        try:
            frame.locator(LINK_POPUP_CLOSE_BUTTON_SELECTOR).click(timeout=POPUP_CHECK_TIMEOUT_MS)
        except Exception:
            pass


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
