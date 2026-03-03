from pathlib import Path

BASE_DIR = str(Path(__file__).resolve().parent.parent.parent.parent.parent.parent)


def get_relative_path(path: str) -> str:
    return str(Path(path).relative_to(BASE_DIR))
