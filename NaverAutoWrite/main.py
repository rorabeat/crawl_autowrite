# 구현 기능: F001, F013
"""CLI 진입점 골격.

인자 파싱, 전체 실행 순서 조율(config -> md_parser -> naver_login -> editor ->
image_uploader), 진행 상황 로그 출력을 담당한다. 각 모듈 호출부는 이후
Task 010(메인 오케스트레이션)에서 실제로 연결한다.
"""

from __future__ import annotations

import argparse
import os
import hashlib
import sys
from pathlib import Path

# A frozen GUI can pass its private DLL search directory to this external
# Python process. Reset it before importing asyncio/Playwright (Winsock).
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

import editor
import naver_login
from config import load_config
from md_parser import parse_markdown


def build_parser() -> argparse.ArgumentParser:
    """--md/--blog-id/--headless/--session-file 인자를 정의한다 (F001)."""
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="md 초안을 네이버 블로그에 임시저장하는 CLI",
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
    return parser


def main() -> int:
    naver_login.start_cancel_watchdog()
    args = build_parser().parse_args()

    draft = Path(args.md).resolve()
    journal = os.environ.get("NAVER_SAVE_JOURNAL") or str(
        draft.parent / ('.save-intent-' + hashlib.sha256(str(draft).encode('utf-8')).hexdigest() + '.json'))
    confirmation = os.environ.get("NAVER_SAVE_CONFIRMED")
    editor_opened = os.environ.get("NAVER_EDITOR_OPENED")
    if confirmation and Path(confirmation).is_file():
        print("진단: 이미 확인된 임시저장 기록이 있습니다.", flush=True)
        return 0
    if Path(journal).exists():
        print("진단: 이전 임시저장 결과 확인 대기 중 (저장 버튼 미실행)", flush=True)
        print("에러: 이전 임시저장 결과가 불명입니다. 네이버에서 결과를 확인하세요.", file=sys.stderr)
        return naver_login.RC_SAVE_UNKNOWN

    with editor.draft_stage("임시저장 설정 및 초안 읽기"):
        config = load_config(args.blog_id, args.session_file)
        parsed = parse_markdown(args.md)
    print(f"진단: 글 제목={' '.join(parsed.title.split())}", flush=True)

    with sync_playwright() as playwright:
        profile = os.environ.get("NAVER_CHROME_PROFILE")
        if not profile:
            print("진단: 발행용 Chrome 프로필 설정 누락", flush=True)
            print("에러: 발행용 Chrome에 직접 로그인한 뒤 다시 실행하세요.", file=sys.stderr)
            return 8
        from login_session.session import chrome_session, require_chrome_auth
        page = None
        browser = None
        saved = False

        def report_editor_state():
            if browser is not None and not browser.is_connected():
                print("진단: 오류 처리 시 Chrome 연결이 끊겨 있습니다.", flush=True)
            elif page is not None and page.is_closed():
                print("진단: 오류 처리 시 작성 탭이 이미 닫혀 있습니다.", flush=True)
            elif page is not None:
                print("진단: 작성 중인 에디터 탭을 유지합니다. 자동으로 임시저장하거나 재시도하지 않습니다.", flush=True)

        try:
            with chrome_session(playwright, Path(profile), restart_if_idle=True) as context:
                browser = context.browser
                with editor.draft_stage("본문 입력 전 인증 확인"):
                    require_chrome_auth(context)
                with editor.draft_stage("에디터 진입"):
                    page = context.new_page()
                    page.on("crash", lambda _: print("진단: 작성 탭의 렌더러가 비정상 종료됐습니다.", flush=True))
                    page.on("close", lambda _: print(
                        "진단: 임시저장 완료 후 앱이 작성 탭을 닫았습니다." if saved
                        else "진단: 작업 도중 작성 탭이 닫혔습니다.", flush=True))
                    frame = editor.open_editor(page, config.blog_id)
                    if editor_opened:
                        Path(editor_opened).touch()

                with editor.draft_stage("제목 입력"):
                    editor.input_title(frame, parsed.title)
                with editor.draft_stage("본문 입력"):
                    editor.input_body(frame, parsed, base_dir=draft.parent)

                with editor.draft_stage("임시저장 직전 인증 재확인"):
                    require_chrome_auth(context)
                with editor.draft_stage("중복 저장 방지 기록 생성"):
                    with open(journal, "x", encoding="utf-8") as marker:
                        marker.write("save outcome uncertain\n")
                        marker.flush()
                        os.fsync(marker.fileno())
                if not editor.save_draft(page):
                    print("에러: 임시저장 결과를 확인하지 못했습니다. 네이버 임시저장 목록을 확인하세요. 자동 재시도하지 않습니다.", file=sys.stderr)
                    report_editor_state()
                    return naver_login.RC_SAVE_UNKNOWN
                with editor.draft_stage("임시저장 결과 기록"):
                    if confirmation:
                        Path(journal).replace(confirmation)
                    else:
                        Path(journal).unlink()
                print("진행 상황: 임시저장 완료 — 최종 발행은 네이버에서 직접 진행하세요.", flush=True)
                saved = True
                return 0
        except (
            naver_login.LoginFailedError,
            naver_login.AuthChallengeTimeoutError,
            naver_login.SessionVerificationError,
            editor.EditorError,
        ) as exc:
            print(f"에러: {exc}", file=sys.stderr, flush=True)
            report_editor_state()
            return naver_login.RC_SAVE_UNKNOWN if Path(journal).exists() else naver_login.result_code(exc)
        except Exception as exc:
            print(f"에러: 네이버 임시저장 처리 중 예외 발생 ({type(exc).__name__})", file=sys.stderr, flush=True)
            report_editor_state()
            return naver_login.RC_SAVE_UNKNOWN if Path(journal).exists() else 1
        finally:
            if saved and page is not None and not page.is_closed():
                try:
                    page.close()
                except Exception as exc:
                    print(f"진단: 임시저장 완료 후 작성 탭 정리 오류 ({type(exc).__name__})", flush=True)


if __name__ == "__main__":
    try:
        rc = main()
    except KeyboardInterrupt:
        rc = naver_login.RC_CANCELED
    except (naver_login.LoginFailedError, naver_login.AuthChallengeTimeoutError, naver_login.SessionVerificationError) as exc:
        print(f"에러: {exc}", file=sys.stderr)
        rc = naver_login.result_code(exc)
    except Exception as exc:
        print(f"에러: 임시저장 CLI 처리 중 예외 발생 ({type(exc).__name__})", file=sys.stderr, flush=True)
        rc = 1
    naver_login.acknowledge_cancel()
    sys.exit(rc)
