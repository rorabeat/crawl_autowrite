# 구현 기능: F002, F009 보조 진입점
"""로그인만 미리 수행해두는 보조 CLI.

main.py(--md 필수, F001/F013)의 인자/동작은 그대로 두고(변경 금지 대상 — 기존 CLI 계약을
유지해야 다중 계정 발행 등 이미 이 계약에 맞춰 만들어진 오케스트레이터 쪽 코드가 깨지지
않는다), 로그인 관련 모듈(config.py/naver_login.py)만 그대로 재사용하는 별도 진입점이다.

로그인 완료 후 계정별 크롬 프로필을 저장하고, 발행 시 같은 프로필을 다시 연다.

"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    import ctypes
    # Older GUI builds may launch this script with an incomplete environment.
    # Winsock needs the Windows directory even before Playwright is imported.
    if not os.environ.get("SystemRoot"):
        windows_dir = ctypes.create_unicode_buffer(32768)
        if not ctypes.windll.kernel32.GetWindowsDirectoryW(windows_dir, len(windows_dir)):
            raise ctypes.WinError()
        os.environ["SystemRoot"] = windows_dir.value
    if not ctypes.windll.kernel32.SetDllDirectoryW(None):
        raise ctypes.WinError()

from playwright.sync_api import sync_playwright

import naver_login


def build_parser() -> argparse.ArgumentParser:
    """--blog-id/--headless/--session-file 인자를 정의한다(main.py와 동일한 이름/의미)."""
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
        help="헤드리스 모드로 실행 (기본값: headed, 수동 로그인은 headless에서 불가능)",
    )
    parser.add_argument("--interactive", action="store_true", help="로그인 창을 직접 연다")
    parser.add_argument("--logout", action="store_true", help="현재 발행용 프로필에서 로그아웃한다")
    parser.add_argument(
        "--session-file",
        default=str(Path(__file__).resolve().parent / ".naver_session" / "storage_state.json"),
        help="세션 상태 파일 경로 (기본값: 실행 위치와 무관하게 이 스크립트 기준 고정 경로)",
    )
    return parser


def main() -> int:
    naver_login.start_cancel_watchdog()
    args = build_parser().parse_args()
    from login_session.session import chrome_session, require_chrome_auth, logout_chrome_auth
    profile = Path(os.environ.get("NAVER_CHROME_PROFILE") or
                   Path(__file__).resolve().parent.parent / "data/chrome-publish-profile")
    with sync_playwright() as playwright:
        try:
            with chrome_session(playwright, profile) as context:
                if args.logout:
                    logout_chrome_auth(context)
                    print("진행 상황: 로그아웃 완료", flush=True)
                    return 0
                require_chrome_auth(context, interactive=args.interactive and not args.headless)
                print("진행 상황: 로그인 성공", flush=True)
                return 0
        except (naver_login.LoginFailedError, naver_login.SessionVerificationError) as exc:
            print(f"에러: {exc}", file=sys.stderr)
            return naver_login.result_code(exc)



if __name__ == "__main__":
    try:
        rc = main()
    except KeyboardInterrupt:
        rc = naver_login.RC_CANCELED
    except (naver_login.LoginFailedError, naver_login.AuthChallengeTimeoutError, naver_login.SessionVerificationError) as exc:
        print(f"에러: {exc}", file=sys.stderr)
        rc = naver_login.result_code(exc)
    naver_login.acknowledge_cancel()
    sys.exit(rc)
