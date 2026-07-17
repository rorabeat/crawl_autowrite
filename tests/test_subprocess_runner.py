"""Task 005 완료 조건 검증: subprocess_runner.run()을 실제 subprocess.Popen 경로로 검증."""

import sys

import subprocess_runner


def test_run_returns_exit_code_of_normal_process(tmp_path):
    script = tmp_path / "normal.py"
    script.write_text("import sys\nprint('hello')\nsys.exit(0)\n", encoding="utf-8")

    outputs = []
    rc = subprocess_runner.run([sys.executable, str(script)], on_output=outputs.append)

    assert rc == 0
    assert "hello" in outputs


def test_run_streams_korean_output_without_corruption(tmp_path):
    script = tmp_path / "korean.py"
    script.write_text(
        "import sys\nprint('안녕하세요 크롤링 시작')\nsys.exit(0)\n",
        encoding="utf-8",
    )

    outputs = []
    rc = subprocess_runner.run([sys.executable, str(script)], on_output=outputs.append)

    assert rc == 0
    assert "안녕하세요 크롤링 시작" in outputs


def test_run_kills_process_and_returns_minus_one_on_timeout(tmp_path):
    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(10)\n", encoding="utf-8")

    rc = subprocess_runner.run([sys.executable, str(script)], timeout=0.5)

    assert rc == -1


def test_run_nonzero_exit_code_is_propagated(tmp_path):
    script = tmp_path / "fail.py"
    script.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")

    rc = subprocess_runner.run([sys.executable, str(script)])

    assert rc == 3
