# 구현 기능: F007
"""이미지 자동 업로드 모듈 골격.

본문 내 이미지 참조 라인을 감지하여 로컬 이미지 파일을 네이버 스마트에디터에
업로드한다. 실제 업로드 인터랙션 로직은 이후 Task(008)에서 구현한다.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from naver_login import DEFAULT_TIMEOUT_MS

UPLOAD_COMPLETE_SELECTOR = ".se-image, .se-module-image"
# 업로드 중에는 이미지 요소에 로딩 표시(스피너/플레이스홀더) 클래스가 붙는 경우가 흔하다.
# 정확한 클래스명이 실측 검증되지 않았으므로 흔한 후보를 모아 두고, 하나도 안 걸리면
# 조용히 넘어간다(있으면 사라질 때까지 기다리고, 없으면 그냥 다음 안전장치로 넘어감).
UPLOAD_LOADING_SELECTOR = ".se-image-loading, .se-image-resource-loading, [data-uploading='true']"
# 로딩 표시를 못 찾았을 때를 대비한 안전장치용 고정 대기 시간(ms). 이미지 업로드와 다음
# 작업(다음 줄 타이핑, 다음 이미지 업로드 등)이 겹쳐서 오류가 나는 문제가 실제로
# 보고되어(사용자 실측), 업로드 완료 확인 실패 시에도 최소한 이 시간만큼은 쉬고
# 넘어가도록 한다.
UPLOAD_SAFETY_DELAY_MS = 1_500


def upload_image(frame: Any, image_path: str) -> None:
    """로컬 이미지 파일을 스마트에디터 본문에 업로드한다 (F007).

    이미지 파일이 존재하지 않으면 에러 로그만 출력하고 예외 없이 반환해
    상위 editor.input_body의 본문 입력 루프가 계속 진행되도록 한다.
    """
    if not Path(image_path).exists():
        print(f"에러: 이미지 파일을 찾을 수 없습니다: {image_path}", file=sys.stderr)
        return

    # 네이버 스마트에디터의 사진 삽입 버튼은 아이콘이며 접근 가능한 이름이
    # "사진"으로 노출되므로 role 기반 셀렉터를 우선 사용한다.
    #
    # 이 버튼 클릭은 내부적으로 <input type=file>의 네이티브 클릭을 트리거해 실제
    # OS "열기" 대화상자가 뜬다(실측 확인 — 자동화가 이 대화상자에 막혀 그대로
    # 멈춰버리는 화면이 스크린샷으로 보고됨). frame.locator("input[type=file]")에
    # 곧바로 set_input_files를 호출하는 방식은 Playwright가 그 사이 타이밍에 따라
    # 네이티브 대화상자 등장을 못 막을 때가 있다. page.expect_file_chooser()로
    # 클릭 전에 미리 가로채는 표준 패턴을 써야 대화상자 자체가 뜨지 않는다.
    page = frame.owner.page
    with page.expect_file_chooser() as file_chooser_info:
        frame.get_by_role("button", name="사진").click()
    file_chooser_info.value.set_files(image_path)

    frame.locator(UPLOAD_COMPLETE_SELECTOR).first.wait_for(timeout=DEFAULT_TIMEOUT_MS)

    # 이미지 요소가 DOM에 나타난 시점은 "업로드 완료"가 아니라 "미리보기 삽입 시작"일
    # 수 있다(실제 파일 전송은 비동기로 뒤이어 진행됨) — 이미지 업로드가 끝나기 전에
    # 다음 작업(다음 줄 타이핑, 다음 이미지 업로드 등)이 겹쳐서 오류가 난 사례가 실제로
    # 있었다(사용자 보고). 로딩 표시가 있으면 사라질 때까지 기다리고(없으면 즉시
    # 통과), 그 뒤 고정 안전 대기를 한 번 더 둬서 업로드가 완전히 끝난 뒤에야 다음
    # 작업으로 넘어가게 한다.
    try:
        frame.locator(UPLOAD_LOADING_SELECTOR).first.wait_for(state="hidden", timeout=DEFAULT_TIMEOUT_MS)
    except Exception:
        pass
    page.wait_for_timeout(UPLOAD_SAFETY_DELAY_MS)
