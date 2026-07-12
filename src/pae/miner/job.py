"""RQ job entry points — imported by the worker via dotted path."""
from pae.miner.service import run_mining


def mine_patterns_job() -> dict:
    result = run_mining()
    return result.__dict__
