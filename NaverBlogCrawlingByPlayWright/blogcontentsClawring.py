#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
네이버 블로그 포스트 크롤링 스크립트

이 스크립트는 네이버 블로그 포스트 URL을 입력받아 제목과 본문을 추출하여
UTF-8 텍스트 파일로 저장합니다.

또는 키워드와 크롤링할 갯수를 입력받아 자동으로 블로그를 검색하고 크롤링합니다.

필요 패키지 설치:
    pip install -r requirements.txt

브라우저 모드(--mode browser)는 selenium, undetected-chromedriver가 추가로 필요합니다.

이 도구의 이용은 각 서비스 이용약관·robots.txt 및 관련 법령을 준수할 책임이 사용자에게 있습니다.
"""

import argparse
import os
import re
import sys
import time
import random
from urllib.parse import urljoin, urlparse
from datetime import datetime
from typing import Optional, Tuple, List, Dict, Any
import requests
from bs4 import BeautifulSoup

# 실제 브라우저처럼 보이기 위한 User-Agent 목록 (최신 Chrome/Edge)
_USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
]


def get_base_dir() -> str:
    """
    exe/스크립트가 있는 기본 디렉토리 반환.
    PyInstaller로 빌드된 exe의 경우 exe 파일 위치, 스크립트 실행 시 스크립트 위치.
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def load_project_dotenv() -> bool:
    """
    프로젝트 루트(스크립트 또는 exe와 같은 폴더)의 .env 를 로드한다.
    현재 작업 디렉터리와 무관하게 API 키 등을 읽을 수 있다.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    env_path = os.path.join(get_base_dir(), ".env")
    return load_dotenv(env_path)


def _safe_folder_name(name: str) -> str:
    """폴더명으로 사용할 수 없는 문자 제거"""
    name = re.sub(r'[\/:*?"<>|]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name if name else "제목없음"


def get_result_dir(date_included: bool = True, keyword: Optional[str] = None) -> str:
    """
    크롤링 결과 저장 폴더 경로 반환.
    result/YYYYMMDD 또는 result/YYYYMMDD/YYYYMMDD_keyword 형식.
    """
    base_dir = get_base_dir()
    result_base = os.path.join(base_dir, "result")
    if not date_included:
        return result_base
    date_str = datetime.now().strftime("%Y%m%d")
    date_dir = os.path.join(result_base, date_str)
    if keyword:
        safe_keyword = _safe_folder_name(keyword)
        folder_name = f"{date_str}_{safe_keyword}"
        return os.path.join(date_dir, folder_name)
    return date_dir


def _random_delay(min_sec: float = 1.5, max_sec: float = 4.0):
    """요청 간 사람처럼 불규칙한 대기 (탐지 회피)"""
    time.sleep(random.uniform(min_sec, max_sec))


def _browser_headers(referer: Optional[str] = None) -> Dict[str, str]:
    """실제 Chrome 브라우저와 동일한 헤더 생성 (매 요청마다 UA 랜덤)"""
    ua = random.choice(_USER_AGENTS)
    h = {
        'User-Agent': ua,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none' if not referer else 'cross-site',
        'Sec-Fetch-User': '?1',
        'Sec-Ch-Ua': '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Cache-Control': 'max-age=0',
        'DNT': '1',
        'Priority': 'u=0, i',
    }
    if referer:
        h['Referer'] = referer
    return h


def is_naver_logged_in() -> Tuple[bool, Optional[bool]]:
    """
    브라우저에 저장된 쿠키로 네이버 로그인 여부 확인.
    Returns:
        (check_succeeded, is_logged_in)
        - check_succeeded True: 확인 성공 → is_logged_in True면 로그인됨, False면 비로그인.
        - check_succeeded False: 확인 불가(쿠키 읽기 실패 등) → is_logged_in은 None.
    """
    try:
        import browser_cookie3
    except ImportError:
        return (False, None)

    cookies = None
    for loader, domain in [
        (browser_cookie3.chrome, ".naver.com"),
        (browser_cookie3.edge, ".naver.com"),
        (browser_cookie3.firefox, ".naver.com"),
    ]:
        try:
            cookies = loader(domain_name=domain)
            if cookies:
                break
        except Exception:
            continue

    # Whale 브라우저 지원 추가 (Chromium 기반)
    if not cookies:
        try:
            # Whale 브라우저의 쿠키 파일 경로 직접 지정
            import os
            whale_path = os.path.expanduser("~/AppData/Local/Naver/Naver Whale/User Data")
            if os.path.exists(whale_path):
                cookies = browser_cookie3.chromium(
                    domain_name=".naver.com",
                    cookie_file=os.path.join(whale_path, "Default", "Cookies")
                )
        except Exception:
            pass

    if not cookies:
        return (False, None)

    try:
        resp = requests.get(
            "https://www.naver.com",
            cookies=cookies,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ko-KR,ko;q=0.9",
            },
            timeout=10,
        )
        resp.raise_for_status()
        text = resp.text
        # 로그인 시에만 보이는 요소로 판단
        if "로그아웃" in text or "내정보" in text or "nv_mypage" in text:
            return (True, True)
        return (True, False)
    except Exception:
        return (False, None)


class NaverBlogCrawler:
    """네이버 블로그 크롤러 클래스 (브라우저 행동 모방으로 탐지 회피)"""

    def __init__(self):
        self.session = requests.Session()
        # 기본 UA만 설정, 실제 요청 시 _browser_headers()로 매번 갱신
        self.session.headers.update(_browser_headers())

    def parse_blog_url(self, post_url: str) -> Tuple[str, str]:
        """
        블로그 URL에서 blogId와 logNo를 추출
        예: https://blog.naver.com/jjpapa1107/224149729792
        """
        # URL 파싱
        parsed = urlparse(post_url)

        # 경로에서 blogId/logNo 추출
        path_parts = parsed.path.strip('/').split('/')
        if len(path_parts) >= 2:
            blog_id = path_parts[0]
            log_no = path_parts[1]
            return blog_id, log_no

        raise ValueError(f"올바른 네이버 블로그 URL 형식이 아닙니다: {post_url}")

    def get_inner_frame_url(self, post_url: str) -> str:
        """
        메인 페이지에서 iframe#mainFrame의 src를 찾아 실제 컨텐츠 URL 생성
        (탐지 회피: 랜덤 대기, 네이버 검색에서 들어온 것처럼 Referer 설정)
        """
        try:
            _random_delay(1.2, 3.5)
            headers = _browser_headers(referer='https://www.naver.com/')
            response = self.session.get(
                post_url,
                headers=headers,
                timeout=random.randint(12, 20),
                allow_redirects=True,
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')

            # iframe#mainFrame 찾기
            iframe = soup.find('iframe', id='mainFrame')
            if not iframe:
                raise ValueError("iframe#mainFrame을 찾을 수 없습니다")

            src = iframe.get('src')
            if not src:
                raise ValueError("iframe#mainFrame에 src 속성이 없습니다")

            # 상대 URL이면 절대 URL로 변환
            inner_url = urljoin(post_url, src)
            return inner_url

        except Exception as e:
            raise Exception(f"iframe URL 추출 실패: {e}")

    def extract_title(self, soup: BeautifulSoup) -> str:
        """
        제목 추출 (여러 방법 시도)
        """
        # 방법 1: 구조화된 제목 선택자
        title_selectors = [
            '.se-component.se-documentTitle .se-title-text span',
            '.se-component.se-documentTitle span',
            '.se-title-text span',
            'h3.se_textarea',
        ]

        for selector in title_selectors:
            title_elem = soup.select_one(selector)
            if title_elem:
                title = title_elem.get_text(strip=True)
                if title:
                    return title

        # 방법 2: og:title 메타 태그
        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            return og_title['content'].strip()

        # 방법 3: 일반 title 태그
        title_tag = soup.find('title')
        if title_tag:
            title = title_tag.get_text(strip=True)
            # 네이버 블로그 특유의 접미사 제거
            title = re.sub(r'\s*[:|]\s*네이버 블로그$', '', title)
            return title

        return "제목 없음"

    def extract_blogger_info(self, soup: BeautifulSoup) -> Tuple[str, str]:
        """
        블로거명과 작성날짜 추출
        return: (blogger_name, publish_date)
        """
        blogger_name = "알 수 없음"
        publish_date = "알 수 없음"

        # 블로거명 추출
        blogger_selectors = [
            '.nick',
            '.blogger_name',
            '.pcol1',
            '.author',
            '.writer',
            '.se_author',
            '.blog_author'
        ]

        for selector in blogger_selectors:
            blogger_elem = soup.select_one(selector)
            if blogger_elem:
                name = blogger_elem.get_text(strip=True)
                if name and len(name) > 0:
                    blogger_name = name
                    break

        # 작성날짜 추출
        date_selectors = [
            '.se_publishDate',
            '.date',
            '.se_date',
            '.publish_date',
            '.post_date',
            '.blog_date',
            'time',
            '.date-fil3'
        ]

        for selector in date_selectors:
            date_elem = soup.select_one(selector)
            if date_elem:
                # datetime 속성 확인
                if date_elem.get('datetime'):
                    publish_date = date_elem['datetime']
                    break
                # 텍스트 내용 확인
                date_text = date_elem.get_text(strip=True)
                if date_text and len(date_text) > 0:
                    publish_date = date_text
                    break

        return blogger_name, publish_date

    def extract_content(self, soup: BeautifulSoup) -> str:
        """
        본문 내용 추출
        """
        content_parts = []

        # 메인 컨테이너 찾기
        main_container = soup.select_one('.se-main-container')
        if not main_container:
            # 대안 선택자들
            main_container = soup.select_one('.post_ct') or soup.select_one('#postViewArea') or soup
            if not main_container:
                return "본문 내용을 찾을 수 없습니다"

        # 텍스트 요소들 수집
        text_selectors = [
            'p.se-text-paragraph',
            'li.se-text-list-item',
            '.se-component.se-quotation',
            '.se-text-paragraph',
            '.se-text',
            'p',
        ]

        for selector in text_selectors:
            elements = main_container.select(selector)
            for elem in elements:
                text = elem.get_text(strip=True)
                if text and len(text) > 1:  # 너무 짧은 텍스트 제외
                    content_parts.append(text)

        # 중복 제거 및 정리
        seen = set()
        unique_parts = []
        for part in content_parts:
            if part not in seen:
                seen.add(part)
                unique_parts.append(part)

        return '\n\n'.join(unique_parts) if unique_parts else "본문 내용이 없습니다"

    def _is_advertisement(self, text: str) -> bool:
        """
        광고성 텍스트인지 판별
        """
        ad_keywords = [
            '광고', '협찬', '후원', '스폰서', 'PR', '프로모션',
            '공감', '댓글', '공유', '좋아요', '팔로우',
            '이 글은', '이 포스트는', '블로그 운영정책',
            '네이버 블로그', '블로그 마켓'
        ]

        text_lower = text.lower()
        return any(keyword in text_lower for keyword in ad_keywords)

    def _sanitize_filename(self, filename: str) -> str:
        """
        파일명으로 사용할 수 없는 문자들을 제거하거나 안전한 문자로 변환
        """
        import re

        # 파일명으로 사용할 수 없는 문자들 제거 또는 변환
        # \ / : * ? " < > | 와 같은 문자들
        filename = re.sub(r'[\/:*?"<>|]', '', filename)

        # 연속된 공백을 하나의 공백으로 변환
        filename = re.sub(r'\s+', ' ', filename)

        # 앞뒤 공백 제거
        filename = filename.strip()

        # 빈 문자열인 경우 기본값 설정
        if not filename:
            filename = "제목없음"

        # 파일명이 너무 긴 경우 자르기 (Windows 파일명 길이 제한 고려)
        if len(filename) > 100:
            filename = filename[:100].strip()

        return filename

    def crawl_blog_post(self, post_url: str) -> Tuple[str, str, str, str, str]:
        """
        블로그 포스트 크롤링
        return: (title, content, filename, blogger_name, publish_date)
        """
        try:
            # URL에서 blogId와 logNo 추출
            blog_id, log_no = self.parse_blog_url(post_url)

            # iframe을 통한 실제 컨텐츠 URL 가져오기
            inner_url = self.get_inner_frame_url(post_url)
            print(f"실제 컨텐츠 URL: {inner_url}")

            # 블로그 글에서 iframe으로 넘어가는 것처럼 Referer 설정, 요청 간 대기
            _random_delay(0.8, 2.5)
            headers = _browser_headers(referer=post_url)
            response = self.session.get(
                inner_url,
                headers=headers,
                timeout=random.randint(12, 22),
                allow_redirects=True,
            )
            response.raise_for_status()

            # HTML 파싱
            soup = BeautifulSoup(response.content, 'html.parser', from_encoding='utf-8')

            # 제목 추출
            title = self.extract_title(soup)
            print(f"추출된 제목: {title}")

            # 블로거 정보 추출
            blogger_name, publish_date = self.extract_blogger_info(soup)
            print(f"블로거: {blogger_name}, 작성일: {publish_date}")

            # 본문 추출
            content = self.extract_content(soup)
            print(f"본문 길이: {len(content)}자")

            # 파일명 생성 (제목_날짜시간 형식)
            now = datetime.now().strftime('%Y%m%d_%H%M%S')
            # 제목에서 파일명으로 사용할 수 없는 문자들 제거
            safe_title = self._sanitize_filename(title)
            filename = f"{safe_title}_{now}.txt"

            return title, content, filename, blogger_name, publish_date

        except Exception as e:
            raise Exception(f"블로그 크롤링 실패: {e}")

    def save_to_file(self, title: str, content: str, filename: str, url: str = "", blogger_name: str = "", publish_date: str = "", output_dir: str = None) -> str:
        """
        추출된 내용을 파일로 저장
        """
        # 기본 출력 디렉토리: exe/스크립트 위치/result/날짜(YYYYMMDD)
        if output_dir is None:
            output_dir = get_result_dir(date_included=True)

        # 출력 디렉토리 생성
        os.makedirs(output_dir, exist_ok=True)

        # 전체 파일 경로
        filepath = os.path.join(output_dir, filename)

        # 내용 포맷팅
        full_content = f"블로그 URL: {url}\n"
        if blogger_name and blogger_name != "알 수 없음":
            full_content += f"블로거: {blogger_name}\n"
        if publish_date and publish_date != "알 수 없음":
            full_content += f"작성일: {publish_date}\n"
        full_content += f"\n{title}\n\n{content}"

        # UTF-8로 저장
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(full_content)

        print(f"파일 저장 완료: {filepath}")
        return filepath


def _uc_modifier_key():
    """Ctrl+A: Windows/Linux Control, macOS Command."""
    from selenium.webdriver.common.keys import Keys
    return Keys.COMMAND if sys.platform == "darwin" else Keys.CONTROL


def _create_uc_driver(headless: bool = False):
    """
    undetected-chromedriver 단일 인스턴스.
    배치 시 한 드라이버로 여러 URL을 순회(시작·종료 오버헤드·탐지 패턴 완화).
    """
    import undetected_chromedriver as uc
    opts = uc.ChromeOptions()
    return uc.Chrome(options=opts, use_subprocess=True, headless=headless)


def crawl_blog_post_browser(
    driver: Any,
    crawler: "NaverBlogCrawler",
    post_url: str,
    frame_wait_sec: int = 45,
) -> Tuple[str, str, str, str, str]:
    """
    브라우저에서 포스트 URL을 연 뒤 iframe#mainFrame에서 body 포커스 → Ctrl+A →
    Selection 문자열(비면 innerText 폴백). 메타는 동일 HTML로 BeautifulSoup 파싱 재사용.
    return: (title, content, filename, blogger_name, publish_date)
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException

    crawler.parse_blog_url(post_url)
    wait = WebDriverWait(driver, frame_wait_sec)

    driver.switch_to.default_content()
    driver.get(post_url)

    try:
        wait.until(EC.frame_to_be_available_and_switch_to_it((By.ID, "mainFrame")))
    except TimeoutException:
        pass

    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    body = driver.find_element(By.TAG_NAME, "body")
    body.click()
    mod = _uc_modifier_key()
    body.send_keys(mod, "a")
    time.sleep(0.25)

    selected = driver.execute_script("return window.getSelection().toString();") or ""
    content = selected.strip()
    if not content:
        content = (
            driver.execute_script(
                "return document.body ? (document.body.innerText || '') : '';"
            )
            or ""
        ).strip()
    if not content:
        content = "본문 내용이 없습니다"

    soup = BeautifulSoup(driver.page_source, "html.parser")
    title = crawler.extract_title(soup)
    blogger_name, publish_date = crawler.extract_blogger_info(soup)

    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_title = crawler._sanitize_filename(title)
    filename = f"{safe_title}_{now}.txt"

    driver.switch_to.default_content()
    return title, content, filename, blogger_name, publish_date


