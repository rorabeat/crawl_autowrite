"""Login using a persistent browser profile owned by this invocation."""
from __future__ import annotations

import sys
import time
import hashlib
import http.client
import json
import os
import threading
import _thread
import re
import shutil
import subprocess
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from config import Config

NIDLOGIN_URL = "https://nid.naver.com/nidlogin.login"
ACCOUNT_URL = "https://nid.naver.com/user2/help/myInfoV2"
DEFAULT_TIMEOUT_MS = 30_000
CHALLENGE_TIMEOUT_MS = 0
CHROME_PROFILE_IN_USE = 21
CHROME_PROCESS_NOTIFIED = 24
CHROME_OWNER_FILENAME = '.app-chrome-owner.json'
IDLE_CHROME_URLS = frozenset({'about:blank', 'chrome://newtab/', 'chrome://new-tab-page/'})


def start_cancel_watchdog():
    path = os.environ.get('NAVER_CANCEL_FILE')
    if not path:
        return
    def watch():
        while True:
            if Path(path).exists():
                _thread.interrupt_main()
                return
            time.sleep(0.1)
    threading.Thread(target=watch, daemon=True).start()


def acknowledge_cancel():
    ack = os.environ.get('NAVER_CANCEL_ACK')
    if ack:
        Path(ack).touch()


def _auth_cookie(context: Any) -> str | None:
    return next((c.get("value") for c in context.cookies("https://nid.naver.com")
                 if c.get("name") == "NID_AUT" and c.get("value")), None)


def _has_auth_cookie(context: Any) -> bool:
    return bool(_auth_cookie(context))


class LoginFailedError(Exception):
    pass


class AuthChallengeTimeoutError(Exception):
    pass


class SessionVerificationError(Exception):
    """Authentication or account ownership could not be verified."""


class ProfileLockError(SessionVerificationError):
    pass


class AccountMismatchError(SessionVerificationError):
    pass


class BrowserConnectionError(SessionVerificationError):
    pass


# Stable process result codes shared by both publisher entry points.
RC_OK, RC_FAILED, RC_UNAUTHENTICATED, RC_UNKNOWN, RC_ACCOUNT, RC_LOCKED, RC_CANCELED, RC_SAVE_UNKNOWN = range(8)


def result_code(exc):
    if isinstance(exc, KeyboardInterrupt):
        return RC_CANCELED
    if isinstance(exc, ProfileLockError):
        return RC_LOCKED
    if isinstance(exc, BrowserConnectionError):
        return 8
    if isinstance(exc, AccountMismatchError):
        return RC_ACCOUNT
    if isinstance(exc, LoginFailedError):
        return RC_UNAUTHENTICATED
    if isinstance(exc, SessionVerificationError):
        return RC_UNKNOWN
    return RC_FAILED


def _safe_diagnostic(exc, expected_id=''):
    detail = str(exc)
    if expected_id:
        detail = detail.replace(expected_id, '<계정>')
    detail = re.sub(r'https?://\S+', '<URL>', detail, flags=re.IGNORECASE)
    detail = re.sub(r'(?i)(cookie|token|password|authorization|nid_aut|nid_ses)\s*[:=]\s*\S+', r'\1=<REDACTED>', detail)
    return f'{type(exc).__name__}: {detail}'

class _ProfileLock:
    def __init__(self, profile: Path):
        profile.parent.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha256(os.path.normcase(str(profile.resolve())).encode()).hexdigest()
        self.file = open(profile.parent / ('.profile-lock-' + name), 'a+b')
        try:
            if os.fstat(self.file.fileno()).st_size == 0:
                self.file.write(b'0')
                self.file.flush()
            self.file.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise ProfileLockError('브라우저 프로필이 다른 작업에서 사용 중입니다.') from exc

    def close(self):
        self.file.close()


class _LockedContext:
    def __init__(self, context, lock):
        self._context, self._lock = context, lock

    def __getattr__(self, name):
        return getattr(self._context, name)

    def close(self):
        try:
            self._context.close()
        except BaseException:
            marker = os.environ.get('NAVER_FLUSH_UNKNOWN')
            if marker:
                Path(marker).touch()
            raise
        finally:
            self._lock.close()


