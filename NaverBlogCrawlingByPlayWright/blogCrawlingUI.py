#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
네이버 블로그 크롤링 GUI 애플리케이션

이 애플리케이션은 네이버 블로그 크롤링을 위한 GUI 인터페이스를 제공합니다.

시스템 프롬프트·AI 지침(사용자 프롬프트, 멀티라인)은 각각 「파일 불러오기」로 txt를 선택하거나
기본(ai_system_prompt.txt, ai_guidelines.txt)에서 시작 시 로드됩니다.
「시스템 프롬프트 저장하기」「지침 저장하기」는 다른 이름·경로로 저장할 수 있는 저장 대화상자를 띄웁니다.
검색 키워드·크롤 개수·키워드별 AI 추가 문구·API 제공자·모델·글작성 소스 폴더·시스템·지침 파일 경로는 gui_preferences.json 에 저장되어 다음 실행 시 복원됩니다.
입력·선택 변경 시 gui_preferences.json 은 자동 저장됩니다.
AI 글작성 API 호출 성공 횟수는 api_usage_stats.json 에 누적됩니다.
네이버·OpenAI·Gemini 등 API 키는 프로젝트 폴더의 .env 파일에 넣으면 자동으로 읽습니다.
"""

__version__ = "1.1.1"

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import json
import threading
import os
import sys
import traceback
import webbrowser
import re
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from typing import Optional

# 스크립트/exe가 있는 폴더 (PyInstaller 단일 exe는 __file__이 임시 폴더이므로 frozen 시 exe 디렉터리 사용)
def _app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


_script_dir = _app_base_dir()
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))

# AI 시스템·지침 기본 경로 (.txt, 최초 실행·경로 미지정 시)
AI_SYSTEM_PROMPT_PATH = _script_dir / "ai_system_prompt.txt"
AI_GUIDELINES_PATH = _script_dir / "ai_guidelines.txt"
# GUI 검색·크롤 개수·API 제공자·소스 폴더
GUI_PREFERENCES_PATH = _script_dir / "gui_preferences.json"
OPENAI_USAGE_URL = "https://platform.openai.com/settings/organization/usage"


def _decode_guidelines_file_bytes(raw: bytes) -> Optional[str]:
    """메모장(UTF-8 BOM)·UTF-8·CP949 순으로 디코딩. 모두 실패하면 None."""
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return raw.decode(enc).replace("\r\n", "\n")
        except UnicodeDecodeError:
            continue
    return None


# blogcontentsClawring.py에서 필요한 함수들 import
try:
    from blogcontentsClawring import (
        get_blog_posts_by_keyword,
        crawl_multiple_posts,
        get_result_dir,
        load_project_dotenv,
    )
    from blog_ai_complete import (
        AI_TEMPERATURE_CHOICES_UI,
        OPENAI_MODEL_CHOICES,
        GEMINI_MODEL_CHOICES,
        coerce_ai_temperature,
        format_ai_temperature_ui,
        export_html_from_completed_md_in_folder,
        process_folder_combined,
        process_folder_combined_direct,
        default_crawl_txt_folder,
        list_txt_files_in_folder,
        get_blog_api_usage_stats,
        embedded_system_prompt_default,
    )
except ImportError as e:
    err_msg = f"blogcontentsClawring.py를 불러올 수 없습니다.\n\n{e}"
    print(err_msg, file=sys.stderr)
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("오류", err_msg)
    except Exception:
        pass
    sys.exit(1)
except Exception as e:
    err_msg = f"blogcontentsClawring 로드 중 오류:\n\n{e}"
    traceback.print_exc()
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("오류", err_msg)
    except Exception:
        pass
    sys.exit(1)


class BlogCrawlerGUI:
    """네이버 블로그 크롤링 GUI 클래스"""

    def __init__(self, root):
        self.root = root
        self.root.title(f"네이버 블로그 크롤링 도구 v{__version__}")
        self.root.geometry("1200x1000")
        self.root.minsize(720, 640)
        self.root.resizable(True, True)

        # 아이콘 설정 (선택사항)
        try:
            self.root.iconbitmap("icon.ico")
        except:
            pass

        self.create_widgets()
        self.center_window()

    def create_widgets(self):
        """GUI 위젯 생성"""
        # 메인 프레임
        main_frame = ttk.Frame(self.root, padding="20")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # 제목
        title_label = ttk.Label(main_frame, text=f"네이버 블로그 크롤링 도구 v{__version__}",
                               font=("Arial", 16, "bold"))
        title_label.grid(row=0, column=0, sticky=tk.W, pady=(0, 16))

        # 탭: 검색 설정 / AI 설정 / 이미지 프롬프트
        notebook = ttk.Notebook(main_frame)
        notebook.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 10))

        tab_search = ttk.Frame(notebook, padding=0)
        tab_ai = ttk.Frame(notebook, padding=0)
        tab_image = ttk.Frame(notebook, padding=0)
        notebook.add(tab_search, text="검색 설정")
        notebook.add(tab_ai, text="AI 설정")
        notebook.add(tab_image, text="이미지 프롬프트")
        tab_search.columnconfigure(0, weight=1)
        tab_search.rowconfigure(4, weight=1)

        # 키워드 입력 섹션
        keyword_frame = ttk.LabelFrame(tab_search, text="검색 설정", padding="10")
        keyword_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N), pady=(0, 12))

        ttk.Label(keyword_frame, text="키워드(콤마로 구분):").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.keyword_var = tk.StringVar()
        self.keyword_entry = ttk.Entry(keyword_frame, textvariable=self.keyword_var, width=40)
        self.keyword_entry.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(10, 0), pady=5)

        ttk.Label(keyword_frame, text="크롤링할 갯수:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.count_var = tk.IntVar(value=5)
        self.count_spinbox = tk.Spinbox(keyword_frame, from_=1, to=50, textvariable=self.count_var, width=10)
        self.count_spinbox.grid(row=1, column=1, sticky=tk.W, padx=(10, 0), pady=5)

        self._keyword_extra_content = {}
        self._keyword_extra_widgets = {}
        self._keyword_extra_sync_job = None
        self._keyword_extra_snapshot = {}

        kw_extra_outer = ttk.LabelFrame(
            tab_search,
            text="키워드별 AI 추가 문구 (사용자 메시지에 이어 붙여 API로 전달)",
            padding="8",
        )
        kw_extra_outer.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 10))
        kw_extra_outer.columnconfigure(0, weight=1)
        kw_extra_outer.rowconfigure(0, weight=1)
        self.keyword_extra_notebook = ttk.Notebook(kw_extra_outer)
        self.keyword_extra_notebook.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # AI 설정 (지침 · 프로바이더 · 글작성 소스 폴더)
        ai_frame = ttk.LabelFrame(tab_ai, text="AI 글작성", padding="10")
        ai_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        ttk.Label(ai_frame, text="API 제공자:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.ai_provider_var = tk.StringVar(value="auto")
        self.ai_provider_combo = ttk.Combobox(
            ai_frame,
            textvariable=self.ai_provider_var,
            values=("auto", "openai", "gemini"),
            state="readonly",
            width=14,
        )
        self.ai_provider_combo.grid(row=0, column=1, sticky=tk.W, padx=(8, 0), pady=2)
        ttk.Label(
            ai_frame,
            text="(auto: OPENAI_API_KEY 우선, 없으면 GEMINI_API_KEY · 환경변수 BLOG_AI_PROVIDER로도 지정 가능)",
            wraplength=520,
        ).grid(row=0, column=2, sticky=tk.W, padx=(10, 0), pady=2)

        ttk.Label(ai_frame, text="OpenAI 모델:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.openai_model_var = tk.StringVar(value=OPENAI_MODEL_CHOICES[0])
        self.openai_model_combo = ttk.Combobox(
            ai_frame,
            textvariable=self.openai_model_var,
            values=OPENAI_MODEL_CHOICES,
            state="readonly",
            width=22,
        )
        self.openai_model_combo.grid(row=1, column=1, sticky=tk.W, padx=(8, 0), pady=2)
        self.openai_model_hint_label = ttk.Label(
            ai_frame,
            text="(OpenAI·auto에서 OpenAI 사용 시 적용)",
            wraplength=520,
        )
        self.openai_model_hint_label.grid(row=1, column=2, sticky=tk.W, padx=(10, 0), pady=2)

        ttk.Label(ai_frame, text="Gemini 모델:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.gemini_model_var = tk.StringVar(value=GEMINI_MODEL_CHOICES[0])
        self.gemini_model_combo = ttk.Combobox(
            ai_frame,
            textvariable=self.gemini_model_var,
            values=GEMINI_MODEL_CHOICES,
            state="readonly",
            width=22,
        )
        self.gemini_model_combo.grid(row=2, column=1, sticky=tk.W, padx=(8, 0), pady=2)
        self.gemini_model_hint_label = ttk.Label(
            ai_frame,
            text="(Gemini·auto에서 Gemini 사용 시 적용)",
            wraplength=520,
        )
        self.gemini_model_hint_label.grid(row=2, column=2, sticky=tk.W, padx=(10, 0), pady=2)

        ttk.Label(ai_frame, text="Temperature:").grid(row=3, column=0, sticky=tk.W, pady=2)
        self.ai_temperature_var = tk.StringVar(value=AI_TEMPERATURE_CHOICES_UI[0])
        self.ai_temperature_combo = ttk.Combobox(
            ai_frame,
            textvariable=self.ai_temperature_var,
            values=AI_TEMPERATURE_CHOICES_UI,
            state="readonly",
            width=8,
        )
        self.ai_temperature_combo.grid(row=3, column=1, sticky=tk.W, padx=(8, 0), pady=2)
        ttk.Label(
            ai_frame,
            text="(OpenAI·Gemini 글작성 공통, 0.75~1.0 · 0.05 간격)",
            wraplength=520,
        ).grid(row=3, column=2, sticky=tk.W, padx=(10, 0), pady=2)

        self.openai_web_search_var = tk.BooleanVar(value=True)
        self.openai_web_search_check = ttk.Checkbutton(
            ai_frame,
            text="OpenAI 웹 검색 사용",
            variable=self.openai_web_search_var,
        )
        self.openai_web_search_check.grid(row=4, column=1, sticky=tk.W, padx=(8, 0), pady=(6, 2))
        ttk.Label(
            ai_frame,
            text="(OpenAI에서만 적용 · 끄면 tools=web_search 미사용)",
            wraplength=520,
            foreground="gray",
        ).grid(row=4, column=2, sticky=tk.W, padx=(10, 0), pady=(6, 2))

        self.api_usage_stats_var = tk.StringVar(value="")
        usage_block = ttk.Frame(ai_frame)
        usage_block.grid(row=5, column=0, columnspan=3, sticky=tk.W, pady=(4, 2))
        ttk.Label(usage_block, textvariable=self.api_usage_stats_var, wraplength=700).pack(
            anchor=tk.W
        )
        self._openai_usage_link = tk.Label(
            usage_block,
            text=OPENAI_USAGE_URL,
            fg="#0b57d0",
            cursor="hand2",
            font=("Segoe UI", 9, "underline"),
        )
        self._openai_usage_link.pack(anchor=tk.W, pady=(4, 0))
        self._openai_usage_link.bind(
            "<Button-1>",
            lambda _e: webbrowser.open(OPENAI_USAGE_URL),
        )

        ttk.Label(ai_frame, text="시스템 프롬프트:").grid(row=6, column=0, sticky=(tk.N, tk.W), pady=(8, 2))
        self.system_prompt_text = tk.Text(ai_frame, height=5, width=60, wrap=tk.WORD)
        self.system_prompt_text.grid(row=6, column=1, columnspan=2, sticky=(tk.W, tk.E), padx=(8, 0), pady=(8, 2))

        system_prompt_btn_row = ttk.Frame(ai_frame)
        system_prompt_btn_row.grid(row=7, column=0, columnspan=3, sticky=tk.W, pady=(0, 4))
        self.load_system_prompt_button = ttk.Button(
            system_prompt_btn_row,
            text="파일 불러오기",
            command=self.load_system_prompt_from_txt_file,
        )
        self.load_system_prompt_button.pack(side=tk.LEFT, padx=(0, 8))
        self.save_system_prompt_button = ttk.Button(
            system_prompt_btn_row,
            text="시스템 프롬프트 저장하기",
            command=self.save_system_prompt_to_txt_file,
        )
        self.save_system_prompt_button.pack(side=tk.LEFT, padx=(0, 12))
        self.system_prompt_path_label = ttk.Label(
            system_prompt_btn_row,
            text="",
            foreground="gray",
            wraplength=520,
        )
        self.system_prompt_path_label.pack(side=tk.LEFT)

        ttk.Label(ai_frame, text="지침(프롬프트):").grid(row=8, column=0, sticky=(tk.N, tk.W), pady=(8, 2))
        self.guidelines_text = tk.Text(ai_frame, height=5, width=60, wrap=tk.WORD)
        self.guidelines_text.grid(row=8, column=1, columnspan=2, sticky=(tk.W, tk.E), padx=(8, 0), pady=(8, 2))

        guidelines_btn_row = ttk.Frame(ai_frame)
        guidelines_btn_row.grid(row=9, column=0, columnspan=3, sticky=tk.W, pady=(0, 4))
        self.load_guidelines_button = ttk.Button(
            guidelines_btn_row,
            text="파일 불러오기",
            command=self.load_guidelines_from_txt_file,
        )
        self.load_guidelines_button.pack(side=tk.LEFT, padx=(0, 8))
        self.save_guidelines_button = ttk.Button(
            guidelines_btn_row,
            text="지침 저장하기",
            command=self.save_guidelines_to_txt_file,
        )
        self.save_guidelines_button.pack(side=tk.LEFT, padx=(0, 12))
        self.guidelines_path_label = ttk.Label(
            guidelines_btn_row,
            text="",
            foreground="gray",
            wraplength=520,
        )
        self.guidelines_path_label.pack(side=tk.LEFT)

        src_row = ttk.Frame(ai_frame)
        src_row.grid(row=10, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(6, 0))
        ttk.Label(src_row, text="글작성 소스 폴더:").pack(side=tk.LEFT)
        self.pick_folder_button = ttk.Button(src_row, text="폴더 선택", command=self.pick_ai_source_folder)
        self.pick_folder_button.pack(side=tk.LEFT, padx=(8, 0))
        self.clear_folder_button = ttk.Button(src_row, text="기본으로", command=self.clear_ai_source_folder)
        self.clear_folder_button.pack(side=tk.LEFT, padx=(6, 0))
        self.ai_source_desc_var = tk.StringVar()
        self.ai_source_desc_label = ttk.Label(src_row, textvariable=self.ai_source_desc_var, wraplength=480)
        self.ai_source_desc_label.pack(side=tk.LEFT, padx=(10, 0), fill=tk.X, expand=True)
        ai_frame.columnconfigure(2, weight=1)
        tab_ai.columnconfigure(0, weight=1)
        tab_ai.rowconfigure(0, weight=1)

        image_frame = ttk.LabelFrame(tab_image, text="이미지 프롬프트", padding="10")
        image_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        ttk.Label(
            image_frame,
            text=(
                "첫 번째 입력란의 공통 프롬프트는 자동 저장됩니다. "
                "검색 설정 탭의 키워드를 주제로 삼아, ChatGPT 웹에 바로 붙여넣을 수 있는 "
                "완성 프롬프트를 아래 입력란에 순서대로 만들어 줍니다."
            ),
            wraplength=780,
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W)

        ttk.Label(image_frame, text="1. 공통 프롬프트:").grid(
            row=1, column=0, sticky=(tk.N, tk.W), pady=(10, 4)
        )
        common_prompt_frame = ttk.Frame(image_frame)
        common_prompt_frame.grid(
            row=1, column=1, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(10, 4)
        )
        common_prompt_frame.columnconfigure(0, weight=1)
        common_prompt_frame.rowconfigure(0, weight=1)
        self.image_common_prompt_text = tk.Text(
            common_prompt_frame,
            height=7,
            width=80,
            wrap=tk.WORD,
        )
        self.image_common_prompt_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        common_prompt_scrollbar = ttk.Scrollbar(
            common_prompt_frame,
            orient=tk.VERTICAL,
            command=self.image_common_prompt_text.yview,
        )
        common_prompt_scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.image_common_prompt_text.configure(yscrollcommand=common_prompt_scrollbar.set)
        self.image_common_prompt_text.bind(
            "<<Modified>>",
            lambda _e, tw=self.image_common_prompt_text: self._on_image_common_prompt_text_modified(tw),
        )

        ttk.Label(
            image_frame,
            text="2. 완성된 프롬프트 목록:",
        ).grid(row=2, column=0, sticky=(tk.N, tk.W), pady=(10, 4))
        ttk.Label(
            image_frame,
            text=(
                "키워드 입력 순서를 그대로 따르며, 키워드나 공통 프롬프트를 바꾸면 "
                "아래 내용도 자동으로 다시 작성됩니다."
            ),
            wraplength=780,
            foreground="gray",
        ).grid(row=3, column=0, columnspan=2, sticky=tk.W)

        output_prompt_frame = ttk.Frame(image_frame)
        output_prompt_frame.grid(
            row=4, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(8, 0)
        )
        output_prompt_frame.columnconfigure(0, weight=1)
        output_prompt_frame.rowconfigure(0, weight=1)
        self.image_prompt_output_text = tk.Text(
            output_prompt_frame,
            height=24,
            width=90,
            wrap=tk.WORD,
        )
        self.image_prompt_output_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        output_prompt_scrollbar = ttk.Scrollbar(
            output_prompt_frame,
            orient=tk.VERTICAL,
            command=self.image_prompt_output_text.yview,
        )
        output_prompt_scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.image_prompt_output_text.configure(yscrollcommand=output_prompt_scrollbar.set)

        image_frame.columnconfigure(1, weight=1)
        image_frame.rowconfigure(4, weight=1)
        tab_image.columnconfigure(0, weight=1)
        tab_image.rowconfigure(0, weight=1)

        # 버튼 프레임
        button_frame = ttk.Frame(tab_search)
        button_frame.grid(row=2, column=0, pady=(0, 12), sticky=tk.W)

        self.automation_button = ttk.Button(button_frame, text="자동화", command=self.start_automation_crawl)
        self.automation_button.grid(row=0, column=0, padx=(0, 8))

        self.start_button = ttk.Button(
            button_frame,
            text="1 크롤링 시작",
            command=self.start_crawl_only,
            style="Accent.TButton",
        )
        self.start_button.grid(row=0, column=1, padx=(0, 8))

        self.ai_write_button = ttk.Button(
            button_frame,
            text="2 글작성하기 · HTML",
            command=self.start_ai_write,
        )
        self.ai_write_button.grid(row=0, column=2, padx=(0, 8))

        self.stop_button = ttk.Button(button_frame, text="중지", command=self.stop_crawling, state="disabled")
        self.stop_button.grid(row=0, column=3, padx=(0, 8))

        self.open_result_button = ttk.Button(button_frame, text="결과 폴더 열기",
                                          command=self.open_result_folder, state="disabled")
        self.open_result_button.grid(row=0, column=4)

        # 진행바
        progress_frame = ttk.LabelFrame(tab_search, text="진행 상황", padding="10")
        progress_frame.grid(row=3, column=0, sticky=(tk.W, tk.E), pady=(0, 10))

        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=0)

        # 로그
        log_frame = ttk.LabelFrame(tab_search, text="로그", padding="10")
        log_frame.grid(row=4, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 0))

        self.log_text = tk.Text(log_frame, height=24, wrap=tk.WORD, state="disabled")
        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)

        self.log_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))

        # 상태 표시줄
        self.status_var = tk.StringVar()
        self.status_var.set(f"준비 완료 | v{__version__}")
        status_bar = ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=(0, 0))

        # 그리드 설정
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(1, weight=1)
        keyword_frame.columnconfigure(1, weight=1)
        progress_frame.columnconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        # 스타일 설정
        style = ttk.Style()
        style.configure("Accent.TButton", font=("Arial", 10, "bold"))

        # 단축키 설정
        self.root.bind('<Return>', lambda e: self.start_crawl_only())
        self.root.bind('<Escape>', lambda e: self.stop_crawling())

        # 크롤링 / AI 상태
        self.is_crawling = False
        self.ai_busy = False
        self.crawl_thread = None
        self.ai_thread = None
        self._pending_auto_ai = False
        self._guidelines_snapshot = ""
        self._system_prompt_snapshot = ""
        self._provider_snapshot = "auto"
        self._openai_model_snapshot = OPENAI_MODEL_CHOICES[0]
        self._gemini_model_snapshot = GEMINI_MODEL_CHOICES[0]
        self._ai_temperature_snapshot = float(coerce_ai_temperature(None))
        self._openai_web_search_snapshot = True
        self.ai_custom_folder = None
        self.total_posts = 0
        self.completed_posts = 0
        self.current_post_index = 0
        self.total_delay_seconds = 0.0
        self._progress_context = "idle"

        self._prefs_loading = False
        self.guidelines_file_path = AI_GUIDELINES_PATH.resolve()
        self.system_prompt_file_path = AI_SYSTEM_PROMPT_PATH.resolve()
        self._load_gui_preferences()
        self._load_system_prompt_from_disk()
        self._load_guidelines_from_disk()
        self.refresh_ai_source_description()
        self._bind_gui_preferences_traces()
        self._bind_selection_autosave()
        self._sync_provider_dependent_widgets()
        self.refresh_api_usage_display()
        self._sync_keyword_extra_tabs()
        self._refresh_image_prompt_output()

    class _GuiLogStream:
        """print 출력을 GUI 로그로 전달하는 스트림"""
        def __init__(self, callback):
            self.callback = callback
            self.buffer = ""

        def write(self, text):
            if not text:
                return
            self.buffer += text.replace("\r\n", "\n")
            while "\n" in self.buffer:
                line, self.buffer = self.buffer.split("\n", 1)
                if line.strip():
                    self.callback(line)

        def flush(self):
            if self.buffer.strip():
                self.callback(self.buffer.strip())
            self.buffer = ""

    def center_window(self):
        """윈도우를 화면 중앙에 배치"""
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (width // 2)
        y = (self.root.winfo_screenheight() // 2) - (height // 2)
        self.root.geometry(f'{width}x{height}+{x}+{y}')

    def log_message(self, message):
        """로그 메시지 추가"""
        if threading.current_thread() is not threading.main_thread():
            self.root.after(0, self.log_message, message)
            return
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")
        self.root.update_idletasks()

    def _apply_main_progress(self, pct: float, status_text: str = ""):
        """메인 스레드에서 진행률·상태 갱신(다른 스레드에서는 after로 위임)."""
        if threading.current_thread() is not threading.main_thread():
            self.root.after(0, lambda: self._apply_main_progress(pct, status_text))
            return
        self.progress_var.set(max(0.0, min(100.0, pct)))
        if status_text:
            self.status_var.set(status_text)

    def _sync_provider_dependent_widgets(self):
        """API 제공자에 따라 사용하지 않는 모델 콤보는 비활성·안내 문구 표시."""
        if self._prefs_loading:
            return
        if self.is_crawling or self.ai_busy:
            return
        prov = (self.ai_provider_var.get() or "auto").strip().lower()
        if prov == "openai":
            self.openai_model_combo.config(state="readonly")
            self.gemini_model_combo.config(state="disabled")
            self.openai_model_hint_label.config(text="(이번 글작성에 사용)")
            self.gemini_model_hint_label.config(
                text="(미사용 — 제공자가 OpenAI 입니다)"
            )
        elif prov == "gemini":
            self.openai_model_combo.config(state="disabled")
            self.gemini_model_combo.config(state="readonly")
            self.openai_model_hint_label.config(
                text="(미사용 — 제공자가 Gemini 입니다)"
            )
            self.gemini_model_hint_label.config(text="(이번 글작성에 사용)")
        else:
            self.openai_model_combo.config(state="readonly")
            self.gemini_model_combo.config(state="readonly")
            self.openai_model_hint_label.config(
                text="(auto: 키가 있으면 OpenAI 우선, 없으면 Gemini)"
            )
            self.gemini_model_hint_label.config(
                text="(auto: Gemini 사용 시 적용)"
            )

    def update_progress_display(self):
        """진행률/딜레이 정보를 프로그래스바와 상태바에 반영"""
        if threading.current_thread() is not threading.main_thread():
            self.root.after(0, self.update_progress_display)
            return

        if self._progress_context in ("ai_only", "automation_ai"):
            return

        if self.total_posts > 0:
            ratio = self.completed_posts / self.total_posts
            n_seq = getattr(self, "_auto_seq_n_keywords", 0)
            if self._progress_context == "automation_crawl" and n_seq > 1:
                seg = 100.0 / n_seq
                i_kw = getattr(self, "_auto_seq_kw_i", 1)
                base = (i_kw - 1) * seg
                percent = base + 0.5 * seg * ratio
            elif self._progress_context == "automation_crawl":
                percent = ratio * 85.0
            else:
                percent = ratio * 100.0
            self.progress_var.set(percent)
            self.status_var.set(
                f"진행 {self.completed_posts}/{self.total_posts} | 누적 딜레이 {self.total_delay_seconds:.2f}초"
            )

    def process_crawler_log_line(self, line):
        """크롤러 출력 로그를 UI에 표시하고 진행 상태를 파싱"""
        self.log_message(line)

        start_match = re.search(r"\[(\d+)\s*/\s*(\d+)\]\s*크롤링 시작", line)
        if start_match:
            self.current_post_index = int(start_match.group(1))
            self.total_posts = int(start_match.group(2))
            self.completed_posts = max(self.completed_posts, self.current_post_index - 1)
            self.update_progress_display()
            return

        delay_match = re.search(r"\[POST_DELAY\].*?([0-9]+(?:\.[0-9]+)?)초", line)
        if delay_match:
            self.total_delay_seconds += float(delay_match.group(1))
            self.update_progress_display()
            return

        if ("✓ 성공:" in line) or ("✗ 실패:" in line):
            if self.current_post_index > 0:
                self.completed_posts = max(self.completed_posts, self.current_post_index)
                self.update_progress_display()

    def clear_log(self):
        """로그 영역 초기화"""
        self.log_text.config(state="normal")
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state="disabled")

    def parse_keywords(self, raw_keywords: str):
        """콤마(,)로 구분된 키워드 문자열을 리스트로 변환"""
        keywords = [kw.strip() for kw in raw_keywords.split(",") if kw.strip()]
        # 중복 키워드 제거 (입력 순서 유지)
        unique_keywords = list(dict.fromkeys(keywords))
        return unique_keywords

    def _parse_keywords_only(self):
        """검증·다이얼로그 없이 키워드 입력란만 파싱."""
        return self.parse_keywords(self.keyword_var.get().strip())

    def _get_image_common_prompt_text(self) -> str:
        widget = getattr(self, "image_common_prompt_text", None)
        if widget is None:
            return ""
        try:
            return widget.get("1.0", tk.END).replace("\r\n", "\n").rstrip("\n")
        except tk.TclError:
            return ""

    def _set_image_prompt_output_text(self, body: str):
        widget = getattr(self, "image_prompt_output_text", None)
        if widget is None:
            return
        try:
            widget.delete("1.0", tk.END)
            if body:
                widget.insert("1.0", body)
        except tk.TclError:
            return

    def _build_image_prompt_output_text(self) -> str:
        keywords = self._parse_keywords_only()
        common_prompt = self._get_image_common_prompt_text().strip()
        if not keywords:
            return (
                "검색 설정 탭에 키워드를 입력하면, 키워드 순서대로 이미지 프롬프트를 "
                "여기에 자동으로 정리합니다."
            )

        blocks = []
        for idx, keyword in enumerate(keywords, start=1):
            lines = [f"[프롬프트 {idx}]", f"주제: {keyword}"]
            if common_prompt:
                lines.append(common_prompt)
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def _refresh_image_prompt_output(self):
        self._set_image_prompt_output_text(self._build_image_prompt_output_text())

    def _on_image_common_prompt_text_modified(self, w: tk.Text):
        try:
            w.edit_modified(False)
        except tk.TclError:
            return
        if self._prefs_loading:
            return
        self._refresh_image_prompt_output()
        self._save_gui_preferences()

    def _keyword_preview(self, keywords):
        keyword_preview = ", ".join(keywords)
        if len(keyword_preview) > 70:
            keyword_preview = f"{keyword_preview[:67]}..."
        return keyword_preview

    def _parse_keywords_count(self):
        """검증만 수행. 실패 시 None."""
        raw_keywords = self.keyword_var.get().strip()
        count = self.count_var.get()
        keywords = self.parse_keywords(raw_keywords)
        if not keywords:
            messagebox.showwarning("입력 오류", "키워드를 입력해주세요.\n예: 키워드1, 키워드2")
            self.keyword_entry.focus()
            return None
        if not (1 <= count <= 50):
            messagebox.showwarning("입력 오류", "크롤링 갯수는 1-50 사이로 입력해주세요.")
            return None
        return keywords, count

    def _read_guidelines_file_stripped(self) -> str:
        """현재 지침 파일 경로에서 읽기(인코딩 폴백). 실패·없음이면 빈 문자열."""
        try:
            p = self.guidelines_file_path
            if not p.is_file():
                return ""
            raw = p.read_bytes()
            dec = _decode_guidelines_file_bytes(raw)
            if dec is None:
                return ""
            return dec.strip()
        except OSError:
            return ""

    def _read_system_prompt_file_stripped(self) -> str:
        """현재 시스템 프롬프트 파일 경로에서 읽기. 실패·없음이면 빈 문자열."""
        try:
            p = self.system_prompt_file_path
            if not p.is_file():
                return ""
            raw = p.read_bytes()
            dec = _decode_guidelines_file_bytes(raw)
            if dec is None:
                return ""
            return dec.strip()
        except OSError:
            return ""

    def _snapshot_guidelines_and_provider(self):
        # API에는 편집창 내용이 우선. 창이 비어 있으면 디스크의 현재 파일을 사용
        # (외부에서 txt만 수정한 경우·시작 시 로드 실패 등 보완).
        sy = (
            self.system_prompt_text.get("1.0", tk.END)
            .replace("\r\n", "\n")
            .rstrip("\n")
            .strip()
        )
        self._system_prompt_snapshot = sy if sy else self._read_system_prompt_file_stripped()
        if not self._system_prompt_snapshot.strip():
            self._system_prompt_snapshot = embedded_system_prompt_default()
        w = (
            self.guidelines_text.get("1.0", tk.END)
            .replace("\r\n", "\n")
            .rstrip("\n")
            .strip()
        )
        self._guidelines_snapshot = w if w else self._read_guidelines_file_stripped()
        self._provider_snapshot = (self.ai_provider_var.get() or "auto").strip()
        self._openai_model_snapshot = (
            (self.openai_model_var.get() or "").strip() or OPENAI_MODEL_CHOICES[0]
        )
        self._gemini_model_snapshot = (
            (self.gemini_model_var.get() or "").strip() or GEMINI_MODEL_CHOICES[0]
        )
        t_s = (self.ai_temperature_var.get() or "").strip()
        self._ai_temperature_snapshot = coerce_ai_temperature(t_s)
        self._openai_web_search_snapshot = bool(self.openai_web_search_var.get())
        self._flush_keyword_extra_to_memory()
        self._keyword_extra_snapshot = dict(self._keyword_extra_content)

    def _flush_keyword_extra_to_memory(self):
        for kw, w in list(getattr(self, "_keyword_extra_widgets", {}).items()):
            if isinstance(w, tk.Text):
                try:
                    self._keyword_extra_content[kw] = (
                        w.get("1.0", tk.END).replace("\r\n", "\n").rstrip("\n")
                    )
                except tk.TclError:
                    pass

    def _on_keyword_extra_text_modified(self, w: tk.Text):
        try:
            w.edit_modified(False)
        except tk.TclError:
            return
        if self._prefs_loading:
            return
        self._save_gui_preferences()

    def _on_keyword_list_changed_schedule_sync(self, *_args):
        if self._prefs_loading:
            return
        job = getattr(self, "_keyword_extra_sync_job", None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except (ValueError, tk.TclError):
                pass
        self._keyword_extra_sync_job = self.root.after(
            150, self._finish_keyword_extra_sync_job
        )

    def _finish_keyword_extra_sync_job(self):
        self._keyword_extra_sync_job = None
        self._sync_keyword_extra_tabs()
        self._refresh_image_prompt_output()
        self._save_gui_preferences()

    def _sync_keyword_extra_tabs(self):
        nb = getattr(self, "keyword_extra_notebook", None)
        if nb is None:
            return
        self._flush_keyword_extra_to_memory()
        kws = self.parse_keywords((self.keyword_var.get() or "").strip())
        try:
            while nb.index("end") > 0:
                nb.forget(0)
        except tk.TclError:
            return
        self._keyword_extra_widgets = {}
        busy = self.is_crawling or self.ai_busy
        if not kws:
            placeholder = ttk.Frame(nb, padding=8)
            nb.add(placeholder, text="키워드")
            ttk.Label(
                placeholder,
                text="콤마로 구분한 키워드를 입력하면 키워드마다 탭·입력란이 생깁니다.",
                wraplength=560,
            ).pack(anchor=tk.W)
            return
        for kw in kws:
            tab_title = kw if len(kw) <= 20 else kw[:17] + "…"
            fr = ttk.Frame(nb, padding=6)
            nb.add(fr, text=tab_title)
            fr.columnconfigure(0, weight=1)
            fr.rowconfigure(0, weight=1)
            tx = tk.Text(fr, height=5, width=72, wrap=tk.WORD)
            tx.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
            tx.insert("1.0", self._keyword_extra_content.get(kw, ""))
            tx.bind(
                "<<Modified>>",
                lambda _e, tw=tx: self._on_keyword_extra_text_modified(tw),
            )
            self._keyword_extra_widgets[kw] = tx
            if busy:
                tx.config(state="disabled")

    def _keyword_extra_for_folder(self, folder) -> str:
        """오늘 날짜 result 경로·키워드 목록으로 매칭되는 키워드 추가 문구."""
        self._flush_keyword_extra_to_memory()
        folder = Path(folder).resolve()
        kws = self._parse_keywords_only()
        for kw in kws:
            try:
                expected = Path(
                    get_result_dir(date_included=True, keyword=kw)
                ).resolve()
            except OSError:
                continue
            if expected == folder:
                return (self._keyword_extra_content.get(kw) or "").strip()
        if len(kws) == 1:
            return (self._keyword_extra_content.get(kws[0]) or "").strip()
        return ""

    def _refresh_guidelines_path_display(self):
        try:
            p = self.guidelines_file_path.resolve()
        except OSError:
            p = self.guidelines_file_path
        self.guidelines_path_label.config(text=f"(현재 파일: {p.name}  |  {p})")

    def _refresh_system_prompt_path_display(self):
        try:
            p = self.system_prompt_file_path.resolve()
        except OSError:
            p = self.system_prompt_file_path
        self.system_prompt_path_label.config(text=f"(현재 파일: {p.name}  |  {p})")

    def _load_system_prompt_from_disk(self):
        try:
            p = self.system_prompt_file_path
            self.system_prompt_text.delete("1.0", tk.END)
            if p.is_file():
                raw = p.read_bytes()
                text = _decode_guidelines_file_bytes(raw)
                if text is not None:
                    self.system_prompt_text.insert("1.0", text.rstrip("\n"))
                else:
                    self.log_message(
                        f"시스템 프롬프트 파일 인코딩 오류(utf-8/cp949 아님): {self.system_prompt_file_path}"
                    )
        except Exception as e:
            self.log_message(f"시스템 프롬프트 파일 로드 실패({self.system_prompt_file_path}): {e}")
        self._refresh_system_prompt_path_display()

    def load_system_prompt_from_txt_file(self):
        """시스템 프롬프트 txt를 선택해 편집 영역에 불러옵니다."""
        if self.is_crawling or self.ai_busy:
            messagebox.showinfo(
                "안내",
                "크롤링 또는 AI/HTML 작업 중에는 시스템 프롬프트 파일을 불러올 수 없습니다.",
            )
            return
        try:
            parent = self.system_prompt_file_path.parent
            initialdir = str(parent) if parent.is_dir() else str(_script_dir)
        except Exception:
            initialdir = str(_script_dir)
        path = filedialog.askopenfilename(
            title="시스템 프롬프트 txt 파일 선택",
            initialdir=initialdir,
            filetypes=[("텍스트 파일", "*.txt"), ("모든 파일", "*.*")],
        )
        if not path:
            return
        src = Path(path)
        try:
            raw = src.read_bytes()
            text = _decode_guidelines_file_bytes(raw)
        except OSError as e:
            messagebox.showerror("불러오기 실패", f"파일을 읽을 수 없습니다.\n{e}")
            return
        if text is None:
            messagebox.showerror(
                "불러오기 실패",
                "파일 인코딩을 utf-8 또는 cp949로 읽을 수 없습니다.\n메모장에서 UTF-8로 저장해 보세요.",
            )
            return
        self.system_prompt_text.delete("1.0", tk.END)
        self.system_prompt_text.insert("1.0", text.rstrip("\n"))
        self.system_prompt_file_path = src.resolve()
        self._refresh_system_prompt_path_display()
        self._save_gui_preferences()
        messagebox.showinfo(
            "불러오기 완료",
            f"시스템 프롬프트를 불러왔습니다.\n\n{self.system_prompt_file_path}",
        )

    def save_system_prompt_to_txt_file(self):
        """다른 이름·경로를 지정해 시스템 프롬프트를 UTF-8 txt로 저장."""
        if self.is_crawling or self.ai_busy:
            messagebox.showinfo(
                "안내",
                "크롤링 또는 AI/HTML 작업 중에는 시스템 프롬프트를 저장할 수 없습니다.",
            )
            return
        try:
            parent = self.system_prompt_file_path.parent
            initialfile = self.system_prompt_file_path.name
            if not parent.is_dir():
                parent = AI_SYSTEM_PROMPT_PATH.parent
                initialfile = AI_SYSTEM_PROMPT_PATH.name
            initialdir = str(parent) if parent.is_dir() else str(_script_dir)
        except Exception:
            initialdir = str(_script_dir)
            initialfile = AI_SYSTEM_PROMPT_PATH.name
        path = filedialog.asksaveasfilename(
            title="시스템 프롬프트 저장",
            initialdir=initialdir,
            initialfile=initialfile,
            defaultextension=".txt",
            filetypes=[("텍스트 파일", "*.txt"), ("모든 파일", "*.*")],
        )
        if not path:
            return
        out = Path(path)
        try:
            body = self.system_prompt_text.get("1.0", tk.END).rstrip("\n")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(body, encoding="utf-8")
            self.system_prompt_file_path = out.resolve()
        except Exception as e:
            messagebox.showerror("저장 실패", f"시스템 프롬프트 파일을 저장할 수 없습니다.\n{e}")
            return
        self._refresh_system_prompt_path_display()
        self._save_gui_preferences()
        messagebox.showinfo(
            "저장 완료",
            f"시스템 프롬프트를 저장했습니다.\n\n{self.system_prompt_file_path}",
        )

    def _load_guidelines_from_disk(self):
        try:
            p = self.guidelines_file_path
            self.guidelines_text.delete("1.0", tk.END)
            if p.is_file():
                raw = p.read_bytes()
                text = _decode_guidelines_file_bytes(raw)
                if text is not None:
                    self.guidelines_text.insert("1.0", text.rstrip("\n"))
                else:
                    self.log_message(
                        f"지침 파일 인코딩 오류(utf-8/cp949 아님): {self.guidelines_file_path}"
                    )
        except Exception as e:
            self.log_message(f"지침 파일 로드 실패({self.guidelines_file_path}): {e}")
        self._refresh_guidelines_path_display()

    def load_guidelines_from_txt_file(self):
        """지침 txt 파일을 선택해 편집 영역에 불러옵니다."""
        if self.is_crawling or self.ai_busy:
            messagebox.showinfo(
                "안내",
                "크롤링 또는 AI/HTML 작업 중에는 지침 파일을 불러올 수 없습니다.",
            )
            return
        try:
            parent = self.guidelines_file_path.parent
            initialdir = str(parent) if parent.is_dir() else str(_script_dir)
        except Exception:
            initialdir = str(_script_dir)
        path = filedialog.askopenfilename(
            title="지침 txt 파일 선택",
            initialdir=initialdir,
            filetypes=[("텍스트 파일", "*.txt"), ("모든 파일", "*.*")],
        )
        if not path:
            return
        src = Path(path)
        try:
            raw = src.read_bytes()
            text = _decode_guidelines_file_bytes(raw)
        except OSError as e:
            messagebox.showerror("불러오기 실패", f"파일을 읽을 수 없습니다.\n{e}")
            return
        if text is None:
            messagebox.showerror(
                "불러오기 실패",
                "파일 인코딩을 utf-8 또는 cp949로 읽을 수 없습니다.\n메모장에서 UTF-8로 저장해 보세요.",
            )
            return
        self.guidelines_text.delete("1.0", tk.END)
        self.guidelines_text.insert("1.0", text.rstrip("\n"))
        self.guidelines_file_path = src.resolve()
        self._refresh_guidelines_path_display()
        self._save_gui_preferences()
        messagebox.showinfo("불러오기 완료", f"지침을 불러왔습니다.\n\n{self.guidelines_file_path}")

    def save_guidelines_to_txt_file(self):
        """다른 이름·경로를 지정해 지침을 UTF-8 txt로 저장."""
        if self.is_crawling or self.ai_busy:
            messagebox.showinfo("안내", "크롤링 또는 AI/HTML 작업 중에는 지침을 저장할 수 없습니다.")
            return
        try:
            parent = self.guidelines_file_path.parent
            initialfile = self.guidelines_file_path.name
            if not parent.is_dir():
                parent = AI_GUIDELINES_PATH.parent
                initialfile = AI_GUIDELINES_PATH.name
            initialdir = str(parent) if parent.is_dir() else str(_script_dir)
        except Exception:
            initialdir = str(_script_dir)
            initialfile = AI_GUIDELINES_PATH.name
        path = filedialog.asksaveasfilename(
            title="지침 저장",
            initialdir=initialdir,
            initialfile=initialfile,
            defaultextension=".txt",
            filetypes=[("텍스트 파일", "*.txt"), ("모든 파일", "*.*")],
        )
        if not path:
            return
        out = Path(path)
        try:
            body = self.guidelines_text.get("1.0", tk.END).rstrip("\n")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(body, encoding="utf-8")
            self.guidelines_file_path = out.resolve()
        except Exception as e:
            messagebox.showerror("저장 실패", f"지침 파일을 저장할 수 없습니다.\n{e}")
            return
        self._refresh_guidelines_path_display()
        self._save_gui_preferences()
        messagebox.showinfo(
            "저장 완료",
            f"지침을 저장했습니다.\n\n{self.guidelines_file_path}",
        )

    def _load_gui_preferences(self):
        p = GUI_PREFERENCES_PATH
        if not p.is_file():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return
        self._prefs_loading = True
        try:
            if isinstance(data.get("keywords"), str):
                self.keyword_var.set(data["keywords"])
            raw_extras = data.get("keyword_extra_by_keyword")
            if isinstance(raw_extras, dict):
                self._keyword_extra_content = {
                    str(k): (str(v) if v is not None else "")
                    for k, v in raw_extras.items()
                }
            else:
                self._keyword_extra_content = {}
            c = data.get("crawl_count")
            if isinstance(c, int) and 1 <= c <= 50:
                self.count_var.set(c)
            ap = data.get("ai_provider")
            if isinstance(ap, str) and ap in ("auto", "openai", "gemini"):
                self.ai_provider_var.set(ap)
            sf = data.get("ai_source_folder")
            if isinstance(sf, str) and sf.strip():
                q = Path(sf.strip())
                if q.is_dir():
                    self.ai_custom_folder = str(q.resolve())
                else:
                    self.ai_custom_folder = None
            else:
                self.ai_custom_folder = None
            gf = data.get("ai_guidelines_file")
            if isinstance(gf, str) and gf.strip():
                try:
                    self.guidelines_file_path = Path(gf.strip()).expanduser().resolve()
                except OSError:
                    pass
            spf = data.get("ai_system_prompt_file")
            if isinstance(spf, str) and spf.strip():
                try:
                    self.system_prompt_file_path = Path(spf.strip()).expanduser().resolve()
                except OSError:
                    pass
            image_common_prompt = data.get("image_common_prompt")
            if isinstance(image_common_prompt, str):
                self.image_common_prompt_text.delete("1.0", tk.END)
                self.image_common_prompt_text.insert("1.0", image_common_prompt.rstrip("\n"))
                self.image_common_prompt_text.edit_modified(False)
            self.openai_model_combo.configure(values=OPENAI_MODEL_CHOICES)
            om = data.get("openai_model")
            if isinstance(om, str) and om.strip():
                v = om.strip()
                if v in OPENAI_MODEL_CHOICES:
                    self.openai_model_var.set(v)
                else:
                    self.openai_model_var.set(OPENAI_MODEL_CHOICES[0])
            self.gemini_model_combo.configure(values=GEMINI_MODEL_CHOICES)
            gm = data.get("gemini_model")
            if isinstance(gm, str) and gm.strip():
                v = gm.strip()
                if v not in GEMINI_MODEL_CHOICES:
                    self.gemini_model_combo.configure(values=(v,) + GEMINI_MODEL_CHOICES)
                self.gemini_model_var.set(v)
            at = data.get("ai_temperature")
            if at is not None:
                tf = coerce_ai_temperature(at)
                ui = format_ai_temperature_ui(tf)
                if ui in AI_TEMPERATURE_CHOICES_UI:
                    self.ai_temperature_var.set(ui)
                else:
                    self.ai_temperature_var.set(AI_TEMPERATURE_CHOICES_UI[0])
            ows = data.get("openai_web_search")
            if isinstance(ows, bool):
                self.openai_web_search_var.set(ows)
        finally:
            self._prefs_loading = False
        self.refresh_ai_source_description()
        self._refresh_image_prompt_output()
        self._save_gui_preferences()
        self._sync_provider_dependent_widgets()

    def _save_gui_preferences(self):
        if self._prefs_loading:
            return
        self._flush_keyword_extra_to_memory()
        try:
            c = int(self.count_var.get())
        except (tk.TclError, ValueError, TypeError):
            c = 5
        c = max(1, min(50, c))
        try:
            gfp = str(self.guidelines_file_path.resolve())
        except OSError:
            gfp = str(self.guidelines_file_path)
        try:
            sfp = str(self.system_prompt_file_path.resolve())
        except OSError:
            sfp = str(self.system_prompt_file_path)
        data = {
            "keywords": self.keyword_var.get(),
            "crawl_count": c,
            "ai_provider": (self.ai_provider_var.get() or "auto").strip(),
            "openai_model": (self.openai_model_var.get() or "").strip(),
            "gemini_model": (self.gemini_model_var.get() or "").strip(),
            "ai_temperature": coerce_ai_temperature(
                (self.ai_temperature_var.get() or "").strip()
            ),
            "openai_web_search": bool(self.openai_web_search_var.get()),
            "ai_source_folder": self.ai_custom_folder,
            "ai_guidelines_file": gfp,
            "ai_system_prompt_file": sfp,
            "image_common_prompt": self._get_image_common_prompt_text(),
            "keyword_extra_by_keyword": dict(self._keyword_extra_content),
        }
        try:
            GUI_PREFERENCES_PATH.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _on_gui_preference_var_write(self, *_args):
        if self._prefs_loading:
            return
        self._save_gui_preferences()

    def _bind_gui_preferences_traces(self):
        self.keyword_var.trace_add("write", self._on_gui_preference_var_write)
        self.keyword_var.trace_add("write", self._on_keyword_list_changed_schedule_sync)
        self.count_var.trace_add("write", self._on_gui_preference_var_write)
        self.ai_provider_var.trace_add("write", self._on_gui_preference_var_write)
        self.ai_provider_var.trace_add("write", self._on_ai_provider_var_changed)
        self.openai_model_var.trace_add("write", self._on_gui_preference_var_write)
        self.gemini_model_var.trace_add("write", self._on_gui_preference_var_write)
        self.ai_temperature_var.trace_add("write", self._on_gui_preference_var_write)
        self.openai_web_search_var.trace_add("write", self._on_gui_preference_var_write)

    def _on_ai_provider_var_changed(self, *_args):
        if self._prefs_loading:
            return
        self._sync_provider_dependent_widgets()

    def _bind_selection_autosave(self):
        """Spinbox/콤보 등에서 trace가 빠지는 경우를 대비해 명시적으로 저장."""
        self.keyword_entry.bind("<FocusOut>", lambda _e: self._save_gui_preferences())
        self.count_spinbox.bind("<FocusOut>", lambda _e: self._save_gui_preferences())

        def _spin_prefs_later(_evt=None):
            self.root.after(1, self._save_gui_preferences)

        self.count_spinbox.bind("<ButtonRelease-1>", _spin_prefs_later)
        for cmb in (
            self.ai_provider_combo,
            self.openai_model_combo,
            self.gemini_model_combo,
            self.ai_temperature_combo,
        ):
            cmb.bind("<<ComboboxSelected>>", lambda _e: self._save_gui_preferences())

    def refresh_api_usage_display(self):
        """api_usage_stats.json 기준으로 오늘/전체 호출 수 라벨 갱신."""
        if threading.current_thread() is not threading.main_thread():
            self.root.after(0, self.refresh_api_usage_display)
            return
        try:
            st = get_blog_api_usage_stats()
            today = int(st.get("today_calls", 0))
            total = int(st.get("total_calls", 0))
            dk = st.get("day_key", "")
            self.api_usage_stats_var.set(
                f"오늘 AI 글작성 API 호출: {today}회  |  누적(전체): {total}회  (기준일 {dk})"
            )
        except Exception:
            self.api_usage_stats_var.set("AI 글작성 API 호출 누적: (통계를 읽을 수 없음)")

    def refresh_ai_source_description(self):
        if self.ai_custom_folder:
            self.ai_source_desc_var.set(
                "사용자 지정 폴더: " + self.ai_custom_folder
            )
        else:
            kws = self._parse_keywords_only()
            if kws:
                p = Path(get_result_dir(date_included=True, keyword=kws[0]))
                self.ai_source_desc_var.set(
                    "기본: 첫 번째 키워드 결과 폴더 → " + str(p)
                )
            else:
                d = default_crawl_txt_folder()
                if d:
                    self.ai_source_desc_var.set(
                        "키워드 미입력 시: 가장 최근 크롤 폴더 → " + str(d)
                    )
                else:
                    self.ai_source_desc_var.set(
                        "키워드를 입력하면 해당 키워드 result 폴더를 씁니다. "
                        "또는 「폴더 선택」으로 지정하세요."
                    )

    def pick_ai_source_folder(self):
        d = filedialog.askdirectory(title="AI 글작성에 사용할 txt 폴더 선택")
        if d:
            self.ai_custom_folder = d
            self.refresh_ai_source_description()
            self._save_gui_preferences()

    def clear_ai_source_folder(self):
        self.ai_custom_folder = None
        self.refresh_ai_source_description()
        self._save_gui_preferences()

    def _get_ai_txt_source_folder(self):
        if self.ai_custom_folder:
            p = Path(self.ai_custom_folder)
            if p.is_dir():
                return p.resolve()
            return None
        kws = self._parse_keywords_only()
        if kws:
            return Path(get_result_dir(date_included=True, keyword=kws[0])).resolve()
        return default_crawl_txt_folder()

    def _set_crawl_busy_ui(self):
        self.start_button.config(state="disabled")
        self.automation_button.config(state="disabled")
        self.ai_write_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.open_result_button.config(state="disabled")
        self.keyword_entry.config(state="disabled")
        self.count_spinbox.config(state="disabled")
        self.system_prompt_text.config(state="disabled")
        self.load_system_prompt_button.config(state="disabled")
        self.save_system_prompt_button.config(state="disabled")
        self.guidelines_text.config(state="disabled")
        self.load_guidelines_button.config(state="disabled")
        self.save_guidelines_button.config(state="disabled")
        self.ai_provider_combo.config(state="disabled")
        self.openai_model_combo.config(state="disabled")
        self.gemini_model_combo.config(state="disabled")
        self.ai_temperature_combo.config(state="disabled")
        self.pick_folder_button.config(state="disabled")
        self.clear_folder_button.config(state="disabled")
        for _kw, w in list(getattr(self, "_keyword_extra_widgets", {}).items()):
            if isinstance(w, tk.Text):
                w.config(state="disabled")

    def _set_ai_busy_ui(self):
        self.start_button.config(state="disabled")
        self.automation_button.config(state="disabled")
        self.ai_write_button.config(state="disabled")
        self.stop_button.config(state="disabled")
        self.open_result_button.config(state="disabled")
        self.keyword_entry.config(state="disabled")
        self.count_spinbox.config(state="disabled")
        self.system_prompt_text.config(state="disabled")
        self.load_system_prompt_button.config(state="disabled")
        self.save_system_prompt_button.config(state="disabled")
        self.guidelines_text.config(state="disabled")
        self.load_guidelines_button.config(state="disabled")
        self.save_guidelines_button.config(state="disabled")
        self.ai_provider_combo.config(state="disabled")
        self.openai_model_combo.config(state="disabled")
        self.gemini_model_combo.config(state="disabled")
        self.ai_temperature_combo.config(state="disabled")
        self.pick_folder_button.config(state="disabled")
        self.clear_folder_button.config(state="disabled")
        for _kw, w in list(getattr(self, "_keyword_extra_widgets", {}).items()):
            if isinstance(w, tk.Text):
                w.config(state="disabled")

    def start_crawl_only(self):
        """크롤링만 수행"""
        if self.is_crawling or self.ai_busy:
            return
        parsed = self._parse_keywords_count()
        if not parsed:
            return
        keywords, count = parsed
        preview = self._keyword_preview(keywords)
        if not messagebox.askyesno(
            "확인",
            f"키워드 {len(keywords)}개({preview})로\n각 키워드당 {count}개의 블로그를 크롤링하시겠습니까?",
        ):
            return
        self._pending_auto_ai = False
        self._begin_crawl_thread(keywords, count)

    def start_automation_crawl(self):
        """키워드마다 크롤 → AI 글작성 → HTML 생성 → 브라우저 실행 후 다음 키워드로 진행"""
        if self.is_crawling or self.ai_busy:
            return
        parsed = self._parse_keywords_count()
        if not parsed:
            return
        keywords, count = parsed
        preview = self._keyword_preview(keywords)
        if not messagebox.askyesno(
            "자동화 확인",
            f"키워드 {len(keywords)}개({preview}), 각 {count}건씩 순서대로 진행합니다.\n\n"
            f"키워드마다: 크롤링 → 글작성하기(txt 있으면 통합 / 없으면 무원문 생성, .md+.html·HTML 정리) → 브라우저로 열기\n"
            f"를 마친 뒤 다음 키워드로 넘어갑니다.\n계속하시겠습니까?",
        ):
            return
        self._pending_auto_ai = True
        self._begin_crawl_thread(keywords, count)

    def _begin_crawl_thread(self, keywords, count):
        # 키워드 입력 직후 탭 갱신(디바운스)이 남아 있으면 먼저 맞춘 뒤 스냅샷
        job = getattr(self, "_keyword_extra_sync_job", None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except (ValueError, tk.TclError):
                pass
            self._keyword_extra_sync_job = None
        self._sync_keyword_extra_tabs()
        self._save_gui_preferences()
        # Text 위젯이 아직 활성 상태일 때 스냅샷(비활성화 후 .get() 환경 차이 방지)
        self._snapshot_guidelines_and_provider()
        self._progress_context = "automation_crawl" if self._pending_auto_ai else "crawl"
        self.is_crawling = True
        self._set_crawl_busy_ui()
        self.status_var.set("크롤링 준비 중...")
        self.progress_var.set(0)
        self.clear_log()
        self.log_message(f"[시스템 프롬프트] API 전달 분량: {len(self._system_prompt_snapshot)}자")
        if self._guidelines_snapshot:
            self.log_message(f"[지침] API 전달 분량: {len(self._guidelines_snapshot)}자")
        else:
            self.log_message(
                "[지침] API에 넘길 내용이 없습니다. 지침 입력란 또는 현재 지침 txt를 확인하세요."
            )
        self.total_posts = 0
        self.completed_posts = 0
        self.current_post_index = 0
        self.total_delay_seconds = 0.0
        self.crawl_thread = threading.Thread(target=self.run_crawling, args=(keywords, count))
        self.crawl_thread.daemon = True
        self.crawl_thread.start()

    def start_ai_write(self):
        """크롤 txt가 있으면 통합 1편, 없으면 무원문 생성 후 md·html 저장 및 폴더 내 완성 md→html 정리."""
        if self.is_crawling or self.ai_busy:
            return
        folder = self._get_ai_txt_source_folder()
        if not folder:
            messagebox.showwarning(
                "폴더 없음",
                "저장할 폴더를 정할 수 없습니다.\n"
                "키워드를 입력해 result 폴더를 쓰거나, 「폴더 선택」으로 폴더를 지정하세요.",
            )
            return
        folder.mkdir(parents=True, exist_ok=True)
        txts = list_txt_files_in_folder(folder)
        kws = self._parse_keywords_only()
        topic_hint = ", ".join(kws) if kws else folder.name
        if txts:
            if not messagebox.askyesno(
                "확인",
                f"폴더 내 {len(txts)}개의 .txt 원문을 모두 반영해 AI로 블로그 글 1편을 작성합니다.\n"
                f"완성본(.md·.html) 저장 후, 같은 폴더의 `*_completed_*.md`는 모두 HTML로 맞춥니다.\n\n"
                f"{folder}",
            ):
                return
            direct = False
        else:
            if not messagebox.askyesno(
                "확인",
                "참고용 .txt가 없습니다. AI가 주제·지침만으로 블로그 글 1편을 직접 작성합니다.\n"
                "완성본(.md·.html) 저장 후, 같은 폴더의 `*_completed_*.md`는 모두 HTML로 맞춥니다.\n\n"
                f"저장 폴더:\n{folder}\n\n"
                f"주제 힌트: {topic_hint}",
            ):
                return
            direct = True
        self._save_gui_preferences()
        paths = [str(p) for p in txts]
        self._snapshot_guidelines_and_provider()
        self._progress_context = "ai_only"
        self.ai_busy = True
        self._set_ai_busy_ui()
        self.progress_var.set(0)
        self.status_var.set("AI 글작성 준비 중…")
        self.clear_log()
        self.log_message(f"[시스템 프롬프트] API 전달 분량: {len(self._system_prompt_snapshot)}자")
        if self._guidelines_snapshot:
            self.log_message(f"[지침] API 전달 분량: {len(self._guidelines_snapshot)}자")
        else:
            self.log_message(
                "[지침] 비어 있음 — 무원문 모드에서는 주제 힌트 위주로 작성합니다."
            )
        self.log_message(f"소스 폴더: {folder}")
        if direct:
            self.log_message(f"모드: 참고 txt 없음 · 주제 힌트: {topic_hint}")
        else:
            self.log_message(f"처리 대상 txt: {len(paths)}개 (통합 1편 생성)")
        keyword_extra = self._keyword_extra_for_folder(folder)
        self.ai_thread = threading.Thread(
            target=self._run_ai_worker,
            args=(folder, paths, direct, topic_hint, keyword_extra),
        )
        self.ai_thread.daemon = True
        self.ai_thread.start()

    def _run_ai_worker(self, folder, paths, direct=False, topic_hint="", keyword_extra=""):
        load_project_dotenv()
        try:
            prov = self._provider_snapshot or "auto"

            def log(msg):
                self.log_message(msg)

            def prog(pct: float, msg: str):
                self.root.after(
                    0,
                    lambda p=pct, m=msg: self._apply_main_progress(p, f"글작성 · {m}"),
                )

            fpath = folder if isinstance(folder, Path) else Path(folder)
            if direct:
                saved = process_folder_combined_direct(
                    fpath,
                    self._guidelines_snapshot,
                    topic_hint or fpath.name,
                    log=log,
                    provider=prov,
                    system_prompt=self._system_prompt_snapshot,
                    openai_model=self._openai_model_snapshot,
                    gemini_model=self._gemini_model_snapshot,
                    temperature=self._ai_temperature_snapshot,
                    openai_web_search=self._openai_web_search_snapshot,
                    detail_log=log,
                    progress=prog,
                    keyword_extra=keyword_extra,
                )
            else:
                saved = process_folder_combined(
                    fpath,
                    paths,
                    self._guidelines_snapshot,
                    log=log,
                    provider=prov,
                    system_prompt=self._system_prompt_snapshot,
                    openai_model=self._openai_model_snapshot,
                    gemini_model=self._gemini_model_snapshot,
                    temperature=self._ai_temperature_snapshot,
                    openai_web_search=self._openai_web_search_snapshot,
                    detail_log=log,
                    progress=prog,
                    keyword_extra=keyword_extra,
                )
            if saved and fpath.is_dir():
                try:
                    export_html_from_completed_md_in_folder(fpath, log=log)
                except Exception as ex:
                    self.log_message(f"[HTML 정리] 완성 md→html 처리 중 오류: {ex}")
                    traceback.print_exc()
            if saved:
                _md_p, html_p = saved
                self.root.after(
                    0,
                    lambda p=html_p: self._open_html_files_in_browser([p]),
                )
            self.root.after(
                0,
                lambda s=saved, n=len(paths), d=direct: self._ai_write_finished_single(
                    s, n, d
                ),
            )
        except Exception as e:
            err = f"AI 글작성 중 오류: {e}"
            self.log_message(err)
            traceback.print_exc()
            self.root.after(0, lambda m=err: self._ai_write_failed(m))

    def _ai_write_finished_single(self, saved_paths, n_txts, direct_mode=False):
        self.ai_busy = False
        self.refresh_api_usage_display()
        self.status_var.set(f"AI 글작성 종료 | v{__version__}")
        self.open_result_button.config(state="normal")
        if saved_paths:
            md_p, html_p = saved_paths
            if direct_mode:
                head = (
                    "참고 txt 없이 주제·지침만으로 작성한 블로그 글 1편이 저장되었습니다.\n"
                    "같은 폴더의 완성 md 파일은 HTML로 맞춰 두었습니다.\n\n"
                )
            else:
                head = (
                    f"크롤링 원문 txt {n_txts}개를 통합한 블로그 글 1편이 저장되었습니다.\n"
                    "같은 폴더의 완성 md 파일은 HTML로 맞춰 두었습니다.\n\n"
                )
            messagebox.showinfo(
                "완료",
                f"{head}"
                f"MD:\n{md_p}\n\nHTML:\n{html_p}",
            )
        else:
            messagebox.showwarning(
                "실패",
                "AI 글작성이 완료되지 않았습니다.\n로그의 오류 메시지를 확인하세요.",
            )
        self.reset_ui()

    def _ai_write_failed(self, message):
        self.ai_busy = False
        messagebox.showerror("오류", message)
        self.reset_ui()

    def stop_crawling(self):
        """크롤링 중지 (AI 글작성 단계는 중지 버튼 없음)"""
        if self.is_crawling:
            self.is_crawling = False
            self._pending_auto_ai = False
            self.status_var.set("크롤링 중지 중...")
            self.log_message("사용자에 의해 크롤링이 중지되었습니다.")

    def run_crawling(self, keywords, count):
        """크롤링 실행 (별도 스레드에서 실행). 자동화 시 키워드마다 크롤→글작성→HTML→브라우저 순."""
        try:
            load_project_dotenv()
            gui_stream = self._GuiLogStream(self.process_crawler_log_line)
            any_success = False
            do_auto_mode = self._pending_auto_ai
            self._pending_auto_ai = False
            n_kw = len(keywords)
            if do_auto_mode:
                self._auto_seq_n_keywords = n_kw
            else:
                self._auto_seq_n_keywords = 0

            prov = self._provider_snapshot or "auto"

            for keyword_index, keyword in enumerate(keywords, 1):
                if not self.is_crawling:
                    break

                self._auto_seq_kw_i = keyword_index
                self.root.after(0, self.status_var.set, f"키워드 검색 중... ({keyword_index}/{len(keywords)})")
                self.log_message(f"\n=== [{keyword_index}/{len(keywords)}] 키워드 '{keyword}' 시작 ===")
                self.log_message(f"키워드 '{keyword}'로 블로그 검색을 시작합니다...")

                with redirect_stdout(gui_stream), redirect_stderr(gui_stream):
                    post_urls = get_blog_posts_by_keyword(keyword, count)

                if not post_urls:
                    self.log_message(f"'{keyword}' 키워드로 검색된 블로그가 없습니다. 다음 키워드로 이동합니다.")
                    continue

                any_success = True
                self.total_posts = len(post_urls)
                self.completed_posts = 0
                self.current_post_index = 0
                self.progress_var.set(0)
                self.update_progress_display()
                self.log_message(f"검색 완료: {len(post_urls)}개의 블로그를 찾았습니다.")
                for i, url in enumerate(post_urls, 1):
                    self.log_message(f"  {i}. {url}")

                with redirect_stdout(gui_stream), redirect_stderr(gui_stream):
                    saved_list, _out_dir = crawl_multiple_posts(post_urls, keyword)

                if not self.is_crawling:
                    break

                if do_auto_mode:
                    def log_ai(msg):
                        self.log_message(msg)

                    seg = 100.0 / max(n_kw, 1)
                    base_ai = (keyword_index - 1) * seg + 0.5 * seg

                    def prog(pct: float, msg: str) -> None:
                        mapped = base_ai + (pct / 100.0) * (0.42 * seg)
                        st = (
                            f"자동화 · [{keyword_index}/{n_kw}] {keyword} · 글작성 · {msg}"
                        )
                        self.root.after(
                            0,
                            lambda u=mapped, s=st: self._apply_main_progress(u, s),
                        )

                    saved_pair = None
                    folder: Path
                    if saved_list:
                        folder = Path(saved_list[0]).resolve().parent
                        paths_str = [str(p) for p in saved_list]
                        self.log_message(
                            f"\n[자동화] [{keyword_index}/{n_kw}] '{keyword}' — AI 글작성(원문 통합 1편)…"
                        )
                        try:
                            ke = self._keyword_extra_snapshot.get(keyword, "")
                            saved_pair = process_folder_combined(
                                folder,
                                paths_str,
                                self._guidelines_snapshot,
                                log=log_ai,
                                provider=prov,
                                system_prompt=self._system_prompt_snapshot,
                                openai_model=self._openai_model_snapshot,
                                gemini_model=self._gemini_model_snapshot,
                                temperature=self._ai_temperature_snapshot,
                                openai_web_search=self._openai_web_search_snapshot,
                                detail_log=log_ai,
                                progress=prog,
                                keyword_extra=ke,
                            )
                        except Exception as e:
                            self.log_message(f"[자동화] AI 글작성 실패 ({keyword}): {e}")
                            traceback.print_exc()
                    else:
                        folder = Path(
                            get_result_dir(date_included=True, keyword=keyword)
                        ).resolve()
                        folder.mkdir(parents=True, exist_ok=True)
                        self.log_message(
                            f"\n[자동화] [{keyword_index}/{n_kw}] '{keyword}' — "
                            f"저장된 txt 없음, 무원문 AI 글작성…"
                        )
                        try:
                            ke = self._keyword_extra_snapshot.get(keyword, "")
                            saved_pair = process_folder_combined_direct(
                                folder,
                                self._guidelines_snapshot,
                                keyword,
                                log=log_ai,
                                provider=prov,
                                system_prompt=self._system_prompt_snapshot,
                                openai_model=self._openai_model_snapshot,
                                gemini_model=self._gemini_model_snapshot,
                                temperature=self._ai_temperature_snapshot,
                                openai_web_search=self._openai_web_search_snapshot,
                                detail_log=log_ai,
                                progress=prog,
                                keyword_extra=ke,
                            )
                        except Exception as e:
                            self.log_message(
                                f"[자동화] 무원문 AI 글작성 실패 ({keyword}): {e}"
                            )
                            traceback.print_exc()

                    if saved_pair and self.is_crawling:
                        self.log_message(
                            f"[자동화] [{keyword_index}/{n_kw}] '{keyword}' — 완성 md→HTML 정리…"
                        )
                        pct_html = (keyword_index - 1) * seg + 0.92 * seg
                        self.root.after(
                            0,
                            lambda p=pct_html, kw=keyword, ki=keyword_index, nk=n_kw: self._apply_main_progress(
                                p,
                                f"자동화 · [{ki}/{nk}] {kw} · HTML 정리",
                            ),
                        )
                        html_paths = []
                        try:
                            html_paths = export_html_from_completed_md_in_folder(
                                folder,
                                log=log_ai,
                            )
                        except Exception as e:
                            self.log_message(
                                f"[자동화] HTML 정리 실패 ({keyword}): {e}"
                            )
                            traceback.print_exc()

                        if html_paths:
                            paths_copy = list(html_paths)
                            self.root.after(
                                0,
                                lambda paths=paths_copy: self._open_html_files_in_browser(
                                    paths
                                ),
                            )
                        elif saved_pair:
                            _md_p, html_p = saved_pair
                            self.root.after(
                                0,
                                lambda p=html_p: self._open_html_files_in_browser([p]),
                            )

                    pct_done = (keyword_index / max(n_kw, 1)) * 100.0
                    self.root.after(
                        0,
                        lambda p=pct_done, ki=keyword_index, nk=n_kw, kw=keyword: self._apply_main_progress(
                            min(p, 99.9),
                            f"자동화 · 키워드 [{ki}/{nk}] '{kw}' 단계 완료",
                        ),
                    )

            self._auto_seq_n_keywords = 0

            if not self.is_crawling:
                self.log_message("[자동화] 사용자 중지로 이후 단계는 실행하지 않습니다.")
                self.root.after(0, self.reset_ui)
                return

            if not any_success:
                self.root.after(0, lambda: messagebox.showwarning("검색 결과", "입력한 키워드들로 검색된 블로그가 없습니다."))
                self.root.after(0, self.reset_ui)
                return

            if do_auto_mode:
                self.root.after(0, self.automation_all_completed)
            else:
                self.root.after(0, self.crawling_completed)

        except Exception as e:
            error_msg = f"크롤링 중 오류 발생: {str(e)}"
            self.log_message(error_msg)
            self._pending_auto_ai = False
            self._auto_seq_n_keywords = 0
            self.root.after(0, lambda: messagebox.showerror("오류", error_msg))
            self.root.after(0, self.reset_ui)

    def crawling_completed(self):
        """크롤링만 완료 처리"""
        self._progress_context = "idle"
        self.completed_posts = max(self.completed_posts, self.total_posts)
        self.progress_var.set(100)
        self.status_var.set(f"크롤링 완료 | 총 딜레이 {self.total_delay_seconds:.2f}초")
        self.log_message("\n크롤링이 완료되었습니다!")
        self.log_message(f"총 누적 딜레이: {self.total_delay_seconds:.2f}초")
        self.log_message("결과 폴더를 확인해주세요.")
        self.open_result_button.config(state="normal")
        messagebox.showinfo("완료", "블로그 크롤링이 완료되었습니다!\n\n결과 폴더 열기 버튼을 클릭하여 결과를 확인하세요.")
        self.reset_ui()

    def automation_all_completed(self):
        """키워드 순차 자동화(크롤→글작성→HTML→브라우저) 전체 종료."""
        self._progress_context = "idle"
        self.refresh_api_usage_display()
        self.completed_posts = max(self.completed_posts, self.total_posts)
        self.progress_var.set(100)
        self.status_var.set(f"자동화 완료 | 총 딜레이 {self.total_delay_seconds:.2f}초")
        self.log_message("\n[자동화] 모든 키워드 순차 처리가 끝났습니다.")
        self.open_result_button.config(state="normal")
        messagebox.showinfo(
            "완료",
            "자동화가 완료되었습니다.\n"
            "각 키워드마다 크롤링 → 글작성 → HTML 생성 후 브라우저에서 열었습니다.\n"
            "경로는 로그에서 확인할 수 있습니다.",
        )
        self.reset_ui()

    def reset_ui(self):
        """UI를 초기 상태로 리셋"""
        self.is_crawling = False
        self.ai_busy = False
        self._progress_context = "idle"
        self.start_button.config(state="normal")
        self.automation_button.config(state="normal")
        self.ai_write_button.config(state="normal")
        self.stop_button.config(state="disabled")
        self.keyword_entry.config(state="normal")
        self.count_spinbox.config(state="normal")
        self.system_prompt_text.config(state="normal")
        self.load_system_prompt_button.config(state="normal")
        self.save_system_prompt_button.config(state="normal")
        self.guidelines_text.config(state="normal")
        self.load_guidelines_button.config(state="normal")
        self.save_guidelines_button.config(state="normal")
        self.ai_provider_combo.config(state="readonly")
        self.openai_model_combo.config(state="readonly")
        self.gemini_model_combo.config(state="readonly")
        self.ai_temperature_combo.config(state="readonly")
        self.pick_folder_button.config(state="normal")
        self.clear_folder_button.config(state="normal")
        for _kw, w in list(getattr(self, "_keyword_extra_widgets", {}).items()):
            if isinstance(w, tk.Text):
                w.config(state="normal")
        self.refresh_api_usage_display()
        self._sync_provider_dependent_widgets()
        self.status_var.set(f"준비 완료 | v{__version__}")
        self.progress_var.set(0)

    def _open_html_files_in_browser(self, paths) -> None:
        """로컬 HTML 파일을 기본 브라우저(또는 연결된 앱)에서 연다."""
        for p in paths:
            if not p:
                continue
            try:
                path = Path(p).resolve()
                if not path.is_file():
                    self.log_message(f"[브라우저] 파일 없음: {p}")
                    continue
                if os.name == "nt":
                    os.startfile(str(path))
                else:
                    webbrowser.open(path.as_uri())
            except Exception as e:
                self.log_message(f"[브라우저] 열기 실패 {p}: {e}")

    def open_result_folder(self):
        """결과 폴더 열기 (exe/스크립트 위치/result)"""
        result_dir = Path(get_result_dir(date_included=False))
        if result_dir.exists():
            try:
                # Windows에서 폴더 열기
                if os.name == 'nt':
                    os.startfile(str(result_dir))
                # macOS에서 폴더 열기
                elif os.name == 'posix':
                    if sys.platform == 'darwin':  # macOS
                        os.system(f'open "{result_dir}"')
                    else:  # Linux
                        os.system(f'xdg-open "{result_dir}"')
                else:
                    # 크로스 플랫폼 방식
                    webbrowser.open(str(result_dir))
            except Exception as e:
                messagebox.showerror("오류", f"결과 폴더를 열 수 없습니다: {e}")
        else:
            messagebox.showwarning("경고", "결과 폴더가 존재하지 않습니다.")


def main():
    """메인 함수"""
    load_project_dotenv()
    root = tk.Tk()
    app = BlogCrawlerGUI(root)

    # 창 닫기 이벤트 처리
    def on_closing():
        if app.ai_busy:
            messagebox.showinfo(
                "안내",
                "AI 글작성 또는 HTML 변환이 진행 중입니다. 완료될 때까지 기다리거나 작업 관리자에서 종료해 주세요.",
            )
            return
        if app.is_crawling:
            if messagebox.askyesno("확인", "크롤링이 진행 중입니다. 정말 종료하시겠습니까?"):
                app.stop_crawling()
                app._save_gui_preferences()
                root.destroy()
        else:
            app._save_gui_preferences()
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        traceback.print_exc()
        err_msg = f"프로그램 시작 중 오류:\n\n{e}"
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("오류", err_msg)
        except Exception:
            print(err_msg, file=sys.stderr)
        sys.exit(1)
