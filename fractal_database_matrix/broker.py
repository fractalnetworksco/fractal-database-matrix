from taskiq import TaskiqScheduler
from taskiq.middlewares.retry_middleware import SimpleRetryMiddleware
from taskiq_matrix.matrix_broker import MatrixBroker
from taskiq_matrix.matrix_result_backend import MatrixResultBackend
from taskiq_matrix.schedulesource import MatrixRoomScheduleSource

broker = (
    MatrixBroker()
    .with_result_backend(MatrixResultBackend(result_ex_time=60))
    .with_middlewares(SimpleRetryMiddleware(default_retry_count=3))
)

scheduler = TaskiqScheduler(broker=broker, sources=[MatrixRoomScheduleSource(broker)])