def chrome_endpoint(profile: Path) -> str | None:
    try:
        lines = (profile / 'DevToolsActivePort').read_text(encoding='utf-8').splitlines()
    except FileNotFoundError:
        return None
    if (len(lines) != 2 or not lines[0].isdigit() or not 0 < int(lines[0]) < 65536
            or not re.fullmatch(r'/devtools/browser/[A-Za-z0-9-]+', lines[1])):
        raise BrowserConnectionError('Chrome 연결 정보가 잘못되었습니다. 프로필은 보존했습니다.')
    return 'ws://127.0.0.1:' + lines[0] + lines[1]


def _chrome_endpoint_is_live(endpoint: str) -> bool:
    """Confirm the loopback server owns the exact DevTools endpoint in the profile."""
    target = urlsplit(endpoint)
    try:
        with closing(http.client.HTTPConnection('127.0.0.1', target.port, timeout=1)) as connection:
            connection.request('GET', '/json/version')
            response = connection.getresponse()
            if response.status != 200:
                return False
            metadata = json.loads(response.read(16384))
        advertised = urlsplit(metadata.get('webSocketDebuggerUrl', ''))
        return (advertised.scheme == 'ws' and advertised.hostname in {'127.0.0.1', 'localhost'}
                and advertised.port == target.port and advertised.path == target.path)
    except (OSError, http.client.HTTPException, ValueError, AttributeError, TypeError):
        return False


def _endpoint_fingerprint(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode('utf-8')).hexdigest()


def _browser_pid(process_info: dict) -> int | None:
    return next((p['id'] for p in process_info['processInfo']
                 if p['type'] == 'browser' and type(p['id']) is int and p['id'] > 0), None)


@contextmanager
def _local_chrome_cdp(endpoint: str, websocket, *, timeout=2):
    with closing(websocket.create_connection(endpoint, timeout=timeout, suppress_origin=True,
                                             http_no_proxy=['127.0.0.1'])) as connection:
        def send(method, command_id, *, wait=True, params=None, session_id=None):
            command = {'id': command_id, 'method': method}
            if params is not None:
                command['params'] = params
            if session_id is not None:
                command['sessionId'] = session_id
            connection.settimeout(timeout)
            connection.send(json.dumps(command))
            if not wait:
                return None
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('CDP response timed out')
                connection.settimeout(remaining)
                response = json.loads(connection.recv())
                if 'id' in response or 'method' not in response:
                    break
            if response.get('id') != command_id or 'error' in response:
                raise ValueError('unexpected CDP response')
            return response['result']
        yield send


def _remember_app_browser(profile: Path, endpoint: str, process) -> None:
    """Record ownership only when the browser reports the PID we just launched."""
    try:
        import websocket
    except ImportError:
        print('진단: Chrome 소유 정보 기록 생략 (WebSocket 모듈 없음)', flush=True)
        return
    try:
        with _local_chrome_cdp(endpoint, websocket) as send:
            pid = _browser_pid(send('SystemInfo.getProcessInfo', 1))
        if pid != process.pid or process.poll() is not None:
            return
        owner = {'endpoint': _endpoint_fingerprint(endpoint), 'pid': pid}
        (profile / CHROME_OWNER_FILENAME).write_text(json.dumps(owner), encoding='utf-8')
        print('진단: 발행용 Chrome의 앱 실행 정보 확인', flush=True)
    except (OSError, websocket.WebSocketException, AttributeError, KeyError, TypeError, ValueError) as exc:
        print(f'진단: Chrome 소유 정보 기록 생략 (예외={type(exc).__name__})', flush=True)


