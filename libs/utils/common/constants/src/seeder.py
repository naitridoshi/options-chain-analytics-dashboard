from pathlib import Path

INSTRUMENTS_FILE_PATH = (
    Path(__file__).parent.parent.parent.parent.parent.parent
    / "data"
    / "instruments.json"
)

SCRIPTS_FILE_PATH = (
    Path(__file__).parent.parent.parent.parent.parent.parent / "data" / "scripts.json"
)

INDICES_FILE_PATH = (
    Path(__file__).parent.parent.parent.parent.parent.parent / "data" / "indices.json"
)

INDEX_HEATMAP_CSV_PATH = (
    Path(__file__).parent.parent.parent.parent.parent.parent
    / "data"
    / "INDEX FILE_HEATMAP.csv"
)

SECTOR_HEATMAP_CSV_PATH = (
    Path(__file__).parent.parent.parent.parent.parent.parent
    / "data"
    / "SECTOR FILE_HEATMAP.csv"
)
