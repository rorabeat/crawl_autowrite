"""웹에서 실제 이미지를 다운로드해 정사각형(1:1)으로 크롭하는 모듈.

Claude 백엔드에는 codex의 $imagegen 같은 이미지 생성 도구가 없어(pipeline.py
_build_inline_image_instruction 참조), AI에게 이미지를 만들게 하는 대신 글에 어울리는
실제 이미지의 URL을 웹 검색으로 찾게 하고, 그 URL을 여기서 직접 다운로드해서 정사각형으로
잘라 저장한다(사용자 요청).
"""

import logging
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)

_REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; crawl_autowrite/1.0)"}
_REQUEST_TIMEOUT_SEC = 20


def download_and_crop_square(url: str, dest_path: Path) -> bool:
    """url의 이미지를 받아 중앙 기준 정사각형으로 크롭한 뒤 dest_path(JPEG)에 저장한다.

    핵심 피사체가 중앙에 있다고 가정하고 짧은 변 기준으로 중앙을 크롭한다(가로가 길면
    좌우를, 세로가 길면 상하를 잘라낸다). 다운로드/디코딩 실패는 예외를 올리지 않고
    False를 반환한다 — 이미지 하나가 실패해도 나머지 이미지·글 작성 자체는 계속 진행돼야
    하기 때문이다(호출부가 실패를 로그만 남기고 넘어가도록).
    """
    try:
        response = requests.get(url, headers=_REQUEST_HEADERS, timeout=_REQUEST_TIMEOUT_SEC)
        response.raise_for_status()
        image = Image.open(BytesIO(response.content))
        image = image.convert("RGB")
    except (requests.RequestException, UnidentifiedImageError, OSError, ValueError) as exc:
        logger.warning("image_fetch.download_and_crop_square: 실패 url=%s (%s)", url, exc)
        return False

    width, height = image.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    cropped = image.crop((left, top, left + side, top + side))

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    cropped.save(dest_path, "JPEG", quality=90)
    logger.info("image_fetch.download_and_crop_square: 저장됨 %s (원본 %dx%d → %dx%d)", dest_path, width, height, side, side)
    return True
