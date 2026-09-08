# 구현 기능: F002, F009, F011
"""네이버 로그인 자동화 모듈 골격.

크롬을 "매 실행마다 새로 띄웠다 끄는" 대신, 백그라운드에 계속 떠 있는 크롬 프로세스
하나에 CDP(Chrome DevTools Protocol)로 붙었다 떼는 방식을 쓴다. 없으면 완전히
분리된(detached, main.py 프로세스가 끝나도 같이 죽지 않는) 프로세스로 새로 띄운다.
필요 시 클립보드 붙여넣기(pyperclip) 방식으로 #id/#pw에 로그인 정보를 입력하며,
CAPTCHA/2단계 인증/신규기기 인증 등 자동 처리 불가능한 챌린지를 감지하면 수동
개입을 대기한다.

이렇게 하는 이유: (1) storage_state(쿠키만 저장) 재사용 방식은 실행할 때마다 완전히
새 브라우저 인스턴스를 띄우기 때문에 캐시/기기 지문 등이 매번 초기화되어 네이버가
"새 기기 로그인"으로 판단해 재인증을 자주 요구했다. (2) launch_persistent_context로
프로필 디렉터리만 재사용해도 매 실행마다 크롬 프로세스 자체는 새로 뜨고 끝나면
종료된다(=로그인 상태 유지에는 충분하지만 "크롬을 계속 켜 둔 채 재사용"은 아니다).
CDP로 이미 떠 있는 크롬에 붙는 방식은 크롬 프로세스 자체를 여러 발행 작업 사이에
그대로 유지해 재인증 트리거를 더 줄이고, 매번 브라우저를 새로 띄우는 대기 시간도
없앤다.
"""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pyperclip

from config import Config

DEFAULT_TIMEOUT_MS = 30_000
CHALLENGE_TIMEOUT_MS = 0  # 0 = Playwright 타임아웃 없음(무제한 대기)
NIDLOGIN_URL = "https://nid.naver.com/nidlogin.login"

# 크롬 프로필 디렉터리는 로그인 성공 여부와 무관하게 브라우저를 한 번만 열어도
# 파일이 생기므로, 폴더에 파일이 있는지만으로는 "재사용 가능(=이미 로그인됨)"을
# 판단할 수 없다. 로그인이 실제로 성공했을 때만 이 마커 파일을 만들어 다음 실행이
# login()을 건너뛰어도 되는지 판단하는 근거로 쓴다.
_LOGIN_MARKER_NAME = ".login_ok"

# 오케스트레이터가 이 프로젝트에서 쓰는 프로필은 하나뿐이므로 CDP 포트도 고정값 하나로
# 충분하다(여러 프로필을 동시에 쓰게 되면 프로필별로 포트를 분리해야 한다).
_CDP_PORT = 9333
_CDP_READY_TIMEOUT_S = 20

# connect_over_cdp의 playwright 기본 타임아웃(180초)은 "크롬을 죽여야 하는 상황"을
# 그대로 180초 동안 블로킹시켜 버린다(실측: 아래 _connect_over_cdp_with_retry 참조).
# 정상적인 로컬 CDP 접속은 수 초 내로 끝나므로, 좀비 상태를 빠르게 판단하기 위해 훨씬
# 짧게 잡는다.
_CDP_CONNECT_TIMEOUT_MS = 15_000


def _cdp_url() -> str:
    return f"http://127.0.0.1:{_CDP_PORT}"


def _is_cdp_ready() -> bool:
    try:
        urllib.request.urlopen(f"{_cdp_url()}/json/version", timeout=1)
        return True
    except (urllib.error.URLError, OSError):
        return False


