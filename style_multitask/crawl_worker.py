"""Run with the crawler interpreter; never writes into the legacy crawler result tree."""

import argparse
import json
import os
from pathlib import Path
import sys


def run(crawler_dir: Path, keyword: str, count: int, work_dir: Path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.path.insert(0, str(crawler_dir))
    import blogcontentsClawring as crawler_module  # type: ignore
    from bs4 import BeautifulSoup
    from style_multitask.profile import analyze_first_html

    crawler_module.load_project_dotenv()
    client_id = os.getenv("NAVER_CLIENT_ID", "").strip()
    client_secret = os.getenv("NAVER_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("네이버 검색 API 자격증명이 설정되지 않았습니다.")
    response = crawler_module.requests.get(
        "https://openapi.naver.com/v1/search/blog.json",
        headers={"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret},
        params={"query": keyword, "display": min(count, 100), "start": 1, "sort": "sim"},
        timeout=30,
    )
    response.raise_for_status()
    items = response.json().get("items", [])
    ranked = [(rank, item.get("link", "")) for rank, item in enumerate(items, 1)
              if "blog.naver.com" in item.get("link", "")][:count]
    if not ranked:
        raise RuntimeError("네이버 블로그 검색 결과가 없습니다.")

    crawler = crawler_module.NaverBlogCrawler()
    blog_dir = work_dir / "blog"
    blog_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"keyword": keyword, "search_sort": "sim", "sources": []}
    for filtered_rank, (raw_rank, url) in enumerate(ranked, 1):
        try:
            if filtered_rank == 1:
                inner_url = crawler.get_inner_frame_url(url)
                article_response = crawler.session.get(
                    inner_url,
                    headers=crawler_module._browser_headers(referer=url),
                    timeout=25,
                    allow_redirects=True,
                )
                article_response.raise_for_status()
                page_html = article_response.content.decode("utf-8", errors="replace")
                profile = analyze_first_html(page_html)
                soup = BeautifulSoup(page_html, "html.parser")
                title = crawler.extract_title(soup)
                content = profile.body_text
                manifest["first_profile"] = profile.public_metrics()
            else:
                title, content, _, _, _ = crawler.crawl_blog_post(url)
            path = blog_dir / f"{filtered_rank:02d}.txt"
            path.write_text(f"블로그 URL: {url}\n제목: {title}\n\n{content}", encoding="utf-8")
            manifest["sources"].append({
                "filtered_rank": filtered_rank,
                "search_rank_raw": raw_rank,
                "url": url,
                "txt": str(path),
            })
        except Exception as exc:
            if filtered_rank == 1:
                raise RuntimeError(f"첫 검색 블로그 분석 실패: {exc}") from exc
            manifest["sources"].append({
                "filtered_rank": filtered_rank,
                "search_rank_raw": raw_rank,
                "url": url,
                "error": str(exc),
            })
    (work_dir / "source_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--crawler-dir", type=Path, required=True)
    parser.add_argument("--keyword", required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args.crawler_dir, args.keyword, args.count, args.work_dir)
    except Exception as exc:
        args.work_dir.mkdir(parents=True, exist_ok=True)
        (args.work_dir / "crawl_error.txt").write_text(str(exc), encoding="utf-8")
        print(str(exc), file=sys.stderr)
        sys.exit(1)
