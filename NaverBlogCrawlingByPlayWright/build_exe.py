#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PyInstaller로 blogCrawlingUI.py를 단일 exe로 빌드하고,
배포 폴더에 .env, 지침, 설정 파일을 함께 둡니다.

출력: release/<프로젝트폴더명>/<프로젝트폴더명>.exe + 동봉 파일
실행 시 exe와 같은 폴더의 .env, ai_system_prompt.txt, ai_guidelines.txt, gui_preferences.json 등을 사용합니다.
"""

import os
import re
import shutil
import sys
from datetime import datetime
from typing import List, Tuple


def get_version_from_source(script_dir: str) -> str:
    """blogCrawlingUI.py에서 __version__ 값을 추출"""
    source_path = os.path.join(script_dir, "blogCrawlingUI.py")
    with open(source_path, "r", encoding="utf-8") as f:
        content = f.read()
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
    return match.group(1) if match else "0.0.0"


def normalize_windows_version(version: str) -> Tuple[int, int, int, int]:
    """PyInstaller version-file용 4자리 정수 버전으로 정규화."""
    nums = [int(part) for part in re.findall(r"\d+", version)]
    nums = (nums + [0, 0, 0, 0])[:4]
    return tuple(nums)  # type: ignore[return-value]


def create_version_info_file(build_dir: str, project_name: str, version: str) -> str:
    """Windows 파일 속성에 들어갈 version info 파일을 생성."""
    filevers = normalize_windows_version(version)
    copyright_year = datetime.now().year
    version_info = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={filevers},
    prodvers={filevers},
    mask=0x3f,
    flags=0x0,
    OS=0x4,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'040904B0',
        [
          StringStruct(u'CompanyName', u'NaverKeyword'),
          StringStruct(u'FileDescription', u'네이버 블로그 크롤링 GUI 도구'),
          StringStruct(u'FileVersion', u'{version}'),
          StringStruct(u'InternalName', u'{project_name}'),
          StringStruct(u'LegalCopyright', u'Copyright (C) {copyright_year}'),
          StringStruct(u'OriginalFilename', u'{project_name}.exe'),
          StringStruct(u'ProductName', u'네이버 블로그 크롤링 도구'),
          StringStruct(u'ProductVersion', u'{version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"""
    version_info_path = os.path.join(build_dir, "version_info.generated.txt")
    with open(version_info_path, "w", encoding="utf-8") as f:
        f.write(version_info)
    return version_info_path


def copy_bundle_files(project_root: str, bundle_dir: str) -> None:
    """
    exe와 같은 폴더에 바로 쓸 수 있도록 설정, 지침 파일을 복사합니다.
    - .env: 프로젝트에 있으면 복사, 없으면 .env.example 에서 .env 생성
    - .env.example, ai_system_prompt.txt, ai_guidelines.txt, gui_preferences.json, api_usage_stats.json: 있으면 복사
    """
    optional_same_name = (
        ".env.example",
        "ai_system_prompt.txt",
        "ai_guidelines.txt",
        "gui_preferences.json",
        "api_usage_stats.json",
    )
    for name in optional_same_name:
        src = os.path.join(project_root, name)
        if os.path.isfile(src):
            dst = os.path.join(bundle_dir, name)
            shutil.copy2(src, dst)
            print(f"  + {name}")

    env_src = os.path.join(project_root, ".env")
    env_dst = os.path.join(bundle_dir, ".env")
    if os.path.isfile(env_src):
        shutil.copy2(env_src, env_dst)
        print("  + .env (프로젝트 원본)")
    elif not os.path.isfile(env_dst):
        ex = os.path.join(project_root, ".env.example")
        if os.path.isfile(ex):
            shutil.copy2(ex, env_dst)
            print("  + .env <- .env.example 복사 (API 키 등을 채워 주세요)")
        else:
            print("  ! .env / .env.example 없음 - API 키는 직접 .env 를 만들어 주세요")

    if not os.path.isfile(os.path.join(bundle_dir, "ai_system_prompt.txt")):
        empty_sp = os.path.join(bundle_dir, "ai_system_prompt.txt")
        with open(empty_sp, "w", encoding="utf-8") as f:
            f.write("")
        print("  + ai_system_prompt.txt (빈 파일 생성)")
    if not os.path.isfile(os.path.join(bundle_dir, "ai_guidelines.txt")):
        empty = os.path.join(bundle_dir, "ai_guidelines.txt")
        with open(empty, "w", encoding="utf-8") as f:
            f.write("")
        print("  + ai_guidelines.txt (빈 파일 생성)")

    result_dir = os.path.join(bundle_dir, "result")
    os.makedirs(result_dir, exist_ok=True)
    print("  + result/ (폴더 생성)")


def list_legacy_release_exes(release_root: str) -> List[str]:
    """release 루트에 남아 있는 예전 exe 목록."""
    if not os.path.isdir(release_root):
        return []
    return sorted(
        entry.name
        for entry in os.scandir(release_root)
        if entry.is_file() and entry.name.lower().endswith(".exe")
    )