def _kill_chrome_process(port: int = _CDP_PORT) -> None:
    """CDP 포트를 점유한 크롬 프로세스를 강제 종료한다(좀비 CDP 타깃 복구용).

    "크롬을 계속 살려두고 재사용"하는 설계상, 이전 실행이 서브프로세스 강제종료(타임아웃/
    취소)로 중간에 죽으면 방금 만들다 만 빈 페이지 타깃(Page.enable 등에 응답하지 않는
    렌더러)이 크롬 안에 그대로 남을 수 있다. 이 좀비 타깃 하나 때문에 이후의 모든
    connect_over_cdp 호출이 "ws connected"까지는 찍히고도 타임아웃 나버리므로(playwright가
    기존 타깃 초기화 완료까지 기다린 뒤에야 반환), 연결 자체가 실패하면 크롬 프로세스를
    통째로 재시작하는 것이 가장 확실한 복구 방법이다. netstat/taskkill은 Windows
    전용이며(이 프로젝트는 Windows 데스크톱 앱), 이미 종료돼 있으면 조용히 넘어간다.
    """
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        print("_kill_chrome_process: netstat 실행 실패, 건너뜀", file=sys.stderr)
        return

    pids: set[str] = set()
    needle = f":{port}"
    for line in result.stdout.splitlines():
        if needle in line and "LISTENING" in line:
            parts = line.split()
            if parts:
                pids.add(parts[-1])

    for pid in pids:
        try:
            subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, timeout=10, check=False)
            print(f"_kill_chrome_process: 응답 없는 크롬(PID {pid})을 종료함", file=sys.stderr)
        except (OSError, subprocess.SubprocessError):
            print(f"_kill_chrome_process: PID {pid} 종료 실패", file=sys.stderr)

    deadline = time.monotonic() + _CDP_READY_TIMEOUT_S
    while time.monotonic() < deadline and _is_cdp_ready():
        time.sleep(0.3)


def _connect_over_cdp_with_retry(playwright: Any, profile_dir: Path, headless: bool) -> Any:
    """connect_over_cdp를 짧은 타임아웃으로 시도하고, 실패하면 크롬을 재기동해 한 번 더 시도한다.

    첫 시도가 실패하는 경우는 대부분 좀비 CDP 타깃(응답 없는 렌더러) 때문이다 — 크롬
    프로세스 자체를 죽이고 새로 띄우면(디스크의 user_data_dir은 그대로 유지되므로 로그인
    세션은 보존된다) 대부분 해소된다. 재시도까지 실패하면 예외를 그대로 올려 호출부
    (main.py/prelogin.py)가 기존과 동일하게 실패로 처리하게 한다.
    """
    try:
        return playwright.chromium.connect_over_cdp(_cdp_url(), timeout=_CDP_CONNECT_TIMEOUT_MS)
    except Exception as exc:
        print(
            f"CDP 연결 실패(좀비 크롬 프로세스로 추정) — 크롬을 재기동하고 한 번 더 시도합니다: {exc}",
            file=sys.stderr,
        )
        _kill_chrome_process()
        _launch_detached_chrome(playwright, profile_dir, headless)
        return playwright.chromium.connect_over_cdp(_cdp_url(), timeout=_CDP_CONNECT_TIMEOUT_MS)