def _restart_idle_app_chrome(profile: Path, endpoint: str) -> None:
    """Close only a verified, idle Windows browser and wait for its process to exit."""
    stage = '로컬 제어 연결'
    try:
        import _winapi
        import websocket
        owner = json.loads((profile / CHROME_OWNER_FILENAME).read_text(encoding='utf-8'))
        pid = owner['pid']
        if (owner['endpoint'] != _endpoint_fingerprint(endpoint) or
                type(pid) is not int or pid <= 0 or chrome_endpoint(profile) != endpoint):
            raise ValueError('ownership mismatch')
    except (ImportError, OSError, KeyError, TypeError, ValueError) as exc:
        raise BrowserConnectionError('앱이 실행한 발행용 Chrome인지 확인할 수 없어 자동 재시작을 생략했습니다. 해당 창을 정상 종료한 뒤 다시 시도하세요.') from exc

    try:
        # This uses a separate local connection even when Playwright's full
        # attachment stalls. Only inspect browser state and request normal exit.
        with _local_chrome_cdp(endpoint, websocket) as send:
            stage = 'Chrome 프로세스 확인'
            if _browser_pid(send('SystemInfo.getProcessInfo', 1)) != pid:
                raise BrowserConnectionError('Chrome 프로세스 소유를 확인할 수 없어 자동 재시작을 생략했습니다.')
            handle = _winapi.OpenProcess(_winapi.SYNCHRONIZE, False, pid)
            try:
                stage = '열린 탭 확인'
                targets = send('Target.getTargets', 2)['targetInfos']
                if not isinstance(targets, list):
                    raise ValueError('unexpected target list')
                if any(t['type'] != 'page' or t['url'] not in IDLE_CHROME_URLS for t in targets):
                    raise BrowserConnectionError('발행용 Chrome에 작업 중일 수 있는 탭이 열려 있어 자동 재시작을 생략했습니다. 해당 창을 확인한 뒤 다시 시도하세요.')
                print(f'진단: 앱이 실행한 발행용 Chrome 정상 종료 요청 (빈 탭={len(targets)})', flush=True)
                stage = 'Chrome 정상 종료'
                send('Browser.close', 3, wait=False)
                if _winapi.WaitForSingleObject(handle, 8000) != _winapi.WAIT_OBJECT_0:
                    raise BrowserConnectionError('발행용 Chrome 정상 종료를 확인하지 못했습니다. 해당 창을 확인한 뒤 다시 시도하세요.')
            finally:
                _winapi.CloseHandle(handle)
    except (OSError, websocket.WebSocketException, AttributeError, KeyError, TypeError, ValueError) as exc:
        print(f'진단: 발행용 Chrome 자동 재시작 중단 (단계={stage}, 예외={type(exc).__name__})', flush=True)
        raise BrowserConnectionError('발행용 Chrome의 탭 상태 또는 정상 종료를 확인할 수 없어 자동 재시작을 중단했습니다. 해당 창을 확인하세요.') from exc
    print('진단: 발행용 Chrome 정상 종료 확인, 같은 프로필로 한 번 재시작합니다.', flush=True)


