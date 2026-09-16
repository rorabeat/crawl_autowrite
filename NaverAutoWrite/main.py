# 구현 기능: F001, F013
"""CLI 진입점 골격.

인자 파싱, 전체 실행 순서 조율(config -> md_parser -> naver_login -> editor ->
image_uploader), 진행 상황 로그 출력을 담당한다. 각 모듈 호출부는 이후
Task 010(메인 오케스트레이션)에서 실제로 연결한다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

import editor
import naver_login
from config import load_config
from md_parser import parse_markdown


def build_parser() -> argparse.ArgumentParser:
    """--md/--blog-id/--headless/--session-file 인자를 정의한다 (F001)."""
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="md 초안을 네이버 블로그에 자동으로 발행하는 CLI",
    )
    parser.add_argument("--md", required=True, help="입력 마크다운 파일 경로")
    parser.add_argument(
        "--blog-id", default=None, help="대상 블로그 ID (기본값: .env의 NAVER_BLOG_ID)"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="헤드리스 모드로 실행 (기본값: headed, CAPTCHA/2FA 수동 대응을 위함)",
    )
    parser.add_argument(
        "--session-file",
        default=str(Path(__file__).resolve().parent / ".naver_session" / "storage_state.json"),
        help="세션 상태 파일 경로 (기본값: 실행 위치와 무관하게 이 스크립트 기준 고정 경로)",
    )
    parser.add_argument(
        "--login-mode",
        choices=["auto", "manual"],
        default="auto",
        help=(
            "auto: .env 계정으로 클립보드 자동 입력 로그인(챌린지 발생 시에만 수동 대기, 기본값). "
            "manual: 자동 입력 없이 처음부터 사람이 직접 아이디/비밀번호와 인증을 완료 "
            "(자동 입력이 이상 로그인으로 자주 탐지되는 계정에서 사용)."
        ),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    config = load_config(args.blog_id, args.session_file)
    parsed = parse_markdown(args.md)
    print(f"진행 상황: md 파싱 완료 (title={parsed.title!r})")

    with sync_playwright() as playwright:
        context, reused = naver_login.create_browser_context(
            playwright, args.headless, config.session_file
        )
        # context는 CDP로 붙은 공유 크롬의 컨텍스트다 — 여기서 닫으면 다음 실행이
        # 재사용할 컨텍스트 자체가 없어지므로, 이 실행에서 연 page만 닫는다(크롬
        # 프로세스와 컨텍스트는 계속 떠 있는 채로 둔다).
        page = None
        try:
            if not reused:
                print(f"진행 상황: 신규 로그인 시작 (login-mode={args.login_mode})")
                context = naver_login.login_with_recovery(
                    playwright, context, config, args.login_mode, args.headless
                )
                print("진행 상황: 로그인 성공")

            page = context.new_page()
            frame = editor.open_editor(page, config.blog_id)
            print("진행 상황: 에디터 진입 완료")

            editor.input_title(frame, parsed.title)
            editor.input_body(frame, parsed, base_dir=Path(args.md).resolve().parent)
            print("진행 상황: 제목/본문 입력 완료")

            editor.save_draft(page)
            print("진행 상황: 임시저장 완료 (바로 발행하지 않음 — 네이버 블로그에서 직접 확인 후 발행하세요)")
            return 0
        except (
            naver_login.LoginFailedError,
            naver_login.AuthChallengeTimeoutError,
            editor.EditorError,
        ) as exc:
            print(f"에러: {exc}", file=sys.stderr)
            return 1
        finally:
            if page is not None:
                page.close()


if __name__ == "__main__":
    sys.exit(main())