def _launch_detached_chrome(playwright: Any, profile_dir: Path, headless: bool) -> None:
    """main.py 프로세스가 끝나도 같이 종료되지 않는 독립 크롬 프로세스를 새로 띄운다.

    playwright.chromium.launch()로 띄우면 Windows에서 Job Object로 부모(이 파이썬
    프로세스)에 종속되어 main.py가 끝나는 순간 크롬도 같이 죽는다(=재사용 불가). 이를
    피하려고 playwright가 내려받은 크롬 실행 파일 경로만 빌려 쓰고, 실행 자체는
    subprocess.Popen을 완전 분리 모드로 직접 호출한다.
    """
    chrome_path = playwright.chromium.executable_path
    args = [
        chrome_path,
        f"--remote-debugging-port={_CDP_PORT}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if headless:
        args.append("--headless=new")

    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    print(f"크롬을 백그라운드로 새로 실행합니다(재사용을 위해 종료하지 않음): {profile_dir}")
    subprocess.Popen(args, **popen_kwargs)

    deadline = time.monotonic() + _CDP_READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if _is_cdp_ready():
            return
        time.sleep(0.3)
    raise RuntimeError(f"크롬 CDP 포트가 {_CDP_READY_TIMEOUT_S}초 내에 준비되지 않았습니다: {_cdp_url()}")


class LoginFailedError(Exception):
    """아이디/비밀번호 오류 등으로 로그인이 실패했을 때 발생한다 (F011)."""


class AuthChallengeTimeoutError(Exception):
    """캡차/2FA/신규기기 인증을 타임아웃 내에 수동으로 완료하지 못했을 때 발생한다 (F009)."""


def create_browser_context(
    playwright: Any, headless: bool, session_file: str
) -> tuple[Any, bool]:
    """이미 떠 있는 크롬에 CDP로 붙거나(없으면 분리 프로세스로 새로 띄운 뒤 붙어서)
    공유 브라우저 컨텍스트를 반환한다 (F002 골격).

    session_file의 부모 디렉터리를 크롬 user_data_dir로 사용한다(이름은 과거
    storage_state.json 시절의 흔적이며, 실제로는 파일이 아니라 그 폴더 자체를
    프로필 디렉터리로 재사용한다).

    connect_over_cdp로 얻은 Browser/BrowserContext는 "빌려 쓰는" 연결이라
    context.close()를 호출해도 실제 크롬 프로세스는 종료되지 않는다(연결만 끊긴다)
    — 다만 그 컨텍스트 자체(및 그 안의 탭들)는 없어지므로, 호출부(main.py)는 이
    컨텍스트를 닫지 말고 그 안에 연 개별 page만 닫아야 다음 실행에서 같은 컨텍스트를
    이어 쓸 수 있다. 로그인 상태 자체는 크롬 프로필(디스크)에 계속 남으므로 컨텍스트가
    바뀌어도 로그인은 유지된다.

    반환값의 두 번째 항목(session_reused)으로 이전에 로그인을 완료한 적 있는
    프로필인지 알 수 있다. 이 값은 `.login_ok` 마커 파일이 아니라, 실제로
    nid.naver.com에 접속해봤을 때 로그인 페이지에서 벗어나는지(=리다이렉트되는지)로
    판정한다 — 마커는 "언젠가 로그인에 성공한 적 있다"만 보장할 뿐 지금도 로그인
    상태인지는 보장하지 않는다(세션 만료, 로그아웃, 프로필 손상 등으로 실제로는
    로그아웃 상태인데 마커만 남아 있으면 로그인 단계를 건너뛰어 발행이 실패한다).
    """
    profile_dir = Path(session_file).parent
    profile_dir.mkdir(parents=True, exist_ok=True)
    had_login_marker = (profile_dir / _LOGIN_MARKER_NAME).exists()

    print(
        f"크롬 프로필 재사용(로그인 완료 이력 있음): {profile_dir}"
        if had_login_marker
        else f"신규 로그인 필요: {profile_dir}"
    )

    if _is_cdp_ready():
        print(f"기존 크롬 프로세스에 연결합니다: {_cdp_url()}")
    else:
        _launch_detached_chrome(playwright, profile_dir, headless)

    browser = _connect_over_cdp_with_retry(playwright, profile_dir, headless)
    context = browser.contexts[0] if browser.contexts else browser.new_context(viewport={"width": 1280, "height": 800})

    # 크롬이 새로 뜨든(빈 새 탭) 기존 프로세스에 재연결하든, 사용자가 "크롬만 뜨고
    # 아무것도 안 보인다"고 느끼지 않도록 연결 직후 첫 탭을 곧바로 로그인 페이지로
    # 이동시킨다(사용자 요청). 이미 로그인된 세션이면 nid.naver.com이 알아서 네이버
    # 홈으로 리다이렉트하므로 로그인 상태를 눈으로 바로 확인할 수 있다.
    page = context.pages[0] if context.pages else context.new_page()
    page.bring_to_front()
    try:
        page.goto(NIDLOGIN_URL, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
    except Exception:
        pass

    # goto가 (드물게) 예외로 실패하면 page.url이 새 탭의 초기값("about:blank") 등으로
    # 남을 수 있는데, 이는 "nidlogin.login"을 포함하지 않으므로 자칫 로그인된 것으로
    # 잘못 판정될 수 있다 — 실제로 nid.naver.com 계열 URL에 도달했을 때만 신뢰한다.
    current_url = page.url
    reused = bool(current_url) and "naver.com" in current_url and _left_nidlogin(current_url)
    if had_login_marker and not reused:
        print(
            "로그인 완료 마커는 있지만 실제로는 로그인 상태가 아닙니다(세션 만료 등) — 재로그인을 진행합니다.",
            file=sys.stderr,
        )

    return context, reused


def wait_for_navigation(page: Any, url_pattern: str, timeout: int = DEFAULT_TIMEOUT_MS) -> None:
    """고정 sleep 대신 URL 전환을 명시적으로 대기한다."""
    page.wait_for_url(url_pattern, timeout=timeout)


def wait_for_selector(page_or_frame: Any, selector: str, timeout: int = DEFAULT_TIMEOUT_MS) -> Any:
    """고정 sleep 대신 셀렉터 등장을 명시적으로 대기한다."""
    return page_or_frame.wait_for_selector(selector, timeout=timeout)


_ERROR_KEYWORDS = ["일치하지 않습니다", "다시 확인"]


def _left_nidlogin(url: str) -> bool:
    return "nidlogin.login" not in url


def _click_login_button(page: Any) -> None:
    """반응형 이중 레이아웃(#loginBtn_row/#loginBtn_column) 중 실제로 보이는 로그인 버튼을 클릭한다."""
    try:
        page.get_by_role("button", name="로그인").click(timeout=DEFAULT_TIMEOUT_MS)
        return
    except Exception:
        pass

    for selector in ("#loginBtn_row", "#loginBtn_column"):
        locator = page.locator(selector)
        if locator.count() and locator.first.is_visible():
            locator.first.click()
            return

    page.locator("#loginBtn_row, #loginBtn_column").first.click(force=True)


def _mark_login_success(config: Config) -> None:
    Path(config.session_file).parent.mkdir(parents=True, exist_ok=True)
    (Path(config.session_file).parent / _LOGIN_MARKER_NAME).touch()


def login(context: Any, config: Config, login_mode: str = "auto") -> None:
    """nidlogin 페이지에서 로그인을 수행한다.

    login_mode="auto"(기본값): (F002) #id/#pw에 pyperclip 붙여넣기로 자격증명 자동
    입력 후 (F009) CAPTCHA/2FA/신규기기 인증 감지 시에만 수동 개입 대기.
    login_mode="manual": 자격증명 자동 입력을 아예 하지 않고, 로그인 페이지를 연 채로
    아이디/비밀번호 입력부터 인증까지 전부 사람이 직접 완료하도록 대기한다
    (클립보드 자동 붙여넣기가 "이상 로그인 시도"로 자주 탐지되는 계정을 위함).
    (F011) auto 모드에서만 아이디/비밀번호 오류를 판별해 LoginFailedError를 던진다.
    """
    # create_browser_context가 연결 직후 이미 첫 탭을 로그인 페이지로 이동시켜 두므로,
    # 그 탭을 그대로 재사용한다(탭이 중복으로 쌓이지 않도록).
    page = context.pages[0] if context.pages else context.new_page()
    try:
        page.bring_to_front()
        page.goto(NIDLOGIN_URL)
        wait_for_selector(page, "#id")

        if login_mode == "manual":
            print(
                "수동 로그인 모드: 자동 입력 없이 브라우저에서 아이디/비밀번호 입력부터 인증까지 직접 진행해주세요.",
                file=sys.stderr,
            )
            try:
                page.wait_for_url(_left_nidlogin, timeout=CHALLENGE_TIMEOUT_MS)
            except Exception as exc:
                raise AuthChallengeTimeoutError("수동 로그인을 제한 시간 내에 완료하지 못했습니다.") from exc
            _mark_login_success(config)
            return

        pyperclip.copy(config.naver_id)
        page.click("#id")
        page.keyboard.press("Control+V")

        pyperclip.copy(config.naver_pw)
        page.click("#pw")
        page.keyboard.press("Control+V")

        _click_login_button(page)

        try:
            page.wait_for_url(_left_nidlogin, timeout=DEFAULT_TIMEOUT_MS)
            _mark_login_success(config)
            return
        except Exception:
            pass

        page_text = page.content()

        if any(keyword in page_text for keyword in _ERROR_KEYWORDS):
            raise LoginFailedError("네이버 로그인 실패: 아이디 또는 비밀번호가 일치하지 않습니다.")

        print(
            "인증 챌린지(캡차/2FA/신규기기)로 추정되는 화면이 감지되었습니다. "
            "브라우저에서 직접 인증을 완료해주세요.",
            file=sys.stderr,
        )
        try:
            page.wait_for_url(_left_nidlogin, timeout=CHALLENGE_TIMEOUT_MS)
        except Exception as exc:
            raise AuthChallengeTimeoutError(
                "인증 챌린지를 제한 시간 내에 완료하지 못했습니다."
            ) from exc

        _mark_login_success(config)
    finally:
        # 공유 크롬 컨텍스트에 연 로그인 탭이므로, 다음 실행이 같은 컨텍스트를 이어
        # 쓸 때 탭이 계속 쌓이지 않도록 끝나면 반드시 닫는다.
        page.close()
