from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Connection


@dataclass(frozen=True)
class BudgetSnapshot:
    charged: float
    open_reservations: float
    committed: float


def budget_snapshot(conn: Connection, currency: str = "USD") -> BudgetSnapshot:
    rows = conn.execute(
        """
        SELECT entry_type, COALESCE(SUM(amount), 0) AS amount
        FROM spend_ledger WHERE currency=? GROUP BY entry_type
        """,
        (currency,),
    ).fetchall()
    amounts = {row["entry_type"]: float(row["amount"]) for row in rows}
    charged = amounts.get("charge", 0.0) - amounts.get("refund", 0.0)
    open_reservations = amounts.get("reserve", 0.0) - amounts.get("release", 0.0)
    return BudgetSnapshot(
        charged=round(charged, 4),
        open_reservations=round(open_reservations, 4),
        committed=round(charged + open_reservations, 4),
    )


def can_reserve(conn: Connection, amount: float, cap: float, currency: str = "USD") -> bool:
    snapshot = budget_snapshot(conn, currency)
    return snapshot.committed + amount <= cap + 1e-9
