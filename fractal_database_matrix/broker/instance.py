import os

from taskiq import TaskiqScheduler
from taskiq.middlewares.retry_middleware import SimpleRetryMiddleware
from taskiq_matrix.matrix_result_backend import MatrixResultBackend
from taskiq_matrix.schedulesource import MatrixRoomScheduleSource

from .broker import FractalMatrixBroker

broker = (
    FractalMatrixBroker()
    .with_matrix_config(
        os.environ.get("MATRIX_HOMESERVER_URL"),
        os.environ.get("MATRIX_ACCESS_TOKEN"),
    )
    .with_result_backend(
        MatrixResultBackend(
            homeserver_url=os.environ.get("MATRIX_HOMESERVER_URL"),
            access_token=os.environ.get("MATRIX_ACCESS_TOKEN"),
            result_ex_time=60,
        )
    )
    .with_middlewares(SimpleRetryMiddleware(default_retry_count=3))
)

scheduler = TaskiqScheduler(broker=broker, sources=[MatrixRoomScheduleSource(broker)])
