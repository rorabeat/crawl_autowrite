"""Brand Connect UI adapter. Each operation owns its browser on the calling thread."""
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, quote, urljoin, urlsplit
import json
import functools
import threading
import re
import time

BASE = 'https://brandconnect.naver.com'
_DATA = Path(__file__).resolve().parent / '.shopping_connect'
SETTINGS_PATH = _DATA / 'settings.json'
DEFAULT_PROFILE = _DATA / 'browser_profile'

class ShoppingError(Exception): pass
class AuthenticationRequired(ShoppingError): pass
class ExtractionError(ShoppingError): pass
class NetworkError(ShoppingError): pass
class Cancelled(ShoppingError): pass

@dataclass
class ShoppingSettings:
    account_root: str = ''
    profile_path: str = ''

@dataclass
class Product:
    id: str
    title: str
    price: int | None = None
    commission: float | None = None
    store: str | None = None
    image_url: str | None = None
    detail_url: str = ''
    original_url: str | None = None
    affiliate_url: str | None = None
    rating: float | None = None
    reviews: int | None = None
    original_rank: int = 0

@dataclass
class SearchResult:
    products: list[Product]
    total: int | None
    collected: int
    warning: str = ''

def account_root(url):
    """Only accept the observed account route on the exact trusted host."""
    try:
        p = urlsplit(url)
        if p.scheme != 'https' or p.netloc.lower() != 'brandconnect.naver.com':
            return ''
        m = re.fullmatch(r'/([0-9]+)/affiliate/products(?:/.*)?/?', p.path)
        return f'{BASE}/{m.group(1)}' if m else ''
    except ValueError:
        return ''

def _is_search_url(url, root, keyword):
    try:
        p = urlsplit(url)
        expected = urlsplit(validate_root(root))
        query = parse_qs(p.query, keep_blank_values=True)
        return (p.scheme == 'https' and p.netloc.lower() == expected.netloc
                and p.path.rstrip('/') == expected.path + '/affiliate/products/search'
                and query.get('query') == [keyword] and query.get('tab') == ['product'])
    except (ValueError, TypeError):
        return False

def validate_root(root):
    try:
        p = urlsplit(root)
        if p.scheme == 'https' and p.netloc.lower() == 'brandconnect.naver.com' and re.fullmatch(r'/[0-9]+/?', p.path) and not p.query and not p.fragment:
            return f'{BASE}/{p.path.strip("/")}'
    except ValueError:
        pass
    raise ValueError('브랜드커넥트 계정 주소가 올바르지 않습니다.')

def _settings_root(root):
    if not root:
        return ''
    if root in (BASE, BASE + '/'):
        return ''  # Login bootstrap; the observed account route is saved after connection.
    return validate_root(root)

def load_settings():
    if not SETTINGS_PATH.exists():
        return ShoppingSettings()
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding='utf-8'))
        return ShoppingSettings(account_root=data.get('account_root', ''), profile_path=data.get('profile_path', ''))
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise ShoppingError('쇼핑 연결 설정을 읽을 수 없습니다.') from exc

def _profile_path(value):
    path = Path(value).resolve() if value else DEFAULT_PROFILE.resolve()
    # User-selectable subdirectories stay inside the dedicated shopping area.
    if path == _DATA.resolve() or not path.is_relative_to(_DATA.resolve()):
        raise ValueError('쇼핑 전용 폴더 안의 브라우저 프로필을 선택하세요.')
    return path

def save_settings(settings):
    root = _settings_root(settings.account_root)
    profile = str(_profile_path(settings.profile_path))
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps({'account_root': root, 'profile_path': profile}, ensure_ascii=False, indent=2), encoding='utf-8')
    settings.account_root = root

def _safe_http_url(value):
    try:
        p = urlsplit(value or '')
        return value if p.scheme == 'https' and p.hostname and not p.username and not p.password and p.port in (None, 443) else None
    except ValueError:
        return None

def _number(pattern, text, cast=float):
    match = re.search(pattern, text)
    return cast(match.group(1).replace(',', '')) if match else None

