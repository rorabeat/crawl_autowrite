"""Travel Connect operations and the public Brand Connect entry point."""

from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass, replace
from decimal import Decimal
from functools import partial
from hashlib import sha256
from itertools import count
import json
import re
from time import monotonic, sleep
from urllib.parse import urlencode, urlsplit
import webbrowser

from shopping_connect import (
    BASE, AuthenticationRequired, ExtractionError, NetworkError, ShoppingError,
    _check_cancel, _exclusive_browser, _safe_http_url,
)
from source.core.shopping_session import ShoppingService


OFFICIAL_SITE = f'{BASE}/redirect/travel-connect/products'
PRODUCTS = '/affiliate/query/connect/products'
PRODUCT_DETAIL = f'{PRODUCTS}/detail'
ISSUE_LINK = '/affiliate/command/connect-urls'
CURRENT_ACCOUNT = '/brand-connect/query/me'
TRAVEL_TYPE = 'TRAVEL_PACKAGE'


class TravelError(ShoppingError):
    """A Travel Connect operation could not be completed."""


class TravelIntegrationUnavailable(TravelError):
    """The shared web session could not be used."""


@dataclass(frozen=True)
class TravelOffer:
    key: str
    scope: str
    title: str
    seller: str | None
    display_price: str | None
    detail_url: str
    rank: int
    comparable_price: Decimal | None = None
    price_basis: str = ''


@dataclass(frozen=True)
class TravelLink:
    url: str
    reused: bool