def launch_profile_chrome(profile: Path, *, stderr):
    candidates = [Path(os.environ.get(key, '')) / 'Google/Chrome/Application/chrome.exe'
                  for key in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA')]
    executable = next((str(p) for p in candidates if p.is_file()), None) or shutil.which('chrome')
    if not executable:
        raise BrowserConnectionError('설치된 Google Chrome을 찾을 수 없습니다.')
    # Use the same Windows DLL-safe subprocess helper as the GUI.
    import importlib.util
    helper = Path(__file__).resolve().parents[2] / 'source/core/subprocess_runner.py'
    spec = importlib.util.spec_from_file_location('_chrome_spawn', helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Chrome outlives prelogin; inherited stdout keeps the GUI reader waiting for EOF.
    return module.external_popen([executable, '--remote-debugging-address=127.0.0.1',
        '--remote-debugging-port=0', '--user-data-dir=' + str(profile),
        '--no-first-run', '--no-default-browser-check', '--enable-logging=stderr', '--log-level=2',
        'about:blank'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=stderr, close_fds=True)


def _chrome_exit_diagnostic(stderr):
    """Return known startup error categories without exposing Chrome's raw output."""
    stderr.seek(max(0, os.fstat(stderr.fileno()).st_size - 16384))
    detail = stderr.read().decode('utf-8', errors='replace').lower()
    categories = (
        ('프로필 사용 중', ('profile in use', 'profile appears to be in use', 'sharing violation')),
        ('파일 또는 실행 권한 거부', ('access is denied', 'access denied', 'permission denied', '0xc0000022')),
        ('브라우저 구성 요소 로드 실패', ('failed to load chrome.dll', '0xc0000135', '0xc000007b')),
        ('Chrome 보안 프로세스 시작 실패', ('sandbox cannot', 'failed to start sandbox', 'sandbox win error')),
    )
    return next((label for label, patterns in categories if any(value in detail for value in patterns)), '미분류')


@contextmanager
def chrome_session(playwright, profile: Path, *, ready_timeout=20, restart_if_idle=False):
    """Serialize profile operations, attach or relaunch once, leave Chrome alive."""
    profile = Path(profile).resolve()
    print(f'진행 상황: 발행용 Chrome 프로필={profile}', flush=True)
    lock = _ProfileLock(profile)
    browser = None
    startup_stderr = None
    process = None
    restarted = False
    try:
        last_cdp_error = None

        def connect(endpoint, *, timeout=1500, step=None):
            nonlocal last_cdp_error
            if endpoint is None:
                return None
            started = time.monotonic()
            last_cdp_error = None
            try:
                return playwright.chromium.connect_over_cdp(endpoint, timeout=timeout)
            except Exception as exc:
                last_cdp_error = type(exc).__name__
                return None
            finally:
                if step:
                    print(f'진단: 기존 Chrome CDP {step} (경과={time.monotonic()-started:.2f}초, 제한={timeout}ms, 결과={last_cdp_error or "연결 완료"})', flush=True)

        previous_endpoint = chrome_endpoint(profile)
        browser = connect(previous_endpoint, step='첫 연결')
        endpoint_live = False
        if browser is None and previous_endpoint is not None:
            started = time.monotonic()
            endpoint_live = _chrome_endpoint_is_live(previous_endpoint)
            print(f'진단: 기존 Chrome HTTP 상태 확인 (경과={time.monotonic()-started:.2f}초, 응답 확인={endpoint_live})', flush=True)
        if endpoint_live:
            print(f'진단: 기존 Chrome은 응답 중입니다. CDP 연결 재시도 (첫 예외={last_cdp_error})', flush=True)
            browser = connect(previous_endpoint, timeout=8000, step='재연결')
            if browser is None:
                print(f'진단: 기존 Chrome CDP 재연결 예외={last_cdp_error}', flush=True)
                if not restart_if_idle:
                    raise BrowserConnectionError('발행용 Chrome은 실행 중이지만 제어 연결이 열리지 않습니다. 해당 프로필 창을 정상 종료한 뒤 다시 시도하세요.')
                _restart_idle_app_chrome(profile, previous_endpoint)
                restarted = True
        if browser is not None:
            print('진행 상황: 실행 중인 발행용 Chrome 연결 재사용', flush=True)
        if browser is None:
            print('진행 상황: 브라우저 준비 중', flush=True)
            profile.mkdir(parents=True, exist_ok=True)
            try:
                startup_stderr = tempfile.TemporaryFile(mode='w+b')
                process = launch_profile_chrome(profile, stderr=startup_stderr)
            except OSError as exc:
                print(f'진단: Chrome 실행 예외={type(exc).__name__}, Windows 오류={getattr(exc, "winerror", None)}', flush=True)
                raise BrowserConnectionError('Chrome 실행에 실패했습니다.') from exc
            deadline = time.monotonic() + ready_timeout
            reported_handoff = False
            new_endpoint_seen = False
            while time.monotonic() < deadline:
                exit_code = process.poll()
                if exit_code not in (None, 0, CHROME_PROCESS_NOTIFIED):
                    cause = _chrome_exit_diagnostic(startup_stderr)
                    print(f'진단: Chrome 시작 종료 코드={exit_code}, 유형={cause}, 새 연결 정보={new_endpoint_seen}, CDP 예외={last_cdp_error or "없음"}', flush=True)
                    if exit_code == CHROME_PROFILE_IN_USE or cause == '프로필 사용 중':
                        raise ProfileLockError('Chrome 프로필이 다른 브라우저에서 사용 중입니다. 해당 창을 정상 종료한 뒤 다시 시도하세요.')
                    raise BrowserConnectionError('Chrome이 연결 전에 종료되었습니다. 인증 만료가 아닌 브라우저 실행/연결 오류입니다.')
                if exit_code is not None and not reported_handoff:
                    print(f'진단: Chrome 시작 프로세스 종료 코드={exit_code}, 기존 또는 새 브라우저의 연결을 기다립니다.', flush=True)
                    reported_handoff = True
                try:
                    endpoint = chrome_endpoint(profile)
                except BrowserConnectionError:
                    # Chrome can expose the file before both lines are written.
                    endpoint = None
                if endpoint is not None and (not restarted or endpoint != previous_endpoint):
                    new_endpoint_seen |= endpoint != previous_endpoint
                    browser = connect(endpoint)
                    if browser is not None:
                        if endpoint != previous_endpoint and process.poll() is None:
                            _remember_app_browser(profile, endpoint, process)
                        if restarted:
                            print('진행 상황: 재시작한 발행용 Chrome 연결 완료', flush=True)
                        break
                time.sleep(.2)
            if browser is None:
                print(f'진단: Chrome 연결 대기 종료, 시작 종료 코드={process.poll()}, 새 연결 정보={new_endpoint_seen}, CDP 예외={last_cdp_error or "없음"}', flush=True)
                if reported_handoff:
                    raise BrowserConnectionError('Chrome 시작 프로세스가 종료됐고 발행용 프로필에 연결할 수 없습니다. 해당 프로필 창이 열려 있다면 정상 종료한 뒤 다시 시도하세요.')
                raise BrowserConnectionError('Chrome 연결 준비 시간이 초과되었습니다. 로그아웃으로 판정하지 않았습니다.')
        if not browser.contexts:
            raise BrowserConnectionError('Chrome 기본 프로필에 연결하지 못했습니다.')
        try:
            yield browser.contexts[0]
        except (LoginFailedError, SessionVerificationError, KeyboardInterrupt):
            raise
        except Exception as exc:
            from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout
            if isinstance(exc, PlaywrightTimeout):
                raise SessionVerificationError('통신 시간이 초과되었습니다. 인증 만료로 판정하지 않았습니다.') from exc
            if isinstance(exc, PlaywrightError):
                exit_code = process.poll() if process is not None else None
                if process is not None and exit_code is None and not browser.is_connected():
                    try:
                        exit_code = process.wait(timeout=.5)
                    except subprocess.TimeoutExpired:
                        pass
                cause = _chrome_exit_diagnostic(startup_stderr) if exit_code is not None else '미분류'
                print(f'진단: CDP 연결 후 오류={type(exc).__name__}, Chrome 연결={browser.is_connected()}, 시작 프로세스 종료 코드={exit_code}, 유형={cause}', flush=True)
                raise BrowserConnectionError('브라우저 연결 또는 통신이 끊겼습니다. 인증 만료로 판정하지 않았습니다.') from exc
            raise
    finally:
        # Playwright.stop disconnects the transport; never close the default context.
        if startup_stderr is not None:
            startup_stderr.close()
        lock.close()


def _auth_diagnostics(context, page, response, started):
    """Only structural facts: no page text, account IDs, query strings or cookies."""
    try:
        location = urlsplit(page.url)
        route = location.path if location.path in ('/MyBlog.naver', '/PostWriteForm.naver', '/nidlogin.login') else '/<기타>'
        frames = list(page.frames)
        blog_frames = [f for f in frames if urlsplit(f.url).hostname == 'blog.naver.com']
        titles = sum(f.locator('.se-section-documentTitle').count() for f in blog_frames)
        print(f"진단: 인증 화면 host={location.hostname}, 경로={route}, HTTP={response.status if response is not None else '없음'}, 경과={time.monotonic()-started:.1f}초", flush=True)
        print(f"진단: 인증 쿠키 존재={bool(_has_auth_cookie(context))}, 프레임={len(frames)}, 블로그 프레임={len(blog_frames)}, 에디터 제목 요소={titles}", flush=True)
    except Exception:
        print('진단: 브라우저 연결 상태로 인해 화면 구조 정보를 수집하지 못했습니다.', flush=True)


def verify_chrome_auth(context, *, keep_page=False):
    """Require an authenticated editor response, cookies alone are insufficient."""
    page = None
    authenticated = False
    response = None
    started = time.monotonic()
    print('진행 상황: 인증 페이지 조회 — 글쓰기 에디터 확인 (최대 10초 대기, 페이지 이동 최대 30초)', flush=True)
    try:
        page = context.new_page()
        response = page.goto('https://blog.naver.com/MyBlog.naver?Redirect=Write',
                             wait_until='domcontentloaded', timeout=30000)
        if response is None or not response.ok:
            _auth_diagnostics(context, page, response, started)
            raise SessionVerificationError('통신 오류: 네이버 글쓰기 화면을 확인하지 못했습니다.')
        deadline = time.monotonic() + 10
        while True:
            urls = [page.url] + [f.url for f in page.frames]
            if any(urlsplit(u).hostname == 'nid.naver.com' and
                   urlsplit(u).path.startswith('/nidlogin.login') for u in urls):
                print('진단: 로그인 화면으로 이동했습니다. 수동 재로그인이 필요합니다.', flush=True)
                return False
            if page.locator('#id').count() and page.locator('#pw').count():
                print('진단: 로그인 입력 화면을 확인했습니다. 수동 재로그인이 필요합니다.', flush=True)
                return False
            if _has_auth_cookie(context) and any(
                f.locator('.se-section-documentTitle').count() for f in page.frames
                if urlsplit(f.url).hostname == 'blog.naver.com'):
                print('진단: 인증 쿠키 및 글쓰기 에디터 접근 확인 완료', flush=True)
                authenticated = True
                return True
            if time.monotonic() >= deadline:
                _auth_diagnostics(context, page, response, started)
                print('진단: 인증 확인에서 중단 (임시저장 버튼 미실행). 로딩 지연·추가 인증·안내 화면·에디터 구조 변경 가능성이 있습니다. 생성된 MD와 이미지는 유지됩니다.', flush=True)
                raise SessionVerificationError('인증 확인 불가: 글쓰기 화면을 확인하지 못했습니다. 로그아웃으로 판정하지 않았습니다.')
            page.wait_for_timeout(200)
    except SessionVerificationError:
        raise
    except Exception as exc:
        if page is not None:
            _auth_diagnostics(context, page, response, started)
        print(f'진단: 인증 조회 예외 유형={type(exc).__name__} (응답 본문과 자격증명 생략)', flush=True)
        if context.browser is not None and not context.browser.is_connected():
            raise BrowserConnectionError('Chrome 연결이 끊겼습니다. 인증 만료로 판정하지 않았습니다.') from exc
        raise SessionVerificationError('통신 오류: 로그인 확인을 완료하지 못했습니다.') from exc
    finally:
        try:
            if page is not None and not (keep_page and authenticated):
                page.close()
        except Exception:
            pass


def logout_chrome_auth(context):
    """Use Naver's website logout; never delete the profile or cookie database."""
    page = None
    try:
        page = context.new_page()
        response = page.goto('https://nid.naver.com/nidlogin.logout?returl=https%3A%2F%2Fwww.naver.com%2F',
                             wait_until='domcontentloaded', timeout=30000)
        if response is None or not response.ok:
            raise SessionVerificationError('로그아웃 요청 결과를 확인하지 못했습니다.')
        if verify_chrome_auth(context) or _has_auth_cookie(context):
            raise SessionVerificationError('로그인 상태가 남아 있어 로그아웃 완료를 확인하지 못했습니다.')
    except SessionVerificationError:
        raise
    except Exception as exc:
        raise SessionVerificationError('로그아웃 확인 중 연결 또는 통신 오류가 발생했습니다. 상태를 다시 확인해 주세요.') from exc
    finally:
        if page is not None:
            try:
                page.close()
            except Exception:
                pass


def require_chrome_auth(context, *, interactive=False, timeout=300):
    print('진행 상황: 로그인 확인 중', flush=True)
    def verify():
        # Explicit login keeps the verified editor visible for the user.
        # Background checks still clean up their temporary tabs.
        return verify_chrome_auth(context, keep_page=True) if interactive else verify_chrome_auth(context)

    if verify():
        return
    if not interactive:
        raise LoginFailedError('인증 만료: 발행을 중단했습니다. 로그인 버튼으로 직접 재로그인하세요.')
    page = context.new_page()
    try:
        page.goto(NIDLOGIN_URL, wait_until='domcontentloaded', timeout=30000)
        print('진행 상황: 수동 로그인 대기 — 단일 발행 프로필에서 직접 로그인하세요. 로그인 유지를 원하면 로그인 상태 유지 옵션을 선택하세요.', flush=True)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if page.is_closed():
                raise LoginFailedError('수동 로그인이 완료되기 전에 창이 닫혔습니다.')
            page.wait_for_timeout(1000)
            if _has_auth_cookie(context) and verify():
                return
        raise LoginFailedError('수동 로그인 대기 시간이 초과되었습니다. 다시 로그인 버튼을 누르세요.')
    finally:
        # Keep the manual page visible; it is owned by the user.
        pass


def _verify_session(context: Any, expected_id: str, *, allow_pending: bool = False) -> bool:
    """Verify the authenticated account on Naver's account page, not by URL alone.

    An explicit login form means authentication is needed; unknown/error pages fail
    closed. A positive result requires both a Naver auth cookie and the exact account
    ID rendered on the account page. The cookies and account ID are never logged.
    """
    page = context.new_page()
    try:
        response = page.goto(ACCOUNT_URL, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
        if response is None or not response.ok:
            raise SessionVerificationError("네이버 계정 확인 페이지를 불러오지 못했습니다.")
        if page.locator("#id").count() and page.locator("#pw").count():
            return False
        if not _has_auth_cookie(context):
            # During an interactive login, challenges can show an intermediate
            # page without the login form and before an auth cookie exists.
            if allow_pending:
                return False
            raise SessionVerificationError("로그인 상태를 확인할 수 없습니다.")
        if expected_id and page.get_by_text(expected_id, exact=True).count() == 0:
            # The cookie may appear before the account page finishes the
            # authentication flow. Never accept an unverified identity.
            if allow_pending:
                return False
            raise SessionVerificationError("로그인한 계정의 소유자를 확인할 수 없습니다. 계정과 화면을 확인하세요.")
        return True
    except SessionVerificationError:
        raise
    except Exception as exc:
        raise SessionVerificationError("네이버 로그인 상태 확인 중 오류가 발생했습니다: " + _safe_diagnostic(exc, expected_id)) from exc
    finally:
        page.close()


def create_browser_context(
    playwright: Any,
    headless: bool,
    session_file: str,
    expected_id: str | None = None,
    verify_existing: bool = True,
) -> tuple[Any, bool]:
    profile_dir = Path(session_file).parent
    lock = _ProfileLock(profile_dir)
    # Chromium enforces exclusive use of its profile. Never connect to, or terminate,
    # a process merely because it owns a debugging port.
    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
        context = playwright.chromium.launch_persistent_context(str(profile_dir), headless=headless, viewport={"width": 1280, "height": 800})
    except BaseException as exc:
        lock.close()
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise SessionVerificationError("브라우저 프로필을 열 수 없습니다. 다른 브라우저에서 사용 중인지 확인하세요.") from exc
    context = _LockedContext(context, lock)
    try:
        # No authentication cookie means there is no session to verify. Avoid
        # opening a transient account tab before the user starts logging in.
        return context, (_verify_session(context, expected_id)
                         if verify_existing and _has_auth_cookie(context) else False)
    except BaseException:
        context.close()
        raise


def login(context: Any, config: Config) -> None:
    page = context.pages[0] if context.pages else context.new_page()
    initial_auth = _auth_cookie(context)
    page.goto(NIDLOGIN_URL, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
    print("브라우저에서 직접 로그인을 완료해주세요.", file=sys.stderr)
    # Leave the login/challenge page untouched while waiting for authentication.
    # A missing or mistyped credential is for the user to correct in that page.
    while True:
        time.sleep(2)
        if page.is_closed():
            raise LoginFailedError("로그인 브라우저가 닫혔습니다.")
        current_auth = _auth_cookie(context)
        host = (urlsplit(page.url).hostname or "").lower()
        returned_to_naver = (host == "naver.com" or host.endswith(".naver.com")) and host != "nid.naver.com"
        if current_auth and (current_auth != initial_auth or returned_to_naver):
            # Allow the interactive authentication redirect to settle, then verify
            # once. Never poll the account endpoint after a failed verification.
            time.sleep(5)
            try:
                verified = _verify_session(context, config.naver_id, allow_pending=True)
            except SessionVerificationError as exc:
                raise SessionVerificationError(
                    "네이버 계정 확인에 실패해 자동 재접속을 중단했습니다. 브라우저의 인증·제한 안내를 확인하세요."
                ) from exc
            if not verified:
                raise SessionVerificationError(
                    "로그인한 계정을 확인하지 못해 자동 재접속을 중단했습니다. 인증을 완료하고 다시 실행하세요."
                )
            return


def login_with_recovery(playwright: Any, context: Any, config: Config, headless: bool) -> Any:
    if headless:
        raise LoginFailedError("새 로그인이 필요합니다. 헤드리스 모드를 끄고 브라우저에서 직접 로그인하세요.")
    login(context, config)
    return context
