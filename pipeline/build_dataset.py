"""scenarios.yaml regex로 raw_lines 라벨링 → train_pool.json + test_set.json 생성.

실행: python -m pipeline.build_dataset [--test-size 1000]
출력:
  data/openstack/train_pool.json   — 라벨 있는 행, 시험셋 제외
  data/openstack/test_set.json     — 고정 시험셋 (is_test=true)
  data/openstack/coverage.json     — 커버리지 요약
"""
import argparse
import json
import pathlib
import random
import re
import sys
import yaml

DATA_DIR = pathlib.Path(__file__).parent.parent / "data" / "openstack"
SCENARIOS_YAML = pathlib.Path(__file__).parent.parent / "config" / "scenarios.yaml"

TRAIN_POOL_JSON = DATA_DIR / "train_pool.json"
TEST_SET_JSON = DATA_DIR / "test_set.json"
COVERAGE_JSON = DATA_DIR / "coverage.json"

DIFFICULTY_DIST = {"easy": 0.35, "medium": 0.35, "hard": 0.30}


def _load_patterns() -> list[dict]:
    data = yaml.safe_load(SCENARIOS_YAML.read_text(encoding="utf-8"))
    return data.get("scenarios", [])


def _label_lines(lines: list[str], patterns: list[dict]) -> list[dict]:
    compiled = [(s, re.compile(s["pattern_regex"], re.IGNORECASE)) for s in patterns]
    labeled: list[dict] = []
    for line in lines:
        for s, rx in compiled:
            if rx.search(line):
                labeled.append({
                    "raw_log": line,
                    "action": s["action"],
                    "difficulty": s["difficulty"],
                    "scenario_id": s["id"],
                })
                break  # 첫 매칭만
    return labeled


def _synthetic_rows_from_scenarios(patterns: list[dict], n_per: int = 30) -> list[dict]:
    """scenarios.yaml log_text augmentation → 합성 라벨 데이터. hold_out 시나리오 제외."""
    rows = []
    for s in patterns:
        if s.get("hold_out", False):
            continue  # hold_out 시나리오는 train_pool에 포함하지 않음
        base = s.get("log_text", "")
        if not base:
            continue
        for _ in range(n_per):
            line = re.sub(r'compute-\d+', f'compute-{random.randint(1, 20):02d}', base)
            line = re.sub(r'\b(\d+)s\b', lambda m: f'{random.randint(10, 120)}s', line)
            line = re.sub(r'\b9\d%', f'{random.randint(90, 99)}%', line)
            rows.append({
                "raw_log": line,
                "action": s["action"],
                "difficulty": s["difficulty"],
                "scenario_id": s["id"],
            })
    return rows


def _split(labeled: list[dict], test_size: int, seed: int = 42) -> tuple[list[dict], list[dict]]:
    random.seed(seed)
    shuffled = labeled.copy()
    random.shuffle(shuffled)
    test = shuffled[:test_size]
    train = shuffled[test_size:]
    return train, test


def _add_ids(rows: list[dict], prefix: str) -> None:
    for i, r in enumerate(rows, 1):
        r["id"] = f"{prefix}-{i:05d}"


def _coverage_report(total: int, labeled: int, train: list[dict], test: list[dict]) -> dict:
    from collections import Counter
    return {
        "total_lines": total,
        "labeled": labeled,
        "coverage_pct": round(labeled / total * 100, 2) if total else 0,
        "train_count": len(train),
        "test_count": len(test),
        "train_action_dist": dict(Counter(r["action"] for r in train)),
        "test_action_dist": dict(Counter(r["action"] for r in test)),
        "test_difficulty_dist": dict(Counter(r["difficulty"] for r in test)),
    }


def build(test_size: int = 1000) -> dict:
    from pipeline.log_loader import load_raw_lines
    lines = load_raw_lines()
    patterns = _load_patterns()

    print(f"Labeling {len(lines)} lines with {len(patterns)} patterns …")
    labeled = _label_lines(lines, patterns)
    print(f"Matched {len(labeled)} / {len(lines)} lines "
          f"({len(labeled)/len(lines)*100:.1f}% coverage)")

    synthetic = _synthetic_rows_from_scenarios(patterns, n_per=30)
    print(f"Synthetic rows from log_text augmentation: {len(synthetic)}")
    labeled = labeled + synthetic

    if len(labeled) < test_size + 100:
        print(f"WARNING: only {len(labeled)} labeled rows, test_size clamped", file=sys.stderr)
        test_size = max(10, len(labeled) // 5)

    train, test = _split(labeled, test_size)
    _add_ids(train, "TRN")
    _add_ids(test, "TST")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TRAIN_POOL_JSON.write_text(
        json.dumps(train, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    TEST_SET_JSON.write_text(
        json.dumps(test, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    cov = _coverage_report(len(lines), len(labeled), train, test)
    COVERAGE_JSON.write_text(
        json.dumps(cov, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"train_pool: {len(train)}  test_set: {len(test)}")
    print("Coverage summary:", cov)
    return cov


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-size", type=int, default=1000)
    args = parser.parse_args()
    build(test_size=args.test_size)
