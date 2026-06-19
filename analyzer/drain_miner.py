"""Drain3 싱글톤 래퍼 — 파일 의존성 없는 단건 호출."""
import jsonpickle
from drain3 import TemplateMiner

_miner: TemplateMiner | None = None


def _get_miner() -> TemplateMiner:
    global _miner
    if _miner is None:
        _miner = TemplateMiner()
    return _miner


def add_log(line: str) -> dict:
    result = _get_miner().add_log_message(line.strip())
    if result:
        return {
            "template": result.get("template_mined", line),
            "change_type": result.get("change_type", ""),
            "cluster_id": result.get("cluster_id", 0),
        }
    return {"template": line, "change_type": "", "cluster_id": 0}


def get_state_json() -> str:
    return jsonpickle.encode(_get_miner().drain, keys=True)


def restore_state_json(json_str: str) -> None:
    _get_miner().drain = jsonpickle.decode(json_str, keys=True)
