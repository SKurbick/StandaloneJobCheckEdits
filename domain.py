"""Pure domain helpers extracted from the legacy standalone job."""

import re
from collections import ChainMap
from typing import Any

try:
    from . import constants as columns
except ImportError:
    import constants as columns


def column_index_to_letter(index: int) -> str:
    letter = ""
    while index > 0:
        index -= 1
        letter = chr((index % 26) + 65) + letter
        index //= 26
    return letter


def process_string(value: str) -> str:
    wild_match = re.match(r"^wild(\d+).*$", value)
    if wild_match:
        return f"wild{wild_match.group(1)}"
    if re.match(r"^[a-zA-Z\s]+$", value):
        return value
    return value


def process_local_vendor_code(value: str) -> str:
    return process_string(value)


def merge_dicts(d1: dict, d2: dict) -> dict:
    result = {}
    for key in d1.keys() | d2.keys():
        result[key] = dict(ChainMap({}, d1.get(key, {}), d2.get(key, {})))
    return result


def calculate_sum_for_logistic(for_one_liter: float, next_liters: float, length, width: int, height: int):
    volume_good = (length * width * height) / 1000
    if volume_good > 1.0:
        return (volume_good - 1) * next_liters + for_one_liter
    return for_one_liter


async def validate_data(nm_ids_db_data, data: dict):
    result_valid_data = {}
    for nm_id, edit_data in data.items():
        if nm_id.isdigit():
            nm_ids_data = {}
            price = edit_data.get(columns.PRICE_DISCOUNT, {}).get(columns.SET_NEW_PRICE, "")
            discount = edit_data.get(columns.PRICE_DISCOUNT, {}).get(columns.SET_NEW_DISCOUNT, "")
            height = edit_data.get(columns.DIMENSIONS, {}).get(columns.NEW_HEIGHT, "")
            length = edit_data.get(columns.DIMENSIONS, {}).get(columns.NEW_LENGTH, "")
            width = edit_data.get(columns.DIMENSIONS, {}).get(columns.NEW_WIDTH, "")

            if columns.PRICE_DISCOUNT in edit_data:
                if str(discount).isdigit():
                    nm_ids_data.setdefault(columns.PRICE_DISCOUNT, {})["discount"] = int(discount)
                if str(price).isdigit():
                    nm_ids_data.setdefault(columns.PRICE_DISCOUNT, {})["price"] = int(price)

            if columns.DIMENSIONS in edit_data and all(str(v).isdigit() for v in (height, length, width)):
                nm_ids_data.setdefault(columns.DIMENSIONS, {})["height"] = int(height)
                nm_ids_data.setdefault(columns.DIMENSIONS, {})["length"] = int(length)
                nm_ids_data.setdefault(columns.DIMENSIONS, {})["width"] = int(width)

            if nm_ids_data:
                nm_ids_data["vendorCode"] = edit_data[columns.WILD]
                if columns.PRICE_DISCOUNT in nm_ids_data:
                    nm_ids_data["net_profit"] = int(
                        str(edit_data[columns.NET_PROFIT]).replace(" ", "").replace("₽", "")
                    )
                if columns.DIMENSIONS in nm_ids_data:
                    nm_ids_data["sizes"] = nm_ids_db_data[nm_id]["sizes"]
                result_valid_data[int(nm_id)] = nm_ids_data
    return result_valid_data


def create_lk_articles(edit_nm_ids_data: dict[str, Any], logger=None) -> dict[Any, set[str]]:
    result = {}
    for k, v in edit_nm_ids_data.items():
        account = v.get("account") if isinstance(v, dict) else None
        if not account:
            if logger is not None:
                logger.warning(
                    "Пропускаем артикул без account при группировке обновлений: {} -> {}",
                    k,
                    v,
                )
            continue
        if account not in result:
            result[account] = {k}
        else:
            result[account].add(k)
    return result