def parse_card(card, root, rank):
    href = urljoin(BASE, card.get('href', ''))
    try:
        p = urlsplit(href)
        r = urlsplit(validate_root(root))
        match = re.fullmatch(re.escape(r.path) + r'/affiliate/products/([0-9]+)/?', p.path)
        if p.scheme != 'https' or p.netloc != r.netloc or not match:
            return None
    except (ValueError, TypeError):
        return None
    lines = [line.strip() for line in (card.get('text') or '').splitlines() if line.strip()]
    title = (card.get('title') or '').strip()
    if not title:
        title = next ( (line for line in lines if len(line) >= 4 and not re.search(r'^[\d,.% 원]+$', line) and not re.search(r'^(수수료|리뷰|평점|할인|판매가|가격)', line)), '')
    if not title:
        return None
    text = '\n'.join(lines)
    # Several amounts may represent list and sale prices. Their order alone does
    # not establish the current price, so preserve no price when ambiguous.
    amounts = re.findall(r'([\d,]+)\s*원', text)
    price = int(amounts[0].replace(',', '')) if len(amounts) == 1 else None
    return Product(id=match.group(1), title=title, price=price,
        commission=_number(r'(?:기본\s*)?수수료\s*[:：]?\s*([\d.]+)\s*%', text),
        rating=_number(r'평점\s*[:：]?\s*([\d.]+)', text),
        reviews=_number(r'리뷰\s*[:：]?\s*([\d,]+)', text, int),
        image_url=_safe_http_url(card.get('image')), detail_url=href, original_rank=rank)

def sort_products(products, sort):
    keys = {'commission': ('commission', True), 'reviews': ('reviews', True), 'price_low': ('price', False), 'price_high': ('price', True)}
    if sort in ('relevance', 'ranking', ''):
        return sorted(products, key=lambda p: p.original_rank)
    if sort not in keys:
        raise ValueError('지원하지 않는 정렬 방식입니다.')
    field, reverse = keys[sort]
    present = [p for p in products if getattr(p, field) is not None]
    missing = [p for p in products if getattr(p, field) is None]
    return sorted(present, key=lambda p: getattr(p, field), reverse=reverse) + missing

def _check_cancel(event):
    if event is not None and event.is_set():
        raise Cancelled('작업이 취소되었습니다.')

# The route is known; markup is not. Find a nearby container with one product link
# and a price, instead of relying on unverified class names or hidden application data.
_EXTRACT = r'''() => {
 const links = [...document.querySelectorAll('a[href]')].filter(a => /\/affiliate\/products\/\d+\/?(?:[?#]|$)/.test(a.getAttribute('href')));
 return {cards: links.map(a => {
   let box = a, found = false;
   for (let i=0; i<7 && box; i++, box=box.parentElement) {
     if (/\d[\d,]*\s*원/.test(box.innerText || '') && box.querySelectorAll('a[href*="/affiliate/products/"]').length <= 1) { found = true; break; }
   }
   if (!found) return null;
   const img = box.querySelector('img');
   return {href:a.href, text:box.innerText || '', title:a.getAttribute('title') || '', image:img ? (img.currentSrc || img.src) : ''};
 }).filter(Boolean), text: document.body ? document.body.innerText : ''};
}'''

_BROWSER_LOCK = threading.Lock()

def _exclusive_browser(method):
    @functools.wraps(method)
    def wrapped(*args, **kwargs):
        if not _BROWSER_LOCK.acquire(blocking=False):
            raise ShoppingError('다른 쇼핑 연결 또는 검색 작업이 실행 중입니다.')
        try:
            return method(*args, **kwargs)
        finally:
            _BROWSER_LOCK.release()
    return wrapped