def write_latest_build_hint(
    release_root: str,
    bundle_dir: str,
    exe_path: str,
    version: str,
) -> str:
    """release 루트에 최신 실행 위치 안내 파일 생성."""
    hint_path = os.path.join(release_root, "LATEST_BUILD.txt")
    rel_bundle_dir = os.path.relpath(bundle_dir, release_root)
    rel_exe_path = os.path.relpath(exe_path, release_root)
    body = (
        "최신 빌드 안내\n"
        "================\n\n"
        f"앱 버전: {version}\n"
        f"실행 파일: {rel_exe_path}\n"
        f"배포 폴더: {rel_bundle_dir}\n\n"
        "주의: release 폴더 루트에 남아 있는 예전 exe는 최신 버전이 아닐 수 있습니다.\n"
        "반드시 위 경로의 exe를 실행하세요.\n"
    )
    with open(hint_path, "w", encoding="utf-8") as f:
        f.write(body)
    return hint_path


def main() -> int:
    print("네이버 블로그 크롤링 GUI - exe 배포 패키지 빌드")
    print("=" * 50)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    project_name = os.path.basename(os.path.normpath(script_dir))
    print(f"프로젝트명(배포 폴더, exe 이름): {project_name}")
    print(f"작업 디렉터리: {script_dir}")

    try:
        import PyInstaller.__main__
    except ImportError:
        import subprocess

        print("PyInstaller 설치 중...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
        import PyInstaller.__main__

    version = get_version_from_source(script_dir)
    today = datetime.now().strftime("%Y%m%d")

    release_root = os.path.join(script_dir, "release")
    bundle_dir = os.path.join(release_root, project_name)
    build_dir = os.path.join(script_dir, "build")
    os.makedirs(bundle_dir, exist_ok=True)
    os.makedirs(build_dir, exist_ok=True)

    version_info_path = create_version_info_file(build_dir, project_name, version)

    hidden_imports = [
        "blogcontentsClawring",
        "blog_ai_complete",
        "requests",
        "bs4",
        "dotenv",
        "charset_normalizer",
        "idna",
        "soupsieve",
        "certifi",
        "urllib3",
        "markdown",
        "openai",
        "httpx",
        "httpcore",
        "h11",
        "anyio",
        "sniffio",
        "pydantic",
        "pydantic_core",
        "jiter",
        "distro",
        "google.generativeai",
        "google.ai.generativelanguage",
        "google.api_core",
        "google.auth",
        "google.auth.transport.requests",
        "grpc",
        "grpc_status",
        "selenium",
        "undetected_chromedriver",
        "websockets",
    ]

    collect_all = [
        "openai",
        "google.generativeai",
        "google.api_core",
    ]

    args = [
        "--onefile",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--name",
        project_name,
        "--distpath",
        bundle_dir,
        "--workpath",
        build_dir,
        "--specpath",
        build_dir,
        "--version-file",
        version_info_path,
    ]
    for mod in hidden_imports:
        args.extend(["--hidden-import", mod])
    for pkg in collect_all:
        args.extend(["--collect-all", pkg])
    args.append("blogCrawlingUI.py")

    print(f"앱 버전(소스): {version}  (빌드일: {today})")
    print(f"출력: {bundle_dir}\\{project_name}.exe")
    print(f"버전 메타데이터: {version_info_path}")
    print("PyInstaller 실행 중... (수 분 걸릴 수 있습니다)")

    try:
        PyInstaller.__main__.run(args)
    except SystemExit as e:
        if e.code not in (0, None):
            print(f"PyInstaller 종료 코드: {e.code}")
            return int(e.code) if isinstance(e.code, int) else 1

    exe_path = os.path.join(bundle_dir, f"{project_name}.exe")
    if not os.path.isfile(exe_path):
        print("\n실패: exe 파일이 생성되지 않았습니다.")
        return 1

    size_mb = os.path.getsize(exe_path) / (1024 * 1024)
    print(f"\nexe 생성: {exe_path} ({size_mb:.1f} MB)")
    print("동봉 파일 복사:")
    copy_bundle_files(script_dir, bundle_dir)

    hint_path = write_latest_build_hint(release_root, bundle_dir, exe_path, version)
    legacy_exes = list_legacy_release_exes(release_root)

    print(f"\n배포 폴더: {os.path.abspath(bundle_dir)}")
    print(f"최신 실행 안내: {os.path.abspath(hint_path)}")
    if legacy_exes:
        print("\n주의: release 루트에 예전 exe가 남아 있습니다.")
        for name in legacy_exes:
            print(f"  - {name}")
        print("최신 버전은 위 배포 폴더 안의 exe를 실행해야 반영됩니다.")
    print("위 폴더 전체를 복사해 두면 바로 실행할 수 있습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