class TravelService:
    def __init__(self, settings):
        self._shopping = ShoppingService(replace(settings))

    @_exclusive_browser
    def search(self, keyword, *, cancel_event=None) -> list[TravelOffer]:
        _check_cancel(cancel_event)
        if not isinstance(keyword, str) or not keyword.strip():
            raise ValueError('여행지나 여행 상품명을 입력하세요.')
        path = PRODUCTS + '?' + urlencode({'serviceType': TRAVEL_TYPE, 'query': keyword.strip()})
        with self._client(cancel_event) as request:
            scope = self._scope(request)
            _check_cancel(cancel_event)
            items = request('GET', path, space=scope).get('data')
            if not isinstance(items, list):
                raise ExtractionError('여행 상품 검색 결과를 읽을 수 없습니다.')
            offers, keys = [], set()
            for rank, item in enumerate(items, 1):
                offer = self._offer(item, scope, rank)
                if offer and offer.key not in keys:
                    keys.add(offer.key)
                    offers.append(offer)
            _check_cancel(cancel_event)
            return offers

    @staticmethod
    def _offer(item, scope, rank):
        if not isinstance(item, dict) or item.get('connectServiceType') != TRAVEL_TYPE:
            raise ExtractionError('여행 상품 정보의 형식을 확인할 수 없습니다.')
        extra = item.get('extra')
        if not isinstance(extra, dict):
            extra = {}
        key, title = item.get('productId'), item.get('name')
        url = _official_url(item.get('url'), 'pkgtour.naver.com')
        if not all(isinstance(value, str) and value.strip() for value in (key, title)) or not url:
            raise ExtractionError('여행 상품의 번호, 이름 또는 상세 주소를 확인할 수 없습니다.')
        seller = item.get('storeName')
        price = next((value for value in (item.get('discountedSalePrice'), item.get('salePrice'))
                      if type(value) is int and value > 0), None)
        price_types = (extra.get('type'), extra.get('productType'))
        price_basis = ':'.join(('KRW', 'listing', *price_types)) if (
            price and all(isinstance(value, str) and value for value in price_types)
        ) else ''
        return TravelOffer(
            key, scope, title, seller if isinstance(seller, str) else None,
            f'{price:,}원' if price else None, url, rank,
            Decimal(price) if price_basis else None, price_basis,
        )

    def _scope(self, request):
        data = request('GET', CURRENT_ACCOUNT)
        ids = data.get('activeSpaceIds')
        if not ids and isinstance(data.get('spaces'), list):
            ids = [space.get('id') for space in data['spaces'] if isinstance(space, dict)]
        if not data.get('creator') or not isinstance(ids, list) or not ids:
            raise AuthenticationRequired('브랜드커넥트 크리에이터 스페이스와 어필리에이트 이용 동의를 확인하세요.')
        scopes = [str(value) for value in ids if re.fullmatch(r'[0-9]+', str(value))]
        if not scopes:
            raise ExtractionError('여행 커넥트 스페이스 번호를 확인할 수 없습니다.')
        preferred = self._shopping.settings.account_root.rstrip('/').rsplit('/', 1)[-1]
        return preferred if preferred in scopes else scopes[0]

    @staticmethod
    def _detail(request, offer, scope):
        path = PRODUCT_DETAIL + '?' + urlencode({'productUrl': offer.detail_url})
        detail = request('GET', path, space=scope)
        if (detail.get('productId') != offer.key or detail.get('url') != offer.detail_url or
                detail.get('connectServiceType') != TRAVEL_TYPE):
            raise ExtractionError('선택한 여행 상품과 상세 조회 결과가 다릅니다. 다시 검색해 주세요.')
        return detail

    @staticmethod
    def _existing_link(detail):
        if 'shortenUrl' not in detail:
            raise ExtractionError('여행 상품의 기존 링크 여부를 확인할 수 없습니다.')
        if detail['shortenUrl'] in (None, ''):
            return None
        link = _official_url(detail['shortenUrl'], 'naver.me')
        if not link:
            raise ExtractionError('기존 여행 링크 주소를 확인할 수 없습니다. 브랜드커넥트에서 확인해 주세요.')
        return link

    @_exclusive_browser
    def resolve_affiliate_link(self, offer, *, cancel_event=None) -> TravelLink:
        _check_cancel(cancel_event)
        if not isinstance(offer, TravelOffer) or not _official_url(offer.detail_url, 'pkgtour.naver.com'):
            raise TravelError('선택한 여행 상품의 상세 주소를 확인할 수 없습니다. 다시 검색해 주세요.')
        with self._client(cancel_event) as request:
            scope = self._scope(request)
            if not offer.scope or offer.scope != scope:
                raise AuthenticationRequired('여행 검색 이후 연결된 스페이스가 변경되었습니다. 다시 검색해 주세요.')
            _check_cancel(cancel_event)
            detail = self._detail(request, offer, scope)
            marker = _issue_marker(scope, offer.key)
            link = self._existing_link(detail)
            if link:
                _clear_issue_marker(marker)
                _check_cancel(cancel_event)
                return TravelLink(link, reused=True)
            if detail.get('affiliateEnabled') is not True:
                raise TravelError('현재 이 여행 상품의 제휴 링크를 발급할 수 없습니다.')
            _check_cancel(cancel_event)
            _claim_issue_marker(marker)
            submitted = False
            try:
                # The first read can become stale while a previous process holds the marker.
                # Only issue after reading again with our marker in place.
                detail = self._detail(request, offer, scope)
                link = self._existing_link(detail)
                _check_cancel(cancel_event)
                if link:
                    return TravelLink(link, reused=True)
                if detail.get('affiliateEnabled') is not True:
                    raise TravelError('현재 이 여행 상품의 제휴 링크를 발급할 수 없습니다.')
                submitted = True
                issued = request('POST', ISSUE_LINK, space=scope, data={'url': offer.detail_url})
                link = _official_url(issued.get('url'), 'naver.me')
                link_id = issued.get('connectUrlId')
                if type(link_id) is not int or link_id <= 0 or not link:
                    raise ExtractionError('여행 링크 발급 응답을 확인할 수 없습니다.')
                if self._detail(request, offer, scope).get('shortenUrl') != link:
                    raise ExtractionError('여행 링크 발급 결과를 재조회할 수 없습니다.')
            except ShoppingError as exc:
                if not submitted:
                    raise
                raise TravelError('여행 링크 발급 결과를 확인하지 못했습니다. 브랜드커넥트의 발급 링크 관리에서 확인해 주세요.') from exc
            finally:
                if not submitted:
                    _clear_issue_marker(marker)
            _clear_issue_marker(marker)
            _check_cancel(cancel_event)
            return TravelLink(link, reused=False)

    @contextmanager
    def _client(self, cancel_event):
        from config import publish_profile
        from NaverAutoWrite.login_session.session import SessionVerificationError, _chrome_endpoint_is_live, chrome_endpoint
        from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout, sync_playwright

        try:
            profile = publish_profile()
            endpoint = chrome_endpoint(profile)
            if not endpoint or not _chrome_endpoint_is_live(endpoint):
                raise AuthenticationRequired('설정의 네이버 로그인에서 로그인하고 여행 커넥트 권한을 확인하세요.')
        except (SessionVerificationError, OSError) as exc:
            raise TravelIntegrationUnavailable('여행 커넥트의 공유 Chrome 연결을 확인할 수 없습니다.') from exc

        with ExitStack() as stack:
            try:
                playwright = stack.enter_context(sync_playwright())
                browser = playwright.chromium.connect_over_cdp(endpoint, timeout=5000)
            except PlaywrightTimeout:
                stack.close()
                request = stack.enter_context(_chrome_client(profile, endpoint, cancel_event))
            except (PlaywrightError, OSError) as exc:
                raise TravelIntegrationUnavailable('여행 커넥트에 연결하지 못했습니다. 공식 상품 페이지에서 이용해 주세요.') from exc
            else:
                if not browser.contexts:
                    raise TravelIntegrationUnavailable('여행 커넥트의 공유 Chrome 세션을 찾지 못했습니다.')
                request = partial(self._playwright_request, browser.contexts[0])
            _check_cancel(cancel_event)
            yield request

    def _playwright_request(self, context, method, path, *, space=None, data=None):
        try:
            return self._shopping._call(context, method, path, space=space, data=data)
        except AuthenticationRequired as exc:
            raise AuthenticationRequired('네이버 인증과 여행 커넥트 이용 권한을 확인하세요.') from exc
        except NetworkError as exc:
            raise NetworkError('여행 커넥트 요청을 완료하지 못했습니다.') from exc
        except ExtractionError as exc:
            raise ExtractionError('여행 커넥트 응답 형식을 확인할 수 없습니다.') from exc

    def open_official_site(self, *, cancel_event=None) -> None:
        _check_cancel(cancel_event)
        if self._open_in_shared_chrome(cancel_event):
            return

        _check_cancel(cancel_event)
        try:
            opened = webbrowser.open_new_tab(OFFICIAL_SITE)
        except (OSError, webbrowser.Error):
            opened = False
        if not opened:
            raise TravelError('브랜드커넥트를 열지 못했습니다. 브라우저에서 브랜드커넥트를 열어 주세요.')

    @staticmethod
    def _open_in_shared_chrome(cancel_event) -> bool:
        from config import publish_profile
        from NaverAutoWrite.login_session.session import (
            SessionVerificationError,
            _chrome_endpoint_is_live,
            chrome_endpoint,
        )
        from playwright.sync_api import Error as PlaywrightError, sync_playwright

        try:
            endpoint = chrome_endpoint(publish_profile())
            if not endpoint or not _chrome_endpoint_is_live(endpoint):
                return False
            _check_cancel(cancel_event)
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(endpoint, timeout=5000)
                if not browser.contexts:
                    return False
                _check_cancel(cancel_event)
                page = browser.contexts[0].new_page()
                try:
                    page.goto(OFFICIAL_SITE, wait_until='domcontentloaded', timeout=15000)
                except PlaywrightError:
                    page.close()
                    return False
        except (SessionVerificationError, PlaywrightError, OSError):
            return False
        return True


