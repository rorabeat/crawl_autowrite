"""Shopping operations sharing the publisher Chrome, without a cookie copy."""
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import quote
import re

from shopping_connect import (ShoppingSettings, Product as LegacyProduct, SearchResult,
    ShoppingError, AuthenticationRequired, ExtractionError, NetworkError, Cancelled,
    load_settings, sort_products, _safe_http_url, _check_cancel, _exclusive_browser, BASE)

@dataclass
class Product(LegacyProduct):
    space_id: str = ''

class ShoppingService:
    GATEWAY = 'https://gw-brandconnect.naver.com'

    def __init__(self, settings):
        self.settings = settings

    @contextmanager
    def _session(self):
        import config
        from playwright.sync_api import sync_playwright
        from NaverAutoWrite.login_session.session import chrome_session, chrome_endpoint, SessionVerificationError
        try:
            with sync_playwright() as pw:
                profile = config.publish_profile()
                # Search only uses the existing context's request API. Attaching to an
                # already running Chrome does not need the publisher's profile lock.
                endpoint = chrome_endpoint(profile)
                if endpoint:
                    browser = pw.chromium.connect_over_cdp(endpoint, timeout=5000)
                    if not browser.contexts:
                        raise ShoppingError('실행 중인 Chrome의 세션을 확인하지 못했습니다.')
                    yield browser.contexts[0]
                else:
                    with chrome_session(pw, profile) as context:
                        yield context
        except SessionVerificationError as exc:
            raise ShoppingError(str(exc)) from exc

    def _call(self, context, method, path, *, space=None, data=None):
        headers = {'accept': 'application/json', 'origin': BASE, 'referer': BASE + '/'}
        if space:
            headers['x-space-id'] = space
        try:
            response = context.request.fetch(self.GATEWAY + path, method=method,
                headers=headers, data=data, timeout=20000, max_redirects=0)
            try:
                if response.status == 401:
                    raise AuthenticationRequired('네이버 인증이 만료되었습니다. 설정의 네이버 로그인으로 다시 로그인하세요.')
                if response.status == 403:
                    raise AuthenticationRequired('쇼핑 커넥트 접근 권한 또는 네이버 인증을 확인하세요.')
                if not response.ok or 300 <= response.status < 400:
                    raise NetworkError(f'쇼핑 커넥트 응답 오류 (HTTP {response.status}).')
                result = response.json()
                if not isinstance(result, dict):
                    raise ExtractionError('쇼핑 커넥트 응답 구조를 확인할 수 없습니다.')
                return result
            finally:
                response.dispose()
        except ShoppingError:
            raise
        except Exception as exc:
            raise NetworkError('쇼핑 커넥트 요청을 완료하지 못했습니다. 연결 상태를 확인하세요.') from exc

    def _space(self, context):
        me = self._call(context, 'GET', '/brand-connect/query/me')
        ids = me.get('activeSpaceIds') or [s.get('id') for s in me.get('spaces', []) if isinstance(s, dict)]
        if not me.get('creator') or not ids:
            raise AuthenticationRequired('로그인 계정의 브랜드커넥트 가입·스페이스 개설·이용 동의를 확인하세요.')
        previous = self.settings.account_root.rstrip('/').rsplit('/', 1)[-1]
        space = next((str(i) for i in ids if str(i) == previous), str(ids[0]))
        if not re.fullmatch(r'[0-9]+', space):
            raise ExtractionError('쇼핑 커넥트 스페이스 정보를 확인할 수 없습니다.')
        self.settings.account_root = f'{BASE}/{space}'
        return space

    def _product(self, item, space, rank):
        pid = str(item.get('id', ''))
        if not re.fullmatch(r'[0-9]+', pid) or not item.get('productName'):
            raise ExtractionError('상품 응답에 필수 정보가 없습니다.')
        review = item.get('reviewInfo') or {}
        return Product(id=pid, title=item['productName'],
            price=item.get('discountedSalePrice', item.get('salePrice')),
            commission=(item.get('commissionRate') or 0) + (item.get('promotionCommissionRate') or 0),
            store=item.get('storeName'), image_url=_safe_http_url(item.get('representativeProductImageUrl')),
            detail_url=f'{BASE}/{space}/affiliate/products/{pid}',
            original_url=_safe_http_url(item.get('productUrl')),
            affiliate_url=_safe_http_url(item.get('shortenUrl')),
            rating=review.get('averageReviewScore'), reviews=review.get('totalReviewCount'),
            original_rank=rank, space_id=space)

    @_exclusive_browser
    def open_site(self, cancel_event=None):
        _check_cancel(cancel_event)
        with self._session() as context:
            page = context.new_page()
            try:
                page.goto(BASE, wait_until='domcontentloaded', timeout=30000)
            except Exception as exc:
                page.close()
                raise NetworkError('브랜드커넥트 화면을 열지 못했습니다.') from exc
            # This page was explicitly opened by the user; leave it visible.

    @_exclusive_browser
    def connect(self, cancel_event=None):
        _check_cancel(cancel_event)
        with self._session() as context:
            self._space(context)
            _check_cancel(cancel_event)
        return self.settings

    @_exclusive_browser
    def search(self, keyword, sort='relevance', max_items=300, cancel_event=None):
        keyword = keyword.strip()
        if not keyword:
            raise ValueError('검색어를 입력하세요.')
        if not isinstance(max_items, int) or isinstance(max_items, bool) or not 1 <= max_items <= 300:
            raise ValueError('수집 개수는 1~300이어야 합니다.')
        sort_products([], sort)
        _check_cancel(cancel_event)
        with self._session() as context:
            space = self._space(context)
            products, seen, cursors = [], set(), set()
            cursor, total = '', None
            while len(seen) < max_items:
                _check_cancel(cancel_event)
                path = '/affiliate/query/affiliate-products/search-by-query?query=' + quote(keyword, safe='') + '&limit=' + str(min(100, max_items-len(seen)))
                if cursor:
                    path += '&cursor=' + quote(cursor, safe='')
                result = self._call(context, 'GET', path, space=space)
                items = result.get('data')
                if not isinstance(items, list):
                    raise ExtractionError('상품 검색 응답 구조가 변경되었습니다.')
                before = len(seen)
                for item in items:
                    if not isinstance(item, dict):
                        raise ExtractionError('상품 검색 응답 구조가 변경되었습니다.')
                    pid = str(item.get('id', ''))
                    if pid in seen:
                        continue
                    seen.add(pid)
                    if item.get('enabled') and item.get('productStatus') == 'SALE':
                        products.append(self._product(item, space, len(seen)))
                    if len(seen) >= max_items:
                        break
                paging = result.get('paging') or {}
                total = paging.get('total', total)
                cursor = paging.get('nextCursor')
                if not cursor:
                    break
                if not isinstance(cursor, str) or cursor in cursors or len(seen) == before:
                    raise ExtractionError('상품 검색 페이지가 반복되어 중단했습니다.')
                cursors.add(cursor)
            _check_cancel(cancel_event)
            return SearchResult(sort_products(products, sort), total, len(products),
                '수집한 상품 범위에서 정렬합니다.' if cursor else '')

    @_exclusive_browser
    def issue_link(self, product, cancel_event=None):
        if not re.fullmatch(r'[0-9]+', str(product.id)):
            raise ShoppingError('상품 번호를 확인할 수 없습니다.')
        _check_cancel(cancel_event)
        with self._session() as context:
            space = self._space(context)
            if not getattr(product, 'space_id', '') or product.space_id != space:
                raise AuthenticationRequired('네이버 계정이 변경되었거나 이전 검색 결과입니다. 상품을 다시 검색하세요.')
            current = self._call(context, 'GET', '/affiliate/query/affiliate-products/' + product.id, space=space)
            if str(current.get('id')) != product.id:
                raise ExtractionError('선택한 상품과 응답이 다릅니다.')
            existing = _safe_http_url(current.get('shortenUrl'))
            if existing:
                return existing
            if not current.get('enabled') or current.get('productStatus') != 'SALE':
                raise ShoppingError('제휴 또는 판매가 중지된 상품입니다.')
            _check_cancel(cancel_event)
            try:
                issued = self._call(context, 'POST', '/affiliate/command/affiliate-urls?affiliateProductId=' + product.id,
                    space=space, data={})
                link = _safe_http_url(issued.get('url'))
                if not link:
                    raise ExtractionError('발급된 링크 응답을 확인할 수 없습니다.')
                return link
            except ShoppingError as exc:
                raise ShoppingError('링크 발급 결과를 확인하지 못했습니다. 다시 추가하기 전에 브랜드커넥트에서 발급 여부를 확인하세요.') from exc
