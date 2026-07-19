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


def test_run_writes_tee_path_with_all_stdout_lines(tmp_path):
    script = tmp_path / "two_lines.py"
    script.write_text("print('a')\nprint('b')\n", encoding="utf-8")
    tee_path = tmp_path / "tee.txt"

    rc = subprocess_runner.run([sys.executable, str(script)], tee_path=tee_path)

    assert rc == 0
    content = tee_path.read_text(encoding="utf-8")
    assert content.splitlines() == ["a", "b"]


def test_run_tee_path_written_even_on_nonzero_exit(tmp_path):
    script = tmp_path / "fail_with_output.py"
    script.write_text("import sys\nprint('partial')\nsys.exit(1)\n", encoding="utf-8")
    tee_path = tmp_path / "tee.txt"

    rc = subprocess_runner.run([sys.executable, str(script)], tee_path=tee_path)

    assert rc == 1
    assert "partial" in tee_path.read_text(encoding="utf-8")


def test_run_tee_path_created_in_nonexistent_parent_dir(tmp_path):
    script = tmp_path / "one_line.py"
    script.write_text("print('x')\n", encoding="utf-8")
    tee_path = tmp_path / "nested" / "dir" / "tee.txt"

    rc = subprocess_runner.run([sys.executable, str(script)], tee_path=tee_path)

    assert rc == 0
    assert tee_path.read_text(encoding="utf-8").strip() == "x"
