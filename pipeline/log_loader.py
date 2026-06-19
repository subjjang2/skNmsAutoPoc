"""Zenodo loghub v2.0 OpenStack 로그 다운로드 + 파싱.

실행: python -m pipeline.log_loader
출력: data/openstack/OpenStack_2k.log_structured.csv  (또는 full)
      data/openstack/raw_lines.json
"""
import csv
import json
import pathlib
import sys
import urllib.request
import zipfile

DATA_DIR = pathlib.Path(__file__).parent.parent / "data" / "openstack"

# Zenodo loghub v2.0 OpenStack full dataset
ZENODO_URL = "https://zenodo.org/record/8196385/files/OpenStack.tar.gz"
CSV_FILENAME = "OpenStack_2k.log_structured.csv"   # 2k sample; full 버전으로 교체 가능
RAW_LINES_JSON = DATA_DIR / "raw_lines.json"


def _find_csv() -> pathlib.Path | None:
    for p in DATA_DIR.glob("*.csv"):
        return p
    return None


def _download_sample() -> pathlib.Path:
    """2k sample CSV 직접 다운로드 (loghub GitHub)."""
    sample_url = (
        "https://raw.githubusercontent.com/logpai/loghub/master/OpenStack/"
        "OpenStack_2k.log_structured.csv"
    )
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    dest = DATA_DIR / CSV_FILENAME
    print(f"Downloading {sample_url} …")
    urllib.request.urlretrieve(sample_url, dest)
    print(f"Saved: {dest}")
    return dest


def load_raw_lines(max_lines: int | None = None) -> list[str]:
    """CSV Content 컬럼 → 원본 로그 라인 목록 반환. 캐시 있으면 재사용."""
    if RAW_LINES_JSON.exists():
        lines = json.loads(RAW_LINES_JSON.read_text(encoding="utf-8"))
        print(f"Cache hit: {len(lines)} lines from {RAW_LINES_JSON}")
        return lines[:max_lines] if max_lines else lines

    csv_path = _find_csv()
    if csv_path is None:
        csv_path = _download_sample()

    lines: list[str] = []
    with csv_path.open(encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            content = row.get("Content") or row.get("content") or ""
            if content.strip():
                lines.append(content.strip())

    # 캐시 저장
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_LINES_JSON.write_text(json.dumps(lines, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Parsed {len(lines)} lines → {RAW_LINES_JSON}")
    return lines[:max_lines] if max_lines else lines


if __name__ == "__main__":
    max_n = int(sys.argv[1]) if len(sys.argv) > 1 else None
    lines = load_raw_lines(max_n)
    print(f"Total lines available: {len(lines)}")
    for l in lines[:3]:
        print(" ", l)
