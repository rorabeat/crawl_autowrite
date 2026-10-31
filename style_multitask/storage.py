"""Style task definitions are independent of standard tasks.json."""

from dataclasses import asdict
import json
from pathlib import Path

from .models import StyleTaskItem


STYLE_TASKS_PATH = Path(__file__).resolve().parent / "style_tasks.json"


def load_style_tasks() -> list[StyleTaskItem]:
    if not STYLE_TASKS_PATH.exists():
        return []
    try:
        raw = json.loads(STYLE_TASKS_PATH.read_text(encoding="utf-8"))
        return [StyleTaskItem(**item) for item in raw]
    except (OSError, ValueError, TypeError):
        return []


def save_style_tasks(tasks: list[StyleTaskItem]) -> None:
    STYLE_TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = STYLE_TASKS_PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps([asdict(task) for task in tasks], ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STYLE_TASKS_PATH)
