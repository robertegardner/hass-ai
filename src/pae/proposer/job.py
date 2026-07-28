"""RQ job entry points — imported by the worker via dotted path."""
from pae.proposer.service import run_proposing


def propose_job() -> dict:
    result = run_proposing()
    return result.__dict__
