"""Native shopping-connect pages; browser work never runs on the GUI thread."""
import threading
from dataclasses import replace
from urllib.parse import urlsplit
from shiboken6 import isValid

from PySide6.QtCore import QThread, Signal, Qt, QUrl, QSize
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QTextEdit, QPushButton, QComboBox, QCheckBox, QListWidget, QListWidgetItem,
    QSplitter, QFormLayout, QMessageBox, QSizePolicy, QScrollArea, QSpinBox, QFileDialog, QTabWidget, QInputDialog)

import config
import pipeline
from pipeline import PipelineContext
from source.core import shopping_session as shopping
from GetImage import image_search


class ShoppingWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, operation, settings, keyword='', sort='relevance', parent=None, product=None):
        super().__init__(parent)
        self.operation, self.settings, self.keyword, self.sort = operation, settings, keyword, sort
        self.product = product
        self.cancel_event = threading.Event()

    def run(self):
        try:
            service = shopping.ShoppingService(self.settings)
            if self.operation == 'connect':
                result = service.connect(cancel_event=self.cancel_event)
            elif self.operation == 'open':
                result = service.open_site(cancel_event=self.cancel_event)
            elif self.operation == 'issue':
                result = replace(self.product, affiliate_url=service.issue_link(self.product, cancel_event=self.cancel_event))
            else:
                result = service.search(self.keyword, sort=self.sort, max_items=300,
                                        cancel_event=self.cancel_event)
            self.succeeded.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc) or type(exc).__name__)


class TravelWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, operation, settings, *, keyword='', offer=None, parent=None):
        super().__init__(parent)
        self.operation, self.settings, self.keyword, self.offer = operation, settings, keyword, offer
        self.cancel_event = threading.Event()

    def run(self):
        try:
            from source.core.travel_session import TravelService
            service = TravelService(self.settings)
            if self.operation == 'travel_search':
                result = service.search(self.keyword, cancel_event=self.cancel_event)
            elif self.operation == 'travel_issue':
                result = service.resolve_affiliate_link(self.offer, cancel_event=self.cancel_event)
            else:
                result = service.open_official_site(cancel_event=self.cancel_event)
            self.succeeded.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc) or type(exc).__name__)


class PublisherLoginWorker(QThread):
    """Runs the publisher's visible, manual-login flow without blocking the UI."""
    completed = Signal(bool)
    progress = Signal(str)

    def __init__(self, account_id, parent=None, *, logout=False):
        super().__init__(parent)
        self.account_id = account_id
        self.logout = logout
        self.cancel_event = threading.Event()

    def run(self):
        def on_output(line):
            if line == '진행 상황: 로그인 성공':
                line = '네이버 인증 확인됨 — 로그인 작업 마무리 중…'
            self.progress.emit(line)
        if self.logout:
            self.completed.emit(pipeline.run_logout(cancel_event=self.cancel_event, on_output=self.progress.emit))
            return
        self.completed.emit(pipeline.run_prelogin(self.account_id, cancel_event=self.cancel_event, interactive=True, on_output=on_output))


