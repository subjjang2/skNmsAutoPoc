"""합성 골드 데이터 생성.

hard/medium 시나리오를 LLM paraphrase로 증강 → gold_pool.json.
라벨(action)은 시나리오에서 차용, LLM은 로그 문장만 생성.

실행: python -m pipeline.synth_gold [--per-scenario 30]
"""
import argparse
import asyncio
import json
import logging
import pathlib
import re
import yaml

DATA_DIR = pathlib.Path(__file__).parent.parent / "data" / "openstack"
SCENARIOS_YAML = pathlib.Path(__file__).parent.parent / "config" / "scenarios.yaml"
GOLD_POOL_JSON = DATA_DIR / "gold_pool.json"

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def _load_scenarios(difficulties=("hard", "medium")) -> list[dict]:
    data = yaml.safe_load(SCENARIOS_YAML.read_text(encoding="utf-8"))
    return [s for s in data.get("scenarios", []) if s.get("difficulty") in difficulties]


async def _generate_variants(db, scenario: dict, n: int) -> list[str]:
    from analyzer.llm_client import analyze
    system = "You are an OpenStack log generator. Output JSON array only, no explanation."
    prompt = (
        f"OpenStack failure scenario: {scenario.get('description', '')}\n"
        f"Required action: {scenario['action']}\n"
        f"Reference log (DO NOT reuse vocabulary): {scenario.get('log_text', '')}\n\n"
        f"Generate {n} realistic OpenStack log messages for this exact failure.\n"
        f"RULES:\n"
        f"- Different vocabulary, format, and component names from the reference\n"
        f"- Use realistic details: hostnames, UUIDs, IPs, error codes\n"
        f"- Each from a different perspective (nova-conductor, neutron-server, syslog, etc.)\n"
        f"- Do NOT include the action name ({scenario['action']}) in the log text\n"
        f'Output JSON array only: ["log1", "log2", ...]'
    )
    try:
        raw, _ = await analyze(db, system, prompt)
        m = re.search(r'\[.*?\]', raw, re.DOTALL)
        if not m:
            return []
        items = json.loads(m.group())
        return [str(x).strip() for x in items if str(x).strip()][:n]
    except Exception as e:
        logger.warning("LLM 생성 실패 (%s): %s", scenario["id"], e)
        return []


async def generate(n_per: int = 30) -> list[dict]:
    from dotenv import load_dotenv
    load_dotenv()
    from db.database import AsyncSessionLocal

    scenarios = _load_scenarios(difficulties=("hard", "medium"))
    logger.info("대상 시나리오: %d개 (hard/medium), 시나리오당 %d건", len(scenarios), n_per)

    rows: list[dict] = []
    async with AsyncSessionLocal() as db:
        for i, s in enumerate(scenarios, 1):
            variants = await _generate_variants(db, s, n_per)
            for j, log in enumerate(variants):
                rows.append({
                    "id": f"GLD-{s['id']}-{j+1:03d}",
                    "raw_log": log,
                    "action": s["action"],
                    "difficulty": s["difficulty"],
                    "scenario_id": s["id"],
                    "source": "gold",
                })
            logger.info("[%d/%d] %s → %d건 생성", i, len(scenarios), s["id"], len(variants))

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    GOLD_POOL_JSON.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("gold_pool.json 저장: %d건 총계", len(rows))
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-scenario", type=int, default=30,
                        help="시나리오당 생성 건수 (기본 30 → 약 600건)")
    args = parser.parse_args()
    asyncio.run(generate(n_per=args.per_scenario))