class ShoppingService:
    def __init__(self, settings):
        self.settings = settings

    def _session_file(self):
        return _profile_path(self.settings.profile_path) / 'shopping_session_cookies.json'

    def _restore_session(self, context):
        path = self._session_file()
        if not path.exists():
            return
        try:
            saved = json.loads(path.read_text(encoding='utf-8'))
            existing = {(c['name'], c['domain'], c['path']) for c in context.cookies()}
            cookies = [c for c in saved if (c['name'], c['domain'], c['path']) not in existing
                       and (c['domain'].lstrip('.') == 'naver.com' or c['domain'].lstrip('.').endswith('.naver.com'))
                       and c.get('expires') == -1]
            if cookies:
                context.add_cookies(cookies)
        except (OSError, ValueError, TypeError, KeyError):
            pass  # A damaged cache must not prevent a new manual login.

    def _save_session(self, context):
        cookies = [c for c in context.cookies() if c.get('expires') == -1
                   and (c['domain'].lstrip('.') == 'naver.com' or c['domain'].lstrip('.').endswith('.naver.com'))]
        path = self._session_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cookies, ensure_ascii=False), encoding='utf-8')

    def _open(self):
        try:
            profile = _profile_path(self.settings.profile_path)
        except ValueError as exc:
            raise ShoppingError(str(exc)) from exc
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ShoppingError('Playwright가 필요합니다. pip install playwright 후 playwright install chromium을 실행하세요.') from exc
        pw = sync_playwright().start()
        try:
            context = pw.chromium.launch_persistent_context(str(profile), headless=False, timeout=15000)
            context.set_default_timeout(5000)
            self._restore_session(context)
            return pw, context, context.pages[0] if context.pages else context.new_page()
        except Exception as exc:
            pw.stop()
            raise ShoppingError('쇼핑 전용 브라우저를 열지 못했습니다. 브라우저 설치 또는 다른 연결 작업 실행 여부를 확인하세요.') from exc

    @_exclusive_browser
    def connect(self, cancel_event=None):
        _check_cancel(cancel_event)
        pw, context, page = self._open()
        try:
            root = _settings_root(self.settings.account_root)
            target = root + '/affiliate/products' if root else BASE
            page.goto(target, wait_until='domcontentloaded', timeout=15000)
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                _check_cancel(cancel_event)
                root = account_root(page.url)
                if root:
                    page.wait_for_timeout(1000)
                    if account_root(page.url) == root:
                        self._save_session(context)
                        result = ShoppingSettings(root, str(_profile_path(self.settings.profile_path)))
                        save_settings(result)
                        self.settings = result
                        return result
                page.wait_for_timeout(250)
            raise AuthenticationRequired('로그인 후 브랜드커넥트 상품 찾기 화면으로 이동해 주세요. 연결 시간이 초과되었습니다.')
        except ShoppingError:
            raise
        except Exception as exc:
            raise NetworkError('연결 화면을 열 수 없거나 브라우저가 닫혔습니다.') from exc
        finally:
            try:
                context.close()
            finally:
                pw.stop()

    @_exclusive_browser
    def issue_link(self, product, cancel_event=None):
        root = validate_root(self.settings.account_root) if self.settings.account_root else ''
        if not root:
            raise AuthenticationRequired('먼저 쇼핑 커넥트를 연결하세요.')
        try:
            url = urlsplit(product.detail_url)
            expected = urlsplit(root)
            if (url.scheme != 'https' or url.netloc != expected.netloc or url.query or url.fragment
                    or url.path != expected.path + '/affiliate/products/' + str(product.id)
                    or not re.fullmatch(r'\d+', str(product.id))):
                raise ValueError()
        except (ValueError, TypeError):
            raise ShoppingError('선택한 상품의 상세 주소를 확인할 수 없습니다.')
        _check_cancel(cancel_event)
        pw, context, page = self._open()
        try:
            # Capture only content copied by this page, without reading or changing
            # the user's system clipboard.
            page.add_init_script("""(() => {
                window.__shoppingCopied = '';
                const install = () => {
                    if (navigator.clipboard) {
                        try { Object.defineProperty(navigator.clipboard, 'writeText', {
                            configurable: true, value: async text => { window.__shoppingCopied = String(text); }
                        }); } catch (_) {}
                    }
                };
                install();
                document.addEventListener('copy', e => {
                    setTimeout(() => { if (e.clipboardData) window.__shoppingCopied = e.clipboardData.getData('text/plain') || window.__shoppingCopied; }, 0);
                });
            })();""")
            page.goto(product.detail_url, wait_until='domcontentloaded', timeout=15000)
            deadline = time.monotonic() + 20
            issued = copied = False
            while time.monotonic() < deadline:
                _check_cancel(cancel_event)
                current = urlsplit(page.url)
                if current.scheme != 'https' or current.netloc != expected.netloc or current.path.rstrip('/') != url.path:
                    raise AuthenticationRequired('로그인 상태 또는 선택한 상품 화면을 확인해 주세요.')
                link = page.evaluate('() => window.__shoppingCopied || ""')
                if link:
                    if not _safe_http_url(link):
                        raise ExtractionError('발급된 링크를 확인할 수 없습니다.')
                    self._save_session(context)
                    return link
                copy_button = page.get_by_role('button', name='링크 복사', exact=True)
                issue_button = page.get_by_role('button', name='링크 발급', exact=True)
                if not copied and copy_button.count() == 1 and copy_button.is_visible():
                    copy_button.click(); copied = True
                elif not issued and not copied and issue_button.count() == 1 and issue_button.is_visible():
                    issue_button.click(); issued = True
                page.wait_for_timeout(250)
            raise ExtractionError('링크 발급 또는 복사 결과를 확인하지 못했습니다. 브랜드커넥트에서 발급 여부를 확인해 주세요.')
        except ShoppingError:
            raise
        except Exception as exc:
            raise NetworkError('상품 링크 발급 중 브라우저 오류가 발생했습니다. 발급 여부를 확인해 주세요.') from exc
        finally:
            try:
                context.close()
            finally:
                pw.stop()

    @_exclusive_browser
    def search(self, keyword, sort='relevance', max_items=300, cancel_event=None):
        keyword = keyword.strip()
        if not keyword:
            raise ValueError('검색어를 입력하세요.')
        if not isinstance(max_items, int) or isinstance(max_items, bool) or not 1 <= max_items <= 300:
            raise ValueError('수집 개수는 1~300이어야 합니다.')
        sort_products([], sort)
        root = validate_root(self.settings.account_root) if self.settings.account_root else ''
        if not root:
            raise AuthenticationRequired('먼저 쇼핑 커넥트를 연결하세요.')
        _check_cancel(cancel_event)
        pw, context, page = self._open()
        try:
            response = page.goto(root + '/affiliate/products/search?query=' + quote(keyword, safe='') + '&tab=product', wait_until='domcontentloaded', timeout=15000)
            if response is not None and response.status >= 400:
                if response.status in (401, 403):
                    raise AuthenticationRequired('로그인 또는 브랜드커넥트 접근 권한을 확인하세요.')
                raise NetworkError(f'검색 화면 오류 (HTTP {response.status}).')
            deadline = time.monotonic() + 30
            seen = {}
            total = None
            stalled = 0
            last_snapshot = None
            text = ''
            while time.monotonic() < deadline:
                _check_cancel(cancel_event)
                if account_root(page.url) != root:
                    raise AuthenticationRequired('로그인이 만료되었거나 계정이 변경되었습니다. 다시 연결해 주세요.')
                if not _is_search_url(page.url, root, keyword):
                    raise ExtractionError('요청한 상품 검색 화면이 아닙니다. 다시 검색해 주세요.')
                snapshot = page.evaluate(_EXTRACT)
                if not _is_search_url(page.url, root, keyword):
                    raise ExtractionError('상품 검색 도중 화면이 변경되었습니다. 다시 검색해 주세요.')
                text = snapshot.get('text', '')
                # Do not confuse the capped 상품 tab label with total results.
                found_total = _number(r'(?:전체|검색\s*결과|총)\s*(?:총\s*)?([\d,]+)\s*(?:개|건)', text, int)
                if found_total is not None:
                    total = found_total
                before = len(seen)
                for card in snapshot.get('cards', []):
                    item = parse_card(card, root, len(seen) + 1)
                    if item and item.id not in seen:
                        seen[item.id] = item
                    if len(seen) >= max_items:
                        break
                if len(seen) >= max_items or (total is not None and len(seen) >= total):
                    break
                stalled = stalled + 1 if len(seen) == before and snapshot == last_snapshot else 0
                last_snapshot = snapshot
                if stalled >= 4:
                    break
                page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                page.wait_for_timeout(750)
            if not seen:
                if re.search(r'검색\s*결과가?\s*없|검색된\s*상품이?\s*없|결과\s*없음', text):
                    self._save_session(context)
                    return SearchResult([], 0 if total is None else total, 0)
                raise ExtractionError('상품 카드를 읽지 못했습니다. 로그인 상태와 검색 화면의 변경 여부를 확인하세요.')
            warning = '제휴 링크는 확인되지 않았습니다. 수집된 상품 범위에서만 정렬됩니다.'
            if any(item.price is None for item in seen.values()):
                warning += ' 일부 상품의 가격은 확인되지 않았습니다.'
            if total is None or len(seen) < total:
                warning += ' 추가 로딩 완료 여부는 확인되지 않았습니다.'
            products = sort_products(list(seen.values()), sort)
            self._save_session(context)
            return SearchResult(products, total, len(products), warning)
        except ShoppingError:
            raise
        except Exception as exc:
            raise NetworkError('상품 검색 중 네트워크 또는 브라우저 오류가 발생했습니다.') from exc
        finally:
            try:
                context.close()
            finally:
                pw.stop()
