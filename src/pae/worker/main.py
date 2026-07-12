from prometheus_client import start_http_server
from redis import Redis
from rq import Queue, Worker

import pae.metrics  # noqa: F401 — registers pae_* metrics in the default registry
from pae.config import get_settings
from pae.logging import get_logger
from pae.worker.scheduler import start_daily_scheduler

log = get_logger(__name__)

QUEUE_NAME = "pae:default"


def run_worker() -> None:
    settings = get_settings()
    start_http_server(settings.worker_metrics_port)
    connection = Redis.from_url(settings.redis_url)
    queue = Queue(QUEUE_NAME, connection=connection)
    start_daily_scheduler(queue, settings.miner_run_hour_utc)
    log.info(
        "worker_starting",
        queue=QUEUE_NAME,
        metrics_port=settings.worker_metrics_port,
        mine_hour_utc=settings.miner_run_hour_utc,
    )
    Worker([queue], connection=connection).work()