def get_blog_posts_by_keyword(keyword: str, count: int) -> List[str]:
    """
    키워드로 블로그 검색하여 포스트 URL들 반환
    """
    try:
        # 환경변수 로드
        import os
        load_project_dotenv()

        client_id = os.getenv("NAVER_CLIENT_ID", "").strip()
        client_secret = os.getenv("NAVER_CLIENT_SECRET", "").strip()

        if not client_id or not client_secret:
            raise RuntimeError("NAVER_CLIENT_ID와 NAVER_CLIENT_SECRET 환경변수가 필요합니다.")

        # 네이버 검색 API로 블로그 글 검색
        search_url = "https://openapi.naver.com/v1/search/blog.json"
        headers = {
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret
        }

        params = {
            "query": keyword,
            "display": min(count, 100),  # 최대 10개로 제한 (네이버 API 제한)
            "start": 1,
            "sort": "sim"  # 정확도순 정렬
        }

        response = requests.get(search_url, headers=headers, params=params)
        response.raise_for_status()

        search_results = response.json()

        post_urls = []
        if "items" in search_results:
            for item in search_results["items"]:
                post_url = item.get("link", "")
                if post_url and "blog.naver.com" in post_url:
                    # 네이버 블로그 URL만 필터링
                    post_urls.append(post_url)

        return post_urls[:count]  # 요청한 갯수만큼 반환

    except Exception as e:
        raise RuntimeError(f"블로그 검색 실패: {e}")


