"""The single release-version source (project JSON has its own schema version)."""
from pathlib import Path

APP_NAME = "太阳能小车 · 凸轮设计工作台"
VERSION = "1.3.0"
RELEASE_DATE = "2026-10-20"


def changelog_text() -> str:
    path = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
    return path.read_text(encoding="utf-8") if path.is_file() else f"{APP_NAME} {VERSION}\n更新日志文件未找到。"
