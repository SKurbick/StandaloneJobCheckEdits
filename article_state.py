from enum import Enum


class ArticleState(str, Enum):
    ACTIVE = "ACTIVE"
    IN_TRASH = "IN_TRASH"
    NOT_FOUND = "NOT_FOUND"
    CHECK_FAILED = "CHECK_FAILED"


ARTICLE_STATE_MESSAGES = {
    ArticleState.IN_TRASH: "НЕ ВЫПОЛНЕНО: артикул находится в корзине WB",
    ArticleState.NOT_FOUND: "НЕ ВЫПОЛНЕНО: артикул не найден в кабинете WB",
    ArticleState.CHECK_FAILED: "ВРЕМЕННАЯ ОШИБКА: не удалось проверить артикул в WB",
}
