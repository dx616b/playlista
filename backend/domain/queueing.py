from rq import Queue
from redis import Redis

from backend.domain.settings import settings


def get_redis() -> Redis:
    return Redis.from_url(settings.redis_url)


def get_queue(name: str = "analysis") -> Queue:
    return Queue(name, connection=get_redis(), default_timeout=1800)