def crawl_multiple_posts(
    post_urls: List[str],
    keyword: str = None,
    mode: str = "http",
    headless: bool = False,
) -> Tuple[List[str], str]:
    """
    여러 개의 블로그 포스트를 크롤링
    keyword: 검색 키워드 (지정시 키워드 폴더에 저장)
    mode: "http" (requests+BeautifulSoup) 또는 "browser" (undetected-chromedriver)

    Returns:
        (저장에 성공한 txt 파일 절대경로 목록, 출력 디렉토리)
    """
    crawler = NaverBlogCrawler()
    success_count = 0
    fail_count = 0
    saved_paths: List[str] = []

    # 출력 디렉토리: exe/스크립트 위치/result/날짜(YYYYMMDD)/YYYYMMDD_키워드
    output_dir = get_result_dir(date_included=True, keyword=keyword)

    print(f"\n총 {len(post_urls)}개의 블로그 포스트를 크롤링합니다... (모드: {mode})")
    print(f"저장 폴더: {output_dir}")
    print("-" * 60)

    # 첫 요청 전 짧은 대기 (즉시 연속 요청 패턴 회피)
    _random_delay(1.0, 3.0)

    if mode == "browser":
        driver = _create_uc_driver(headless=headless)
        try:
            for i, post_url in enumerate(post_urls, 1):
                try:
                    if i > 1:
                        delay = random.uniform(3.0, 8.0)
                        print(f"[POST_DELAY] 다음 글까지 {delay:.2f}초 대기 중...")
                        time.sleep(delay)

                    print(f"\n[{i}/{len(post_urls)}] 크롤링 시작 (browser): {post_url}")
                    title, content, filename, blogger_name, publish_date = crawl_blog_post_browser(
                        driver, crawler, post_url
                    )
                    print(f"추출된 제목: {title}")
                    print(f"본문 길이: {len(content)}자")
                    saved_path = crawler.save_to_file(
                        title, content, filename, post_url, blogger_name, publish_date, output_dir
                    )
                    saved_paths.append(saved_path)
                    print(f"✓ 성공: {saved_path}")
                    success_count += 1
                except Exception as e:
                    print(f"✗ 실패: {e}")
                    fail_count += 1
                    try:
                        driver.switch_to.default_content()
                    except Exception:
                        pass
        finally:
            driver.quit()
    else:
        for i, post_url in enumerate(post_urls, 1):
            try:
                if i > 1:
                    delay = random.uniform(3.0, 8.0)
                    print(f"[POST_DELAY] 다음 글까지 {delay:.2f}초 대기 중...")
                    time.sleep(delay)

                print(f"\n[{i}/{len(post_urls)}] 크롤링 시작: {post_url}")

                title, content, filename, blogger_name, publish_date = crawler.crawl_blog_post(post_url)

                saved_path = crawler.save_to_file(
                    title, content, filename, post_url, blogger_name, publish_date, output_dir
                )
                saved_paths.append(saved_path)

                print(f"✓ 성공: {saved_path}")
                success_count += 1

            except Exception as e:
                print(f"✗ 실패: {e}")
                fail_count += 1

    print("\n" + "="*60)
    print("크롤링 완료!")
    print(f"성공: {success_count}개")
    print(f"실패: {fail_count}개")
    if success_count > 0:
        print(f"저장 위치: {output_dir}")
    print("="*60)

    return saved_paths, output_dir