def _official_url(value, host):
    if isinstance(value, str) and not any(char.isspace() for char in value) and _safe_http_url(value):
        url = urlsplit(value)
        if url.hostname == host and url.path not in ('', '/') and not url.fragment:
            return value
    return None


def _marker_directory():
    from config import publish_profile
    return publish_profile().parent / '.travel-link-pending'


def _issue_marker(scope, key):
    identity = json.dumps([scope, key], ensure_ascii=True)
    return _marker_directory() / sha256(identity.encode()).hexdigest()


def _claim_issue_marker(marker):
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        with marker.open('xb'):
            pass
    except FileExistsError as exc:
        raise TravelError('이전 여행 링크 발급 여부를 브랜드커넥트의 발급 링크 관리에서 확인해 주세요.') from exc
    except OSError as exc:
        raise TravelError('링크 발급 상태를 안전하게 기록할 수 없습니다. 브랜드커넥트에서 링크를 발급해 주세요.') from exc


def _clear_issue_marker(marker):
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        # A retained marker prevents another write if the confirmed link is later missing.
        pass


@contextmanager
def _chrome_client(profile, endpoint, cancel_event):
    import websocket
    from NaverAutoWrite.login_session.session import _ProfileLock, _chrome_endpoint_is_live, _local_chrome_cdp, chrome_endpoint

    chrome_errors = (OSError, ValueError, KeyError, websocket.WebSocketException)
    with closing(_ProfileLock(profile)):
        _check_cancel(cancel_event)
        if chrome_endpoint(profile) != endpoint or not _chrome_endpoint_is_live(endpoint):
            raise TravelIntegrationUnavailable('여행 커넥트의 공유 Chrome 연결이 종료되었습니다.')
        with ExitStack() as stack:
            try:
                send = stack.enter_context(_local_chrome_cdp(endpoint, websocket, timeout=15))
                ids = count(1)
                target = send('Target.createTarget', next(ids), params={'url': 'about:blank', 'background': True})['targetId']
            except chrome_errors as exc:
                raise TravelIntegrationUnavailable('여행 커넥트 연결용 탭을 만들지 못했습니다.') from exc
            try:
                session = send('Target.attachToTarget', next(ids), params={'targetId': target, 'flatten': True})['sessionId']
                command = partial(_target_command, send, ids, session)
                if command('Page.navigate', {'url': OFFICIAL_SITE}).get('errorText'):
                    raise TravelIntegrationUnavailable('여행 커넥트 페이지를 열지 못했습니다.')
                _wait_for_official_origin(command, cancel_event)
                yield partial(_chrome_request, command)
            except chrome_errors as exc:
                raise TravelIntegrationUnavailable('여행 커넥트의 공유 Chrome 연결을 완료하지 못했습니다.') from exc
            finally:
                try:
                    send('Target.closeTarget', next(ids), params={'targetId': target})
                except chrome_errors:
                    # Disconnect from this owned background target. Leave the shared Chrome open.
                    pass


