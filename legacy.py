"""Compatibility exports from the preserved standalone implementation."""

if __package__:
    from . import domain
    from . import job_check_edits_standalone as _impl
    from .db import Database1 as RefactoredDatabase1
    from .google_sheet import GoogleSheet as RefactoredGoogleSheet
    from .google_sheet import safe_batch_update as refactored_safe_batch_update
    from .repositories import (
        ArticleTable as RefactoredArticleTable,
        CardData as RefactoredCardData,
        CostPriceDBContainer as RefactoredCostPriceDBContainer,
        CostPriceTable as RefactoredCostPriceTable,
        UnitEconomicsTable as RefactoredUnitEconomicsTable,
    )
else:
    import domain
    import job_check_edits_standalone as _impl
    from db import Database1 as RefactoredDatabase1
    from google_sheet import GoogleSheet as RefactoredGoogleSheet
    from google_sheet import safe_batch_update as refactored_safe_batch_update
    from repositories import (
        ArticleTable as RefactoredArticleTable,
        CardData as RefactoredCardData,
        CostPriceDBContainer as RefactoredCostPriceDBContainer,
        CostPriceTable as RefactoredCostPriceTable,
        UnitEconomicsTable as RefactoredUnitEconomicsTable,
    )

Database1 = _impl.Database1
Service = _impl.Service
check_edits_columns = _impl.check_edits_columns
gs_service_for_schedule_connection = _impl.gs_service_for_schedule_connection
log_job = _impl.log_job
logger = _impl.logger
settings = _impl.settings

ListOfCardsContent = _impl.ListOfCardsContent
ListOfGoodsPricesAndDiscounts = _impl.ListOfGoodsPricesAndDiscounts
CommissionTariffs = _impl.CommissionTariffs


def configure_token_provider(token_provider):
    _impl.get_wb_tokens = token_provider.get_all


def configure_domain_helpers():
    _impl.column_index_to_letter = domain.column_index_to_letter
    _impl.process_string = domain.process_string
    _impl.process_local_vendor_code = domain.process_local_vendor_code
    _impl.merge_dicts = domain.merge_dicts
    _impl.calculate_sum_for_logistic = domain.calculate_sum_for_logistic
    _impl.validate_data = domain.validate_data

    def create_lk_articles_with_legacy_logger(edit_nm_ids_data):
        return domain.create_lk_articles(edit_nm_ids_data, logger=_impl.logger)

    _impl.create_lk_articles = create_lk_articles_with_legacy_logger


def configure_infrastructure():
    _impl.Database1 = RefactoredDatabase1
    _impl.ArticleTable = RefactoredArticleTable
    _impl.CardData = RefactoredCardData
    _impl.CostPriceTable = RefactoredCostPriceTable
    _impl.CostPriceDBContainer = RefactoredCostPriceDBContainer
    _impl.UnitEconomicsTable = RefactoredUnitEconomicsTable
    _impl.GoogleSheet = RefactoredGoogleSheet
    _impl.safe_batch_update = refactored_safe_batch_update

    globals()["Database1"] = RefactoredDatabase1



def configure_wb_api_factory(wb_api_factory):
    _impl.WB_API_FACTORY = wb_api_factory

