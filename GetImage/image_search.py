"""한국관광공사 TourAPI(국문 관광정보 서비스) searchKeyword2로 키워드 관련 이미지를 찾아
다운로드하는 모듈(data.go.kr publicDataPk=15101578).

기존 AI 실사 이미지 생성 기능(image_fetch.py, pipeline.run_generation)과는 별개의
경로다 — 이쪽은 AI가 웹 검색으로 찾은 URL이 아니라, 사용자가 입력한 키워드로 공공 API를
직접 호출해 이미지를 찾는다. 정사각형 크롭·저장은 기존 image_fetch.download_and_crop_square를
그대로 재사용한다(로직 중복 방지).
"""

import logging
import re
from pathlib import Path
from urllib.parse import urlencode

import requests

import image_fetch

logger = logging.getLogger(__name__)

_ENV_PATH = Path(__file__).resolve().parent / ".env"
_SERVICE_KEY_NAME = "DATA_GO_KR"
_SEARCH_URL = "https://apis.data.go.kr/B551011/KorService2/searchKeyword2"
_REQUEST_TIMEOUT_SEC = 10
_CACHE_DIR = Path(__file__).resolve().parent / "_cache"


def _load_service_key() -> str:
    """GetImage/.env에서 DATA_GO_KR 서비스키를 읽는다.

    이 파일은 "KEY : VALUE"(콜론 구분) 형식으로 저장돼 있어 표준 KEY=VALUE .env 포맷을
    가정하는 python-dotenv로는 읽을 수 없으므로 직접 파싱한다. 값은 이미 URL 인코딩된
    "Encoding" 키이므로(끝에 %3D%3D 등 포함) 그대로 쿼리스트링에 붙여 써야 하며,
    requests의 params=에 넘기면 이중 인코딩되어 인증에 실패한다.
    """
    if not _ENV_PATH.exists():
        return ""
    for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        if _SERVICE_KEY_NAME not in line:
            continue
        sep_index = next((i for i, ch in enumerate(line) if ch in "=:"), -1)
        if sep_index == -1:
            continue
        return line[sep_index + 1 :].strip()
    return ""


def search_image_urls(keyword: str, count: int) -> list[str]:
    """키워드로 TourAPI를 검색해 대표 이미지(firstimage) URL을 최대 count개 반환한다.

    관광정보 DB 특성상 여행/관광과 무관한 키워드는 결과가 없을 수 있다 — 이 경우와
    요청 실패 시 모두 예외를 올리지 않고 빈 리스트를 반환해 호출부(파이프라인)가
    계속 진행되게 한다.
    """
    service_key = _load_service_key()
    keyword = keyword.strip()
    if not service_key or not keyword or count <= 0:
        return []

    query = {
        "MobileOS": "ETC",
        "MobileApp": "crawl_autowrite",
        "_type": "json",
        "keyword": keyword,
        "numOfRows": max(count * 3, 10),
        "pageNo": 1,
        "arrange": "O",  # 제목순 정렬 + 이미지 보유 항목 우선
    }
    url = f"{_SEARCH_URL}?{urlencode(query)}&serviceKey={service_key}"

    try:
        response = requests.get(url, timeout=_REQUEST_TIMEOUT_SEC)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("image_search.search_image_urls: 요청 실패 keyword=%s (%s)", keyword, exc)
        return []

    try:
        items = data["response"]["body"]["items"]
        item_list = items["item"] if items else []
        if isinstance(item_list, dict):
            item_list = [item_list]
    except (KeyError, TypeError) as exc:
        logger.warning("image_search.search_image_urls: 응답 형식 예외 keyword=%s (%s)", keyword, exc)
        return []

    urls = [item["firstimage"] for item in item_list if item.get("firstimage")]
    if not urls:
        logger.info("image_search.search_image_urls: 검색 결과 없음 keyword=%s", keyword)
    return urls[:count]


def _safe_filename_stem(keyword: str) -> str:
    stem = re.sub(r'[\\/:*?"<>|]', "", keyword)
    stem = re.sub(r"\s+", "_", stem.strip())
    return stem or "이미지검색"


def download_search_images(keyword: str, count: int, dest_dir: Path | None = None) -> list[Path]:
    """키워드로 이미지를 검색해 dest_dir(기본 GetImage/_cache)에 다운로드·정사각형 크롭한다.

    개별 이미지 다운로드 실패는 건너뛰고 나머지로 계속 진행한다(image_fetch와 동일 정책).
    성공적으로 저장된 파일 경로만 반환한다.
    """
    urls = search_image_urls(keyword, count)
    if not urls:
        return []

    dest_dir = dest_dir or _CACHE_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_filename_stem(keyword)

    saved: list[Path] = []
    for i, url in enumerate(urls, start=1):
        dest_path = dest_dir / f"{stem}_{i}.jpg"
        if image_fetch.download_and_crop_square(url, dest_path):
            saved.append(dest_path)
    return saved