def get_user_input() -> Tuple[str, int]:
    """
    사용자 입력 받기 (키워드와 크롤링할 갯수)
    """
    print("네이버 블로그 크롤링 도구")
    print("="*50)

    # 키워드 입력
    print("\n📝 검색할 키워드를 입력하세요")
    print("예: 오키나와 여행, 도쿄 맛집, 후쿠오카 관광지")
    while True:
        try:
            keyword = input("\n키워드 입력: ").strip()
            if keyword:
                print(f"✅ 선택된 키워드: '{keyword}'")
                break
            print("❌ 키워드를 입력해주세요.")
        except KeyboardInterrupt:
            print("\n\n👋 프로그램을 종료합니다.")
            sys.exit(0)

    # 갯수 입력
    print(f"\n🔢 '{keyword}' 키워드로 검색할 블로그 갯수를 입력하세요")
    print("권장: 3-5개 (너무 많으면 시간이 오래 걸릴 수 있습니다)")
    while True:
        try:
            count_input = input("\n갯수 입력 (1-100): ").strip()
            count = int(count_input)
            if 1 <= count <= 100:
                print(f"✅ 크롤링할 블로그 수: {count}개")
                break
            else:
                print("❌ 1에서 100 사이의 숫자를 입력해주세요.")
        except ValueError:
            print("❌ 올바른 숫자를 입력해주세요.")
        except KeyboardInterrupt:
            print("\n\n👋 프로그램을 종료합니다.")
            sys.exit(0)

    print(f"\n🚀 '{keyword}' 키워드로 {count}개의 블로그를 검색하고 크롤링을 시작합니다!")
    print("-" * 50)

    return keyword, count




