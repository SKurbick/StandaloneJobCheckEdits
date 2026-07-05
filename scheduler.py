"""Scheduler entrypoint for the standalone check-edits job."""

import asyncio
import contextlib

from apscheduler.events import EVENT_JOB_ERROR
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

if __package__:
    from .job import job_check_edits_columns_and_add_actually_data_to_table
    from .legacy import logger
else:
    from job import job_check_edits_columns_and_add_actually_data_to_table
    from legacy import logger


scheduler = AsyncIOScheduler(job_defaults={"misfire_grace_time": 2000, "max_instances": 1})


@scheduler.scheduled_job(IntervalTrigger(minutes=15), coalesce=True)
async def scheduled_check_edits_columns_and_add_actually_data_to_table():
    await job_check_edits_columns_and_add_actually_data_to_table()


def job_error_listener(event):
    job = scheduler.get_job(event.job_id)
    job_name = job.name if job and getattr(job, "name", None) else event.job_id
    logger.error(f"Scheduler job {job_name!r} failed: {event.exception}")
    if event.traceback:
        logger.error(event.traceback)


async def main():
    logger.info("Запуск standalone scheduler")
    scheduler.add_listener(job_error_listener, EVENT_JOB_ERROR)
    scheduler.start()
    with contextlib.suppress(KeyboardInterrupt, SystemExit):
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
