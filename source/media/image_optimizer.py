"""Prepare separate, verified local images and a Markdown copy for publishing."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import uuid
from pathlib import Path
from urllib.parse import unquote

from PIL import Image, ImageFilter, ImageOps

MAX_WIDTH = 1200
TARGET_BYTES = 300 * 1024
IMAGE_REF = re.compile(r'!\[([^\]\n]*)\]\(([^)\n]+)\)')
IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif', '.tif', '.tiff'}
TEXT_HINTS = ('스크린샷', '텍스트', '차트', '그래프', '도표', '인포그래픽',
              'screenshot', 'chart', 'diagram', 'infographic', 'text')


class ImageOptimizationError(Exception):
    pass


def _graphic_like(image: Image.Image, hint: str) -> bool:
    """Conservatively preserve graphics; this is not a guaranteed text detector."""
    if any(word in hint.lower() for word in TEXT_HINTS):
        return True
    sample = image.convert('RGB')
    sample.thumbnail((256, 256), Image.Resampling.LANCZOS)
    if sample.getcolors(maxcolors=256) is not None:
        return True
    edges = sample.convert('L').filter(ImageFilter.FIND_EDGES).histogram()
    total = sum(edges) or 1
    return sum(edges[:6]) / total > 0.78 and sum(edges[96:]) / total > 0.008


def _encode_image(source: Path, hint: str) -> tuple[bytes, dict]:
    try:
        original_bytes = source.stat().st_size
        with Image.open(source) as opened:
            if getattr(opened, 'n_frames', 1) != 1:
                raise ImageOptimizationError(f'움직이는 이미지는 자동 변환할 수 없습니다: {source.name}')
            original_size = opened.size
            original_format = opened.format or 'unknown'
            icc = opened.info.get('icc_profile')
            has_alpha = 'A' in opened.getbands() or 'transparency' in opened.info
            oriented = ImageOps.exif_transpose(opened)
            oriented.load()
            graphic = _graphic_like(oriented, hint)
            width, height = oriented.size
            if width > MAX_WIDTH:
                oriented = oriented.resize((MAX_WIDTH, max(1, round(height * MAX_WIDTH / width))), Image.Resampling.LANCZOS)
            png = has_alpha or graphic
            image = oriented.convert('RGBA' if has_alpha else 'RGB')
            qualities = (None,) if png else (85, 82, 80)
            encoded = b''
            quality = None
            for quality in qualities:
                buffer = io.BytesIO()
                kwargs = {'icc_profile': icc} if icc else {}
                if png:
                    image.save(buffer, format='PNG', optimize=True, **kwargs)
                else:
                    image.save(buffer, format='WEBP', quality=quality, method=6, **kwargs)
                encoded = buffer.getvalue()
                if len(encoded) <= TARGET_BYTES:
                    break
            with Image.open(io.BytesIO(encoded)) as check:
                check.load()
                if check.size != image.size or check.width > MAX_WIDTH:
                    raise ImageOptimizationError(f'최적화 이미지 크기 검증 실패: {source.name}')
                if has_alpha and 'A' not in check.getbands():
                    raise ImageOptimizationError(f'투명도 검증 실패: {source.name}')
            return encoded, {'original_dimensions': list(original_size), 'oriented_dimensions': [width, height],
                'optimized_dimensions': list(image.size), 'original_bytes': original_bytes,
                'optimized_bytes': len(encoded), 'original_format': original_format,
                'optimized_format': 'PNG' if png else 'WEBP', 'quality': quality,
                'transparent': has_alpha, 'graphic_heuristic': graphic,
                'compression_percent': round((1 - len(encoded) / original_bytes) * 100, 2) if original_bytes else None,
                'under_300kb': len(encoded) <= TARGET_BYTES, 'success': True}
    except ImageOptimizationError:
        raise
    except (OSError, ValueError, SyntaxError) as exc:
        raise ImageOptimizationError(f'이미지를 분석하거나 저장하지 못했습니다: {source.name}') from exc


def prepare_publish_images(md_path: Path, work_dir: Path, cancel_event=None) -> tuple[Path, Path | None]:
    """Leave originals untouched; return a new Markdown path and a JSON report."""
    text = md_path.read_text(encoding='utf-8')
    matches = list(IMAGE_REF.finditer(text))
    refs: dict[Path, list[str]] = {}
    for match in matches:
        raw = match.group(2).strip()
        if raw.lower().startswith(('http://', 'https://')):
            continue
        if raw.lower().startswith('data:'):
            raise ImageOptimizationError('data URL 이미지는 로컬 파일로 첨부해야 합니다.')
        path = Path(unquote(raw))
        source = (path if path.is_absolute() else md_path.parent / path).resolve()
        if not source.is_file():
            raise ImageOptimizationError(f'본문 이미지 파일을 찾을 수 없습니다: {source.name}')
        refs.setdefault(source, []).append(match.group(1))

    root = work_dir.resolve()
    images_dir = root / 'images'
    if images_dir.is_dir():
        for source in images_dir.rglob('*'):
            if source.is_file() and source.suffix.lower() in IMAGE_SUFFIXES and 'optimized' not in source.relative_to(images_dir).parts:
                refs.setdefault(source.resolve(), [])
    if not refs:
        return md_path, None
    dest_dir = images_dir / 'optimized'
    out_dir = root / 'output'
    if not dest_dir.resolve().is_relative_to(root) or not out_dir.resolve().is_relative_to(root):
        raise ImageOptimizationError('최적화 저장 경로가 작업 폴더 밖을 가리킵니다.')
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    optimized: dict[Path, Path] = {}
    report = []
    for source, hints in refs.items():
        if cancel_event is not None and cancel_event.is_set():
            raise ImageOptimizationError('이미지 최적화가 취소되었습니다.')
        encoded, details = _encode_image(source, ' '.join(hints) + ' ' + source.stem)
        digest = hashlib.sha256(encoded).hexdigest()[:16]
        extension = '.png' if details['optimized_format'] == 'PNG' else '.webp'
        target = dest_dir / f'{source.stem[:50]}-{digest}{extension}'
        if target.exists():
            if target.is_symlink() or target.read_bytes() != encoded:
                raise ImageOptimizationError(f'최적화 파일 이름이 충돌합니다: {target.name}')
        else:
            with target.open('xb') as stream:
                stream.write(encoded)
        with Image.open(target) as check:
            check.verify()
        if target.stat().st_size != len(encoded):
            raise ImageOptimizationError(f'최적화 파일 저장 검증 실패: {target.name}')
        optimized[source] = target
        report.append({'original': str(source), 'optimized': str(target), **details})

    def rewrite(match: re.Match) -> str:
        raw = match.group(2).strip()
        if raw.lower().startswith(('http://', 'https://')):
            return match.group(0)
        path = Path(unquote(raw))
        source = (path if path.is_absolute() else md_path.parent / path).resolve()
        relative = Path(os.path.relpath(optimized[source], out_dir)).as_posix()
        return f'![{match.group(1)}]({relative})'

    publish_text = IMAGE_REF.sub(rewrite, text)
    token = uuid.uuid4().hex
    publish_md = out_dir / f'publish-optimized-{token}.md'
    report_path = out_dir / f'image-optimization-{token}.json'
    publish_md.write_text(publish_text, encoding='utf-8')
    report_path.write_text(json.dumps({'images': report}, ensure_ascii=False, indent=2), encoding='utf-8')
    return publish_md, report_path
