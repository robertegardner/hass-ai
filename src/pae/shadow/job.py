"""RQ job entry points — imported by the worker via dotted path."""
from pae.shadow.service import run_shadow_eval


def shadow_eval_job() -> dict:
    return run_shadow_eval()
