"""Task 001 완료 조건 스모크 테스트: 5개 골격 모듈이 예외 없이 import되는지 확인한다."""

import importlib


def test_all_skeleton_modules_import_without_error():
    for module_name in [
        "app",
        "pipeline",
        "agents_editor",
        "image_input",
        "subprocess_runner",
    ]:
        importlib.import_module(module_name)


def test_main_window_has_five_tabs(qtbot):
    from app import TAB_TITLES, MainWindow

    window = MainWindow()
    qtbot.addWidget(window)

    assert window.tabs.count() == len(TAB_TITLES) == 5
