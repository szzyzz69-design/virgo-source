import time

from app.database import Database
from app.schemas.agent_contact import AgentContactItem, AgentMenuItem, AgentSimCardItem


class ContactForbidden(Exception):
    pass


class ContactNotFound(Exception):
    pass


class AgentContactService:
    def __init__(self, database: Database):
        self._database = database

    def list_contacts(self, agent_area: str) -> list[AgentContactItem]:
        with self._database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT id, display_name, phone_number, normalized_phone_number,
                       remark, status, source, last_contact_at, areas, updated_at
                FROM contacts
                WHERE NULLIF(BTRIM(areas), '') = NULLIF(BTRIM(%s), '')
                ORDER BY last_contact_at DESC NULLS LAST, updated_at DESC, id
                """,
                (agent_area,),
            ).fetchall()
        return [
            AgentContactItem(
                id=row[0],
                displayName=row[1],
                phoneNumber=row[2],
                normalizedPhoneNumber=row[3],
                remark=row[4],
                status=row[5],
                source=row[6],
                lastContactAt=row[7],
                areas=row[8],
                updatedAt=row[9],
            )
            for row in rows
        ]

    def update_remark(self, contact_id: str, agent_area: str, remark: str) -> None:
        self._ensure_access(contact_id, agent_area)
        normalized_remark = remark.strip() or None
        now = time.time_ns() // 1_000_000
        with self._database.transaction() as connection:
            connection.execute(
                """
                UPDATE contacts
                SET remark = %s,
                    updated_at = %s
                WHERE id = %s
                  AND NULLIF(BTRIM(areas), '') = NULLIF(BTRIM(%s), '')
                """,
                (normalized_remark, now, contact_id, agent_area),
            )

    def list_menus(self, agent_area: str) -> list[AgentMenuItem]:
        with self._database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT id, menu, update_time, update_by, areas
                FROM products
                WHERE NULLIF(BTRIM(areas), '') = NULLIF(BTRIM(%s), '')
                  AND NULLIF(BTRIM(menu), '') IS NOT NULL
                ORDER BY update_time DESC, id
                """,
                (agent_area,),
            ).fetchall()
        return [
            AgentMenuItem(
                id=row[0],
                menu=row[1],
                updateTime=row[2],
                updateBy=row[3],
                areas=row[4],
            )
            for row in rows
        ]

    def list_sim_cards(self, account_id: str) -> list[AgentSimCardItem]:
        with self._database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT s.id, s.phone_number, s.carrier_name, s.areas,
                       s.esim_profile_name
                FROM account_sim_cards acs
                JOIN sim_cards s ON s.id = acs.sim_card_id
                WHERE acs.account_id = %s
                  AND s.enabled = TRUE
                  AND s.status = 'active'
                  AND s.unregistered_at IS NULL
                ORDER BY s.phone_number ASC NULLS LAST,
                         s.device_id ASC,
                         s.sim_number ASC,
                         s.id ASC
                """,
                (account_id,),
            ).fetchall()
        return [
            AgentSimCardItem(
                id=row[0],
                phoneNumber=row[1],
                carrierName=row[2],
                areas=row[3],
                customerRemark=row[4],
            )
            for row in rows
        ]

    def _ensure_access(self, contact_id: str, agent_area: str) -> None:
        with self._database.transaction() as connection:
            allowed = connection.execute(
                """
                SELECT id
                FROM contacts
                WHERE id = %s
                  AND NULLIF(BTRIM(areas), '') = NULLIF(BTRIM(%s), '')
                """,
                (contact_id, agent_area),
            ).fetchone()
            if allowed is not None:
                return
            exists = connection.execute(
                "SELECT id FROM contacts WHERE id = %s",
                (contact_id,),
            ).fetchone()
        if exists is not None:
            raise ContactForbidden
        raise ContactNotFound