class ShoppingPages:
    """Owns the pages and shared worker lifetime; application callbacks avoid circular imports."""
    def __init__(self, enqueue, populate_account, manage_accounts, populate_agents):
        self.enqueue = enqueue
        self.populate_account = populate_account
        self.manage_accounts = manage_accounts
        self.worker = None
        self._image_generation = 0
        self._travel_generation = 0
        self._travel_offers = []
        self._known_links = {}
        self.settings = shopping.load_settings()
        self.page = QWidget()
        self.image_network = QNetworkAccessManager(self.page)
        self.settings_page = QWidget()
        self._build_writing(populate_agents)
        self._build_settings()

    def _build_writing(self, populate_agents):
        layout = QVBoxLayout(self.page)
        title = QLabel('네이버 커넥트 글쓰기')
        title.setStyleSheet('font-size: 19px; font-weight: bold')
        layout.addWidget(title)
        split = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(split, 1)
        left, right = QWidget(), QWidget()
        left.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        right.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        left_scroll = QScrollArea(); left_scroll.setWidgetResizable(True); left_scroll.setWidget(left)
        left_scroll.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        split.addWidget(left_scroll); split.addWidget(right); split.setSizes([500, 500])
        form = QFormLayout(left)
        self.title_edit = QLineEdit()
        self.tone_combo = QComboBox(); self.tone_combo.addItems(['정보 안내형', '친근한 소개형', '비교 안내형'])
        self.links_edit = QTextEdit(); self.links_edit.setPlaceholderText('상품을 추가하거나 확인한 상품 정보와 링크를 직접 입력하세요.'); self.links_edit.setMaximumHeight(200)
        self.memo_edit = QTextEdit(); self.memo_edit.setPlaceholderText('추가 요청 사항'); self.memo_edit.setMaximumHeight(150)
        self.use_crawling_checkbox = QCheckBox('크롤링 사용')
        self.crawl_count_spin = QSpinBox(); self.crawl_count_spin.setRange(1, 100); self.crawl_count_spin.setValue(config.CRAWLER_COUNT_DEFAULT)
        self.crawl_count_spin.setEnabled(False); self.use_crawling_checkbox.toggled.connect(self.crawl_count_spin.setEnabled)
        self.generate_images_checkbox = QCheckBox('AI 이미지 생성')
        self.image_gen_count_spin = QSpinBox(); self.image_gen_count_spin.setRange(1, 10); self.image_gen_count_spin.setEnabled(False)
        self.search_images_checkbox = QCheckBox('이미지 검색')
        self.generate_images_checkbox.toggled.connect(lambda _: self.image_gen_count_spin.setEnabled(self.generate_images_checkbox.isChecked() or self.search_images_checkbox.isChecked()))
        self.search_images_checkbox.toggled.connect(lambda _: self.image_gen_count_spin.setEnabled(self.generate_images_checkbox.isChecked() or self.search_images_checkbox.isChecked()))
        self.search_download_checkbox = QCheckBox('다운로드'); self.search_download_checkbox.setChecked(True)
        self.search_timing_combo = QComboBox(); self.search_timing_combo.addItem('글쓰기 전', 'before'); self.search_timing_combo.addItem('글쓰기 후', 'after')
        self.search_download_checkbox.setEnabled(False); self.search_timing_combo.setEnabled(False)
        self.search_images_checkbox.toggled.connect(self.search_download_checkbox.setEnabled)
        self.search_images_checkbox.toggled.connect(self.search_timing_combo.setEnabled)
        self.generate_images_checkbox.toggled.connect(lambda checked: self.search_images_checkbox.setChecked(False) if checked else None)
        self.search_images_checkbox.toggled.connect(lambda checked: self.generate_images_checkbox.setChecked(False) if checked else None)
        self.image_paths = []
        self.attach_button = QPushButton('이미지 첨부'); self.attach_button.clicked.connect(self.attach_images)
        self.clear_images_button = QPushButton('첨부 초기화'); self.clear_images_button.clicked.connect(self.clear_images)
        self.attachment_status = QLabel('첨부 이미지 0장')
        self.search_before_button = QPushButton('글쓰기 전 이미지 검색·첨부'); self.search_before_button.clicked.connect(self.search_before_images)
        self.account_combo = QComboBox(); self.populate_account(self.account_combo, None, enabled=True)
        self.model_combo = QComboBox()
        for label, value in config.AI_MODEL_CHOICES: self.model_combo.addItem(label, value)
        self.agents_combo = QComboBox(); populate_agents(self.agents_combo, None)
        self.review_checkbox = QCheckBox('발행 전 초안 검토'); self.review_checkbox.setChecked(True)
        self.submit_button = QPushButton(); self.review_checkbox.toggled.connect(self._update_submit_label)
        self._update_submit_label(); self.submit_button.clicked.connect(self.submit)
        form.addRow('제목', self.title_edit); form.addRow('글 유형', self.tone_combo)
        form.addRow(self.use_crawling_checkbox, self.crawl_count_spin)
        form.addRow(self.generate_images_checkbox, self.image_gen_count_spin)
        form.addRow(self.search_images_checkbox, self.search_download_checkbox)
        form.addRow('이미지 검색 시점', self.search_timing_combo)
        form.addRow(self.search_before_button); form.addRow(self.attach_button, self.clear_images_button); form.addRow(self.attachment_status)
        form.addRow('선택 상품 및 링크', self.links_edit); form.addRow('추가 메모', self.memo_edit)
        form.addRow('네이버 발행 계정', self.account_combo); form.addRow('AI 모델', self.model_combo)
        form.addRow('작성 지침', self.agents_combo); form.addRow(self.review_checkbox); form.addRow(self.submit_button)
        tabs_layout = QVBoxLayout(right)
        self.connect_tabs = QTabWidget()
        tabs_layout.addWidget(self.connect_tabs)
        shopping_tab, travel_tab = QWidget(), QWidget()
        self.connect_tabs.addTab(shopping_tab, '쇼핑 커넥트')
        self.connect_tabs.addTab(travel_tab, '여행 커넥트')
        rlayout = QVBoxLayout(shopping_tab)
        rlayout.addWidget(QLabel('상품 검색'))
        search_row = QHBoxLayout(); self.keyword_edit = QLineEdit(); self.keyword_edit.setPlaceholderText('검색어')
        self.sort_combo = QComboBox()
        for label, value in [('관련도','relevance'), ('수수료','commission'), ('리뷰','reviews'), ('낮은 가격','price_low'), ('높은 가격','price_high')]: self.sort_combo.addItem(label, value)
        self.search_button = QPushButton('검색'); self.search_button.clicked.connect(self.search)
        self.keyword_edit.returnPressed.connect(self.search)
        search_row.addWidget(self.keyword_edit, 1); search_row.addWidget(self.sort_combo); search_row.addWidget(self.search_button)
        rlayout.addLayout(search_row)
        self.result_status = QLabel('검색어를 입력하세요. 최대 300개까지 수집한 결과를 정렬합니다.'); self.result_status.setWordWrap(True)
        rlayout.addWidget(self.result_status)
        self.results = QListWidget(); self.results.currentItemChanged.connect(self._update_image_button); rlayout.addWidget(self.results, 1)
        self.results.itemDoubleClicked.connect(self.open_product)
        self.results.setIconSize(QSize(72, 72))
        self.image_button = QPushButton('상품 이미지 없음'); self.image_button.setEnabled(False); self.image_button.clicked.connect(self.open_image); rlayout.addWidget(self.image_button)
        self.add_button = QPushButton('선택 상품 추가'); self.add_button.clicked.connect(self.add_selected); rlayout.addWidget(self.add_button)
        self.cancel_button = QPushButton('검색·연결 취소'); self.cancel_button.setEnabled(False); self.cancel_button.clicked.connect(self.cancel)
        rlayout.addWidget(self.cancel_button)
        self._build_travel(travel_tab)
        self.account_combo.currentIndexChanged.connect(self._invalidate_travel)

    def _build_travel(self, tab):
        layout = QVBoxLayout(tab)
        layout.addWidget(QLabel('여행 검색'))
        row = QHBoxLayout()
        self.travel_keyword_edit = QLineEdit(); self.travel_keyword_edit.setPlaceholderText('여행지 또는 패키지·가이드투어 검색')
        self.travel_search_button = QPushButton('검색'); self.travel_search_button.clicked.connect(self.search_travel)
        self.travel_keyword_edit.returnPressed.connect(self.search_travel)
        row.addWidget(self.travel_keyword_edit, 1); row.addWidget(self.travel_search_button); layout.addLayout(row)
        self.travel_destinations = {
            '한국': ('제주', '부산'), '일본': ('오사카', '도쿄', '후쿠오카'),
            '동남아': ('다낭', '방콕', '싱가포르'), '유럽': ('파리', '런던', '로마'),
        }
        region_row = QHBoxLayout(); region_row.addWidget(QLabel('지역별 인기 여행지'))
        self.travel_region_combo = QComboBox(); self.travel_region_combo.addItems(list(self.travel_destinations))
        region_row.addWidget(self.travel_region_combo); region_row.addStretch(); layout.addLayout(region_row)
        favorites = QHBoxLayout(); self.travel_favorite_buttons = []
        for index in range(3):
            button = QPushButton()
            button.clicked.connect(lambda _checked=False, button=button: self._travel_favorite(button.text()))
            favorites.addWidget(button); self.travel_favorite_buttons.append(button)
        layout.addLayout(favorites)
        self.travel_region_combo.currentTextChanged.connect(self._update_travel_destinations)
        self._update_travel_destinations(self.travel_region_combo.currentText())
        self.travel_sort_combo = QComboBox()
        self.travel_sort_combo.addItem('서비스 원본 순서', 'original')
        self.travel_sort_combo.addItem('비교 가능한 가격 낮은 순', 'price')
        self.travel_sort_combo.model().item(1).setEnabled(False)
        self.travel_sort_combo.setToolTip('동일한 통화·단위·기준의 가격이 둘 이상일 때 가격순을 사용할 수 있습니다.')
        self.travel_sort_combo.currentIndexChanged.connect(self._render_travel)
        layout.addWidget(self.travel_sort_combo)
        self.travel_status = QLabel('여행지를 입력하거나 인기 여행지를 선택하세요.')
        self.travel_status.setWordWrap(True); layout.addWidget(self.travel_status)
        self.travel_results = QListWidget(); self.travel_results.itemDoubleClicked.connect(self.open_travel_detail)
        layout.addWidget(self.travel_results, 1)
        self.travel_detail_button = QPushButton('선택 여행 상품 상세 열기'); self.travel_detail_button.clicked.connect(self.open_selected_travel_detail)
        self.travel_add_button = QPushButton('선택 여행 상품 추가'); self.travel_add_button.clicked.connect(self.add_travel_selected)
        self.travel_official_button = QPushButton('여행 커넥트 공식 사이트 열기'); self.travel_official_button.clicked.connect(lambda: self._start_travel('travel_open'))
        self.travel_paste_button = QPushButton('직접 발급한 여행 제휴 링크 붙여넣기'); self.travel_paste_button.clicked.connect(self.paste_travel_link)
        self.travel_cancel_button = QPushButton('검색·발급 취소'); self.travel_cancel_button.setEnabled(False); self.travel_cancel_button.clicked.connect(self.cancel)
        for widget in (self.travel_detail_button, self.travel_add_button, self.travel_official_button, self.travel_paste_button, self.travel_cancel_button): layout.addWidget(widget)

    def _update_travel_destinations(self, region):
        destinations = self.travel_destinations.get(region, ())
        for index, button in enumerate(self.travel_favorite_buttons):
            button.setVisible(index < len(destinations))
            if index < len(destinations): button.setText(destinations[index])

    def _travel_favorite(self, name):
        self.travel_keyword_edit.setText(name); self.search_travel()

    def _start_travel(self, operation, *, keyword='', offer=None):
        if self.active_worker(): return
        generation = self._travel_generation
        worker = TravelWorker(operation, self.settings, keyword=keyword, offer=offer, parent=self.page)
        self.worker = worker
        worker.travel_generation = generation
        def success(result):
            if generation != self._travel_generation or worker.cancel_event.is_set(): return
            if operation == 'travel_search':
                self._travel_offers = list(result); self._update_travel_sort(); self._render_travel()
                self.travel_status.setText(f'검색 결과 {len(self._travel_offers)}개' if self._travel_offers else '검색 결과가 없습니다.')
            elif operation == 'travel_issue':
                if not pipeline.is_connect_affiliate_url(result.url, 'travel'):
                    self.travel_status.setText('발급된 링크의 형식을 확인할 수 없습니다. 공식 사이트에서 확인하세요.'); return
                if not self._append_link(result.url, 'travel'):
                    self.travel_status.setText('이 링크는 이미 다른 유형으로 추가되어 있습니다. 유형을 바꾸려면 링크를 삭제한 뒤 다시 추가하세요.'); return
                self.travel_status.setText('기존 제휴 링크를 재사용했습니다.' if result.reused else '제휴 링크를 발급해 추가했습니다.')
            else:
                self.travel_status.setText('공식 사이트의 여행 커넥트 → 상품 찾기 또는 발급 링크 관리에서 링크를 복사해 붙여넣으세요.')
        def failure(message):
            if generation == self._travel_generation and not worker.cancel_event.is_set():
                self.travel_status.setText(f'작업 실패 또는 취소: {message}\n연결이나 여행 권한을 확인하거나 공식 사이트에서 직접 발급해 붙여넣으세요.')
        worker.succeeded.connect(success); worker.failed.connect(failure); worker.finished.connect(lambda: self._finished(worker))
        self._set_busy(True); worker.start()

    def search_travel(self):
        if self.active_worker(): return
        keyword = self.travel_keyword_edit.text().strip()
        if not keyword:
            self.travel_status.setText('여행지를 입력하세요.'); return
        self._travel_generation += 1; self._travel_offers = []; self._update_travel_sort(); self.travel_results.clear()
        self.travel_status.setText('여행 상품을 검색하는 중…')
        self._start_travel('travel_search', keyword=keyword)

    def _update_travel_sort(self):
        counts = {}
        for offer in self._travel_offers:
            if offer.comparable_price is not None and offer.price_basis:
                counts[offer.price_basis] = counts.get(offer.price_basis, 0) + 1
        enabled = any(count >= 2 for count in counts.values())
        if not enabled: self.travel_sort_combo.setCurrentIndex(0)
        self.travel_sort_combo.model().item(1).setEnabled(enabled)

    def _render_travel(self, *_args):
        offers = list(self._travel_offers)
        if self.travel_sort_combo.currentData() == 'price':
            # Only compare prices that explicitly share a currency/unit/basis.
            groups = {}
            for index, offer in enumerate(offers):
                if offer.comparable_price is not None and offer.price_basis:
                    groups.setdefault(offer.price_basis, []).append(index)
            for indices in groups.values():
                ordered = sorted((offers[i] for i in indices), key=lambda offer: offer.comparable_price)
                for index, offer in zip(indices, ordered): offers[index] = offer
        self.travel_results.clear()
        for offer in offers:
            item = QListWidgetItem(f'{offer.title}\n판매처: {offer.seller or "정보 없음"} · 가격: {offer.display_price or "정보 없음"}')
            item.setData(Qt.ItemDataRole.UserRole, offer); self.travel_results.addItem(item)

    def open_travel_detail(self, item):
        offer = item.data(Qt.ItemDataRole.UserRole)
        if shopping._safe_http_url(offer.detail_url): QDesktopServices.openUrl(QUrl(offer.detail_url))

    def open_selected_travel_detail(self):
        item = self.travel_results.currentItem()
        if item is not None: self.open_travel_detail(item)

    def add_travel_selected(self):
        item = self.travel_results.currentItem()
        if item is None or self.active_worker(): return
        self.travel_status.setText('여행 제휴 링크를 확인·발급하는 중…')
        self._start_travel('travel_issue', offer=item.data(Qt.ItemDataRole.UserRole))

    def paste_travel_link(self):
        url, accepted = QInputDialog.getText(self.page, '여행 제휴 링크 추가', '공식 사이트에서 발급한 여행 제휴 링크:')
        if not accepted: return
        url = url.strip()
        if not pipeline.is_connect_affiliate_url(url, 'travel'):
            self.travel_status.setText('확인 가능한 네이버 제휴 단축 링크(https://naver.me/…)를 입력하세요.'); return
        if not self._append_link(url, 'travel'):
            self.travel_status.setText('이 링크는 이미 다른 유형으로 추가되어 있습니다. 유형을 바꾸려면 링크를 삭제한 뒤 다시 추가하세요.'); return
        self.travel_status.setText('직접 발급한 여행 제휴 링크를 추가했습니다.')

    def _append_link(self, url, kind):
        text = self.links_edit.toPlainText()
        present = pipeline.present_connect_affiliate_links(text, self._known_links)
        if url in present and present[url] != kind:
            return False
        if not pipeline.present_connect_affiliate_links(text, {url: kind}):
            self.links_edit.append(url)
        self._known_links[url] = kind
        return True

    def _invalidate_travel(self, *_args):
        self._travel_generation += 1
        if self.worker and getattr(self.worker, 'operation', '').startswith('travel_') and self.worker.isRunning():
            self.worker.cancel_event.set()
        self._travel_offers = []; self._update_travel_sort(); self.travel_results.clear()
        self.travel_status.setText('계정이 변경되어 여행 검색 결과를 초기화했습니다.')

    def _set_busy(self, busy):
        operation = getattr(self.worker, 'operation', '')
        self.submit_button.setEnabled(not (busy and operation in ('issue', 'travel_issue')))
        for widget in (self.search_button, self.publisher_login_button, self.publisher_logout_button, self.login_button,
                       self.add_button, self.travel_search_button, self.travel_add_button, self.travel_official_button):
            widget.setEnabled(not busy)
        self.travel_region_combo.setEnabled(not busy)
        for button in self.travel_favorite_buttons: button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy); self.travel_cancel_button.setEnabled(busy)

    def _build_settings(self):
        layout = QVBoxLayout(self.settings_page)
        title = QLabel('설정'); title.setStyleSheet('font-size: 19px; font-weight: bold'); layout.addWidget(title)
        self.publisher_login_button = QPushButton('네이버 로그인'); self.publisher_login_button.clicked.connect(self.login_publisher)
        self.publisher_logout_button = QPushButton('로그아웃'); self.publisher_logout_button.clicked.connect(self.logout_publisher)
        publisher_row = QHBoxLayout(); publisher_row.addWidget(self.publisher_login_button); publisher_row.addWidget(self.publisher_logout_button); layout.addLayout(publisher_row)
        self.publisher_login_status = QLabel('네이버 로그인: 확인 전 — 글쓰기·쇼핑 커넥트 공통'); self.publisher_login_status.setWordWrap(True); layout.addWidget(self.publisher_login_status)
        self.shopping_status = QLabel('쇼핑 커넥트: 연결 확인 전 — 네이버 로그인 계정을 사용합니다.')
        self.shopping_status.setWordWrap(True); layout.addWidget(self.shopping_status)
        self.login_button = QPushButton('쇼핑 커넥트 연결 확인'); self.login_button.clicked.connect(self.connect); layout.addWidget(self.login_button)
        self.brandconnect_button = QPushButton('브랜드커넥트 열기')
        self.brandconnect_button.clicked.connect(self.open_brandconnect); layout.addWidget(self.brandconnect_button)
        layout.addStretch()

    def _update_submit_label(self):
        self.submit_button.setText('초안 생성·검토' if self.review_checkbox.isChecked() else '작성 후 자동 발행')

    def save_settings(self):
        # Connection identity is discovered from the current Naver session.
        return True

    def open_brandconnect(self):
        self._start('open')

    def _start(self, operation, keyword='', product=None):
        if self.worker is not None and self.worker.isRunning(): return
        self.worker = ShoppingWorker(operation, self.settings, keyword, self.sort_combo.currentData(), self.page, product)
        worker = self.worker
        callback = {'connect': self._connected, 'search': self._searched, 'issue': self._product_added, 'open': lambda _: None}[operation]
        worker.succeeded.connect(lambda result: callback(result) if not worker.cancel_event.is_set() else None)
        worker.failed.connect(lambda message: self._failed(message) if not worker.cancel_event.is_set() else None)
        worker.finished.connect(lambda: self._finished(worker))
        self._set_busy(True)
        self.worker.start()

    def connect(self):
        if self.save_settings():
            self.shopping_status.setText('쇼핑 커넥트: 네이버 로그인 계정으로 연결 확인 중…')
            self._start('connect')

    def login(self):
        self.connect()

    def login_publisher(self):
        if self.worker is not None and self.worker.isRunning():
            self.publisher_login_status.setText('네이버 로그인이 이미 진행 중입니다.')
            return
        account_id = self.account_combo.currentData() or pipeline.default_account_id()
        self.publisher_login_status.setText('발행용 브라우저에서 직접 로그인해 주세요.')
        self.worker = PublisherLoginWorker(account_id, self.settings_page)
        self.worker.progress.connect(self.publisher_login_status.setText)
        self.worker.completed.connect(self._publisher_login_finished)
        self.worker.finished.connect(self._finished)
        self._set_busy(True)
        self.worker.start()

    def logout_publisher(self):
        if self.worker is not None and self.worker.isRunning():
            return
        self.publisher_login_status.setText('로그아웃 확인 중…')
        self.worker = PublisherLoginWorker(None, self.settings_page, logout=True)
        self.worker.progress.connect(self.publisher_login_status.setText)
        self.worker.completed.connect(self._publisher_logout_finished)
        self.worker.finished.connect(self._finished)
        self._set_busy(True)
        self.worker.start()

    def _publisher_logout_finished(self, success):
        if success:
            self.publisher_login_status.setText('네이버 로그아웃됨 — 네이버 로그인 버튼으로 다른 ID로 로그인하세요.')
            self._invalidate_shopping()
        else:
            self.publisher_login_status.setText('로그아웃 확인 실패: ' + self.publisher_login_status.text())

    def _invalidate_shopping(self):
        self._invalidate_travel()
        self.results.clear()
        self.settings.account_root = ''
        self.shopping_status.setText('쇼핑 커넥트: 연결 확인 전 — 현재 네이버 계정을 사용합니다.')

    def _publisher_login_finished(self, success):
        if success:
            self.publisher_login_status.setText('네이버 인증 완료 — 글쓰기·쇼핑 커넥트 공통 로그인')
            self._invalidate_shopping()
        else:
            self.publisher_login_status.setText('인증 확인 실패: ' + self.publisher_login_status.text())

    def search(self):
        if self.worker is not None and self.worker.isRunning(): return
        keyword = self.keyword_edit.text().strip()
        if not keyword:
            self.result_status.setText('검색어를 입력하세요.'); return
        self._image_generation += 1
        self.results.clear(); self.result_status.setText('실제 상품을 검색하는 중…')
        self._start('search', keyword)

    def _connected(self, settings):
        self.settings = settings
        self.shopping_status.setText('쇼핑 커넥트: 연결됨 — 네이버 로그인 계정 사용')
        self.result_status.setText('쇼핑 커넥트 연결 확인됨')

    def _searched(self, result):
        self.shopping_status.setText('쇼핑 커넥트: 연결됨 — 네이버 로그인 계정 사용')
        self._image_generation += 1
        generation = self._image_generation
        self.results.clear()
        for product in result.products:
            link_status = '제휴 링크 있음' if product.affiliate_url else '제휴 링크 미발급'
            item = QListWidgetItem(f'{product.title}\n가격: {product.price or "정보 없음"} · 수수료: {product.commission or "정보 없음"} · {link_status}')
            item.setData(Qt.ItemDataRole.UserRole, product); self.results.addItem(item)
            if shopping._safe_http_url(product.image_url):
                request = QNetworkRequest(QUrl(product.image_url))
                request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                                     QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
                reply = self.image_network.get(request)
                row = self.results.count() - 1
                reply.finished.connect(lambda reply=reply, row=row, generation=generation:
                                       self._image_loaded(reply, row, generation))
        total = f'전체 {result.total}개 중 ' if result.total is not None else ''
        self.result_status.setText(f'{total}수집 {result.collected}개 · 표시 {len(result.products)}개' + (f' · {result.warning}' if result.warning else '') + (' · 검색 결과가 없습니다' if not result.products else ''))

    def _image_loaded(self, reply, row, generation):
        try:
            if not isValid(self.results) or generation != self._image_generation or reply.error() != QNetworkReply.NetworkError.NoError:
                return
            item = self.results.item(row)
            if item is not None:
                pixmap = QPixmap()
                if pixmap.loadFromData(reply.readAll()):
                    item.setIcon(QIcon(pixmap))
        finally:
            reply.deleteLater()

    def _failed(self, message):
        self.shopping_status.setText('쇼핑 커넥트: 연결 확인 필요')
        if self.worker and getattr(self.worker, 'operation', '') in ('connect', 'open'):
            self.shopping_status.setText(f'쇼핑 커넥트: {message}')
        else: self.result_status.setText(f'작업 실패 또는 취소: {message}')

    def _finished(self, worker=None):
        worker = worker or self.worker
        if worker is not self.worker: return
        if worker and worker.cancel_event.is_set():
            operation = getattr(worker, 'operation', '')
            if operation.startswith('travel_'):
                if getattr(worker, 'travel_generation', None) == self._travel_generation:
                    self.travel_status.setText('취소 요청 완료 — 결과를 추가하지 않았습니다.' if operation == 'travel_issue' else '여행 작업이 취소되었습니다.')
            elif operation:
                if operation in ('connect', 'open'):
                    self.shopping_status.setText('쇼핑 커넥트 작업이 취소되었습니다.')
                else:
                    self.result_status.setText('취소 요청 완료 — 결과를 추가하지 않았습니다.' if operation == 'issue' else '쇼핑 작업이 취소되었습니다.')
            else:
                self.publisher_login_status.setText('네이버 로그인 작업이 취소되었습니다.')
        self._set_busy(False)

    def cancel(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel_event.set()
            operation = getattr(self.worker, 'operation', '')
            if operation.startswith('travel_'):
                if getattr(self.worker, 'travel_generation', None) == self._travel_generation:
                    self.travel_status.setText('취소 요청 중…')
            elif operation in ('connect', 'open'):
                self.shopping_status.setText('취소 요청 중…')
            elif operation:
                self.result_status.setText('취소 요청 중…')
            else:
                self.publisher_login_status.setText('취소 요청 중…')

    def active_worker(self):
        return self.worker if self.worker is not None and self.worker.isRunning() else None

    def _image_url(self):
        item = self.results.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole).image_url if item else None
        try:
            parsed = urlsplit(value or '')
            if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password and parsed.port in (None, 443):
                return value
        except ValueError:
            pass
        return None

    def _update_image_button(self, *_args):
        available = bool(self._image_url())
        self.image_button.setEnabled(available)
        self.image_button.setText('상품 이미지 열기' if available else '상품 이미지 없음')

    def open_image(self):
        url = self._image_url()
        if url: QDesktopServices.openUrl(QUrl(url))

    def open_product(self, item):
        product = item.data(Qt.ItemDataRole.UserRole)
        url = product.detail_url or product.original_url
        if shopping._safe_http_url(url): QDesktopServices.openUrl(QUrl(url))

    def attach_images(self):
        paths, _ = QFileDialog.getOpenFileNames(self.page, '이미지 첨부', '', 'Images (*.png *.jpg *.jpeg *.webp *.gif *.bmp)')
        self.image_paths.extend(p for p in paths if p not in self.image_paths)
        self.attachment_status.setText(f'첨부 이미지 {len(self.image_paths)}장')

    def clear_images(self):
        self.image_paths.clear(); self.attachment_status.setText('첨부 이미지 0장')

    def search_before_images(self):
        if not self.search_images_checkbox.isChecked() or self.search_timing_combo.currentData() != 'before':
            self.attachment_status.setText('이미지 검색과 글쓰기 전을 선택하세요.'); return
        keyword = self.title_edit.text().strip()
        if not keyword:
            self.attachment_status.setText('제목을 입력하세요.'); return
        try:
            paths = image_search.download_search_images(keyword, self.image_gen_count_spin.value())
            self.image_paths.extend(str(p) for p in paths if str(p) not in self.image_paths)
            self.attachment_status.setText(f'첨부 이미지 {len(self.image_paths)}장')
        except Exception as exc:
            self.attachment_status.setText(f'이미지 검색 실패: {exc}')

    def add_selected(self):
        item = self.results.currentItem()
        if not item: return
        p = item.data(Qt.ItemDataRole.UserRole)
        self.result_status.setText('선택한 상품의 제휴 링크를 확인·발급하는 중…')
        self._start('issue', product=p)

    def _product_added(self, p):
        if not pipeline.is_connect_affiliate_url(p.affiliate_url, 'shopping'):
            self.result_status.setText('발급된 링크의 형식을 확인할 수 없습니다.'); return
        if not self._append_link(p.affiliate_url, 'shopping'):
            self.result_status.setText('이 링크는 이미 다른 유형으로 추가되어 있습니다. 유형을 바꾸려면 링크를 삭제한 뒤 다시 추가하세요.'); return
        self.result_status.setText('제휴 링크를 확인하고 상품을 추가했습니다.')

    def submit(self):
        if not self.submit_button.isEnabled():
            self.result_status.setText('제휴 링크 확인·발급이 끝난 뒤 글쓰기 작업을 추가하세요.')
            return
        title = self.title_edit.text().strip(); links = self.links_edit.toPlainText().strip()
        if not title or not links:
            QMessageBox.warning(self.page, '입력 확인', '제목과 상품 정보 또는 링크를 입력하세요.'); return
        comment = (f'글 유형: {self.tone_combo.currentText()}\n확인된 상품 정보 및 사용자가 입력한 링크:\n{links}'
                   f'\n추가 메모: {self.memo_edit.toPlainText().strip()}'
                   '\n확인되지 않은 가격, 후기, 사용 경험이나 제휴 링크를 만들어 내지 마세요. '
                   '제휴 링크가 미발급된 상품은 제휴 링크인 것처럼 표시하지 마세요.')
        known_links = pipeline.present_connect_affiliate_links(links, self._known_links)
        context = PipelineContext(keyword=title, comment=comment, image_paths=list(self.image_paths),
                                  connect_affiliate_links=known_links,
                                  use_crawling=self.use_crawling_checkbox.isChecked(), crawl_count=self.crawl_count_spin.value(),
                                  generate_images=self.generate_images_checkbox.isChecked(), image_gen_count=self.image_gen_count_spin.value(),
                                  search_images=self.search_images_checkbox.isChecked(), search_images_download=self.search_download_checkbox.isChecked(),
                                  search_images_timing=self.search_timing_combo.currentData(),
                                  ai_model=self.model_combo.currentData(), account_id=self.account_combo.currentData(),
                                  agents_md_path=self.agents_combo.currentData(), review_draft=self.review_checkbox.isChecked())
        self.enqueue(context, title)
        self.result_status.setText('글쓰기 작업을 대기열에 추가했습니다.')