def _target_command(send, ids, session, method, params):
    return send(method, next(ids), params=params, session_id=session)


def _wait_for_official_origin(command, cancel_event):
    deadline = monotonic() + 10
    while monotonic() < deadline:
        _check_cancel(cancel_event)
        result = command('Runtime.evaluate', {
            'expression': 'document.readyState === "loading" ? "" : location.origin', 'returnByValue': True,
        })
        origin = result.get('result', {}).get('value')
        if origin == BASE:
            return
        if origin == 'https://nid.naver.com':
            raise AuthenticationRequired('설정의 네이버 로그인으로 다시 로그인해 주세요.')
        sleep(0.2)
    raise TravelIntegrationUnavailable('여행 커넥트 공식 페이지의 연결을 확인할 수 없습니다.')


def _chrome_request(command, method, path, *, space=None, data=None):
    route = path.partition('?')[0]
    if ((method == 'GET' and route not in {CURRENT_ACCOUNT, PRODUCTS, PRODUCT_DETAIL}) or
            (method == 'POST' and route != ISSUE_LINK) or method not in {'GET', 'POST'}):
        raise TravelError('여행 커넥트 요청 경로를 확인할 수 없습니다.')
    payload = json.dumps({'base': BASE, 'url': ShoppingService.GATEWAY + path,
                          'method': method, 'space': space, 'body': data})
    expression = '''(async ({base, url, method, space, body}) => {
        if (location.origin !== base) return {status: 401};
        const headers = {accept: 'application/json'};
        if (space) headers['x-space-id'] = space;
        const init = {method, headers, credentials: 'include', redirect: 'error',
                      signal: AbortSignal.timeout(12000)};
        if (body !== null) {
            headers['content-type'] = 'application/json';
            init.body = JSON.stringify(body);
        }
        try {
            const response = await fetch(url, init);
            if (!response.ok) return {status: response.status};
            try { return {status: response.status, data: await response.json()}; }
            catch { return {status: response.status}; }
        } catch { return {status: 0}; }
    })(''' + payload + ')'
    try:
        raw = command('Runtime.evaluate', {'expression': expression, 'awaitPromise': True, 'returnByValue': True})
    except Exception as exc:
        raise NetworkError('여행 커넥트 요청을 완료하지 못했습니다.') from exc
    result = raw.get('result', {}).get('value')
    if not isinstance(result, dict) or type(result.get('status')) is not int:
        raise NetworkError('여행 커넥트 요청 결과를 확인할 수 없습니다.')
    if result['status'] in (401, 403):
        raise AuthenticationRequired('네이버 인증과 여행 커넥트 이용 권한을 확인하세요.')
    if not 200 <= result['status'] < 300:
        raise NetworkError('여행 커넥트 요청을 완료하지 못했습니다.')
    if not isinstance(result.get('data'), dict):
        raise ExtractionError('여행 커넥트 응답 형식을 확인할 수 없습니다.')
    return result['data']