def parse_args():
    p = argparse.ArgumentParser(description="네이버 블로그 크롤링")
    p.add_argument(
        "--mode",
        choices=["http", "browser"],
        default="http",
        help="http: requests+BeautifulSoup, browser: undetected-chromedriver+Selenium",
    )
    p.add_argument(
        "--url",
        type=str,
        default=None,
        help="단일 포스트 URL (지정 시 키워드/API 검색 생략)",
    )
    p.add_argument("--keyword", type=str, default=None, help="비대화식: 검색 키워드")
    p.add_argument("--count", type=int, default=None, help="비대화식: 크롤링할 개수 (1~100)")
    p.add_argument(
        "--headless",
        action="store_true",
        help="browser 모드에서 헤드리스 Chrome (기본은 창 표시)",
    )
    return p.parse_args()


def main():
    """메인 함수"""
    args = parse_args()
    try:
        if args.url:
            u = args.url.strip()
            if not u:
                print("❌ --url 값이 비어 있습니다.")
                return
            crawl_multiple_posts([u], keyword=None, mode=args.mode, headless=args.headless)
            return

        if args.keyword is not None and args.count is not None:
            if not (1 <= args.count <= 100):
                print("❌ --count는 1~100 사이여야 합니다.")
                return
            keyword = args.keyword.strip()
            if not keyword:
                print("❌ --keyword가 비어 있습니다.")
                return
            post_urls = get_blog_posts_by_keyword(keyword, args.count)
        else:
            keyword, count = get_user_input()
            post_urls = get_blog_posts_by_keyword(keyword, count)

        if not post_urls:
            print(f"\n❌ 검색된 블로그가 없습니다.")
            print("다른 키워드를 시도해보세요.")
            return

        print(f"\n🔍 검색된 블로그 목록:")
        for i, url in enumerate(post_urls, 1):
            print(f"  {i}. {url}")

        crawl_multiple_posts(post_urls, keyword=keyword, mode=args.mode, headless=args.headless)

    except KeyboardInterrupt:
        print("\n\n👋 프로그램을 종료합니다.")
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()