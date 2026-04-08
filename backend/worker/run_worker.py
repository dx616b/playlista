from redis import Redis
from rq import Worker

from backend.domain.settings import settings


def main() -> None:
    redis_conn = Redis.from_url(settings.redis_url)
    worker = Worker(["analysis"], connection=redis_conn)
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
