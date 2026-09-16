# 구현 기능: F002, F009 보조 진입점
"""로그인만 미리 수행해두는 보조 CLI.

main.py(--md 필수, F001/F013)의 인자/동작은 그대로 두고(변경 금지 대상 — 기존 CLI 계약을
유지해야 다중 계정 발행 등 이미 이 계약에 맞춰 만들어진 오케스트레이터 쪽 코드가 깨지지
않는다), 로그인 관련 모듈(config.py/naver_login.py)만 그대로 재사용하는 별도 진입점이다.

오케스트레이터(crawl_autowrite/pipeline.py)가 글쓰기 태스크 시작과 동시에 이 스크립트를
백그라운드로 먼저 실행해, 크롤링/AI 생성이 진행되는 동안 사용자가 미리 크롬에서 로그인을
마칠 수 있게 한다. main.py는 naver_login.create_browser_context가 같은 CDP 포트(9333)의
크롬 프로필에 다시 붙으므로, 여기서 로그인이 이미 끝나 있으면(.login_ok 마커) 발행 단계는
추가 대기 없이 바로 진행된다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

import naver_login
from config import load_config


def build_parser() -> argparse.ArgumentParser:
    """--blog-id/--headless/--session-file/--login-mode 인자를 정의한다(main.py와 동일한 이름/의미)."""
    parser = argparse.ArgumentParser(
        prog="prelogin.py",
        description="발행 전에 미리 네이버 로그인을 완료해두는 보조 CLI (main.py --md 없이 로그인만 수행)",
    )
    parser.add_argument(
        "--blog-id", default=None, help="대상 블로그 ID (기본값: .env의 NAVER_BLOG_ID)"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="헤드리스 모드로 실행 (기본값: headed — 수동 로그인은 headless로는 불가능)",
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
        help="main.py --login-mode와 동일 (auto: 클립보드 자동 입력, manual: 전부 수동)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(args.blog_id, args.session_file)

    with sync_playwright() as playwright:
        # context는 CDP로 붙은 공유 크롬의 컨텍스트다 — main.py와 마찬가지로 여기서
        # 컨텍스트 자체를 닫지 않는다(닫으면 다음 실행이 재사용할 컨텍스트가 사라짐).
        context, reused = naver_login.create_browser_context(
            playwright, args.headless, config.session_file
        )
        if reused:
            print("진행 상황: 이미 로그인된 세션을 재사용합니다 (추가 로그인 불필요).")
            return 0

        print(f"진행 상황: 신규 로그인 시작 (login-mode={args.login_mode})")
        try:
            naver_login.login_with_recovery(playwright, context, config, args.login_mode, args.headless)
        except (naver_login.LoginFailedError, naver_login.AuthChallengeTimeoutError) as exc:
            print(f"에러: {exc}", file=sys.stderr)
            return 1

        print("진행 상황: 로그인 성공")
        return 0


if __name__ == "__main__":
    sys.exit(main())
