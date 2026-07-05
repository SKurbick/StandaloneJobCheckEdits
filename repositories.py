"""Database repositories used by the standalone check-edits job."""

from typing import Set


class ArticleTable:
    def __init__(self, db):
        self.db = db

    async def check_nm_ids(self, account: str, nm_ids: list):
        nm_ids_str = ", ".join(f"({nm_id})" for nm_id in nm_ids)
        query = f"""
        SELECT nm_id
        FROM (VALUES {nm_ids_str}) AS input(nm_id)
        EXCEPT
        SELECT nm_id
        FROM article;
        """
        not_found_nm_ids = await self.db.fetch(query)
        return [result_nm_id["nm_id"] for result_nm_id in not_found_nm_ids]

    async def update_articles(self, data, filter_nm_ids):
        article_data = [
            (nm_id, data[nm_id]["account"], data[nm_id]["vendor_code"], data[nm_id]["wild"])
            for nm_id in filter_nm_ids
        ]
        query = """
        INSERT INTO article (nm_id, account, vendor_code, local_vendor_code)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (account, vendor_code) DO NOTHING;
        """
        await self.db.executemany(query, article_data)

    async def get_all_nm_ids(self):
        query = "SELECT * FROM article;"
        nm_ids = await self.db.fetch(query)
        return {str(data["nm_id"]): dict(data) for data in nm_ids}

    async def update_article_data(self, data):
        query = """
        INSERT INTO article (nm_id, account, local_vendor_code, vendor_code)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (nm_id) DO UPDATE
        SET account = EXCLUDED.account,
            local_vendor_code = EXCLUDED.local_vendor_code,
            vendor_code = EXCLUDED.vendor_code
        WHERE NOT EXISTS (
            SELECT 1 FROM article
            WHERE account = $2 AND vendor_code = $4
        );
        """
        await self.db.executemany(query, data)


class CardData:
    def __init__(self, db):
        self.db = db

    async def get_chrt_ids(self):
        return await self.db.fetch("SELECT article_id, chrt_id FROM card_data;")

    async def get_actual_information_to_db(self, article_ids: Set[int]):
        query = """
        SELECT cd.*, a.local_vendor_code
        FROM card_data cd
        JOIN article a ON cd.article_id = a.nm_id
        WHERE cd.article_id = ANY($1);
        """
        return await self.db.fetch(query, article_ids)

    async def update_card_data(self, data):
        query = """
        INSERT INTO card_data (article_id, barcode,commission_wb, discount, height, length,
                                logistic_from_wb_wh_to_opp, photo_link, price, subject_name, width, last_update_time)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        ON CONFLICT (article_id) DO UPDATE
        SET barcode = EXCLUDED.barcode,
            commission_wb = EXCLUDED.commission_wb,
            discount = EXCLUDED.discount,
            height = EXCLUDED.height,
            length = EXCLUDED.length,
            logistic_from_wb_wh_to_opp = EXCLUDED.logistic_from_wb_wh_to_opp,
            photo_link = EXCLUDED.photo_link,
            price = EXCLUDED.price,
            subject_name = EXCLUDED.subject_name,
            width = EXCLUDED.width,
            last_update_time = EXCLUDED.last_update_time;
        """
        await self.db.executemany(query, data)


class CostPriceTable:
    def __init__(self, db):
        self.db = db

    async def get_current_data(self):
        query = """
        SELECT DISTINCT ON (local_vendor_code) *
        FROM cost_price
        ORDER BY local_vendor_code, created_at DESC;
        """
        return await self.db.fetch(query=query)


class CostPriceDBContainer:
    def __init__(self, records: list[dict]):
        self.local_vendor_code = {record["local_vendor_code"]: dict(record) for record in records}


class UnitEconomicsTable:
    def __init__(self, db):
        self.db = db

    async def update_data(self, data):
        query = """
        INSERT INTO unit_economics (article_id, commission_wb, discount,
                                logistic_from_wb_wh_to_opp, price, cost_price, percent_by_tax, last_update_time)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (article_id) DO UPDATE
        SET commission_wb = EXCLUDED.commission_wb,
            discount = EXCLUDED.discount,
            logistic_from_wb_wh_to_opp = EXCLUDED.logistic_from_wb_wh_to_opp,
            price = EXCLUDED.price,
            percent_by_tax = EXCLUDED.percent_by_tax,
            last_update_time = EXCLUDED.last_update_time,
            cost_price = EXCLUDED.cost_price;
        """
        await self.db.executemany(query, data)
