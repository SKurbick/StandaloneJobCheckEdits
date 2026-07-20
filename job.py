"""Job orchestration for the standalone check-edits runner.

The heavy implementation still lives in ``job_check_edits_standalone.py``.
This module is the first refactoring seam: keep the original file untouched
while new execution code can move here module by module.
"""

import asyncio
from functools import lru_cache

if __package__:
    from .config import settings
    from .services.card_actualization import CardActualizationService
    from .token_provider import TokenProvider
    from .wb_api import WBApiFactory, create_client_session
else:
    from config import settings
    from services.card_actualization import CardActualizationService
    from token_provider import TokenProvider
    from wb_api import WBApiFactory, create_client_session


@lru_cache(maxsize=1)
def _legacy():
    if __package__:
        from . import legacy
    else:
        import legacy
    return legacy


async def _job_impl():
    legacy = _legacy()
    token_provider = TokenProvider(settings.TOKENS_FILE_NAME)
    legacy.configure_token_provider(token_provider)
    legacy.configure_domain_helpers()
    legacy.configure_infrastructure()
    logger = legacy.logger
    logger.info(
        "Запуск :"
        "Актуализация информации по ценам, скидкам, габаритам, комиссии, логистики от склада WB до ПВЗ"
    )
    gs_service = legacy.gs_service_for_schedule_connection()
    async with create_client_session() as wb_session:
        wb_api = WBApiFactory(session=wb_session, logger=logger)
        legacy.configure_wb_api_factory(wb_api)
        service = CardActualizationService(token_provider=token_provider, wb_api=wb_api, logger=logger)
        async with legacy.Database1() as db:
            await gs_service.add_actually_data_to_table(db=db)
            logger.info(
                "Завершение :"
                "Актуализация информации по ценам, скидкам, габаритам, комиссии, логистики от склада WB до ПВЗ"
            )
            logger.info("Запуск : Смотрит в таблицу, оценивает изменения")
            result = await legacy.check_edits_columns(db=db)
            if result:
                logger.info("Завершение : Внесение изменений в таблицу")
                await service.actualize_card_data_in_db(result, db=db)


_job_impl.__name__ = "job_check_edits_columns_and_add_actually_data_to_table"


def _build_job():
    return _legacy().log_job(_job_impl)


async def job_check_edits_columns_and_add_actually_data_to_table():
    await _build_job()()


def run():
    asyncio.run(job_check_edits_columns_and_add_actually_data_to_table())
