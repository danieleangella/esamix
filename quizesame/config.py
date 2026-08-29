import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent

DATA_ROOT = Path(os.environ.get("QUIZESAME_DATA_DIR", PROJECT_ROOT / "corsi")).resolve()
LEGACY_ROOT = Path(os.environ.get("QUIZESAME_LEGACY_DIR", PROJECT_ROOT)).resolve()

DATA_ROOT.mkdir(parents=True, exist_ok=True)


def corso_dir(tag: str) -> Path:
    return DATA_ROOT / tag


def corso_db_path(tag: str) -> Path:
    return corso_dir(tag) / "db.sqlite"


def corso_exists(tag: str) -> bool:
    return corso_db_path(tag).exists()
