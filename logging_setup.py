"""Logging setup intended to replace the legacy logger in a later step."""

import datetime
import os
from functools import wraps

from loguru import logger as loguru_logger


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logging")
os.makedirs(LOG_DIR, exist_ok=True)


def get_logger(module_name: str = "job_check_edits"):
    module_log_dir = os.path.join(LOG_DIR, module_name)
    os.makedirs(module_log_dir, exist_ok=True)
    log_file = os.path.join(module_log_dir, f"{module_name}.log")
    loguru_logger.add(
        log_file,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
        rotation="10 MB",
        compression="zip",
        level="DEBUG",
        enqueue=True,
    )
    return loguru_logger


def log_job(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        job_name = func.__name__
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d")
        job_file = __file__

        job_log_dir = os.path.join(LOG_DIR, job_name)
        os.makedirs(job_log_dir, exist_ok=True)
        log_filename = os.path.join(job_log_dir, f"{job_name}_{timestamp}.log")

        sink_id = loguru_logger.add(
            log_filename,
            format="{time:YYYY-MM-DD at HH:mm:ss} | {level} | {name}:{function}:{line} - {message}",
            level="INFO",
            filter=lambda record: record["extra"].get("job") == job_name,
        )
        with loguru_logger.contextualize(job=job_name):
            loguru_logger.info(f"Начало выполнения задачи '{job_name}' в файле {job_file} (время: {timestamp})")
            try:
                result = await func(*args, **kwargs)
                loguru_logger.info(f"Задача '{job_name}' завершена успешно")
                return result
            except Exception as e:
                loguru_logger.error(f"Ошибка в задаче '{job_name}': {e}")
                raise
            finally:
                loguru_logger.remove(sink_id)

    return wrapper
