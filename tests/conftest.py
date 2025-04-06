import logging
from typing import Optional

from task_worker import AsyncTaskWorker


# Enable live logging
def _pytest_configure(config):
    config.option.log_cli = True
    config.option.log_cli_level = "INFO"


logger = logging.getLogger(__name__)

# TaskWorker
_task_worker: Optional[AsyncTaskWorker] = None


# Initialize or Get Test Task Worker
async def get_test_task_worker() -> AsyncTaskWorker:
    global _task_worker
    if _task_worker is None:
        _task_worker = AsyncTaskWorker(max_workers=3)
    return _task_worker
