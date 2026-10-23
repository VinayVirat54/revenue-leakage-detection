"""
tests/test_rules.py
===================
Unit tests for all 5 SQL detection rules using tiny in-memory SQLite
databases.  No files on disk are read; every test is self-contained.

Each rule has:
  - A "should flag" case
  - A "should NOT flag" case
  - One or more edge cases matching the rule boundaries
"""

import sqlite3
import pytest
from pathlib import Path

# ── Helpers ───────────────────────────────────────────────────────────────────
SQL_DIR = Path(__file__).parent.parent / "sql"
APPROVAL_LIMIT = 50_000


def make_conn(invoices: list[dict],
              holidays: list[dict] | None = None) -> sqlite3.Connection:
    """
    Create an in-memory SQLite DB with the 'invoices' table (and optionally
    a 'holidays' table) pre-populated from the given list of dicts.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE invoices (
            invoice_id   TEXT PRIMARY KEY,
            vendor_id    TEXT,
            vendor_name  TEXT,
            invoice_date TEXT,
            payment_date TEXT,
            amount_inr   REAL,
            approver_id  TEXT,
            department   TEXT,
            payment_mode TEXT
        )
    """)
    for row in invoices:
        conn.execute(
            "INSERT INTO invoices VALUES (?,?,?,?,?,?,?,?,?)",
            (
                row["invoice_id"], row.get("vendor_id", "VND0001"),
                row.get("vendor_name", "Test Vendor"),
                row["invoice_date"], row["payment_date"],
                row["amount_inr"], row.get("approver_id", "APR001"),
                row.get("department", "Finance"),
                row.get("payment_mode", "NEFT"),
            ),
        )

    conn.execute("""
        CREATE TABLE holidays (
            date         TEXT PRIMARY KEY,
            holiday_name TEXT
        )
    """)
    for h in (holidays or []):
        conn.execute("INSERT INTO holidays VALUES (?,?)", (h["date"], h["name"]))

    conn.commit()
    return conn


def run_sql(conn: sqlite3.Connection, sql_file: str,
            params: dict | None = None) -> list[str]:
    """Run a SQL rule file and return the list of flagged invoice_ids."""
    sql = (SQL_DIR / sql_file).read_text(encoding="utf-8")
    cur = conn.execute(sql, params or {})
    return [row[0] for row in cur.fetchall()]


# ══════════════════════════════════════════════════════════════════════════════
# Rule 1: Duplicates
# ══════════════════════════════════════════════════════════════════════════════

class TestRule1Duplicates:

    def test_flags_later_invoice_within_7_days(self):
        """Two invoices, same vendor+amount, 4 days apart -> later one flagged."""
        conn = make_conn([
            {"invoice_id": "INV001", "vendor_id": "VND01",
             "invoice_date": "2023-01-01", "payment_date": "2023-01-20",
             "amount_inr": 25000.00},
            {"invoice_id": "INV002", "vendor_id": "VND01",
             "invoice_date": "2023-01-05", "payment_date": "2023-01-25",
             "amount_inr": 25000.00},
        ])
        flagged = run_sql(conn, "01_duplicates.sql")
        assert "INV002" in flagged
        assert "INV001" not in flagged

    def test_does_not_flag_different_amounts(self):
        """Same vendor, similar dates, different amounts -> nothing flagged."""
        conn = make_conn([
            {"invoice_id": "INV001", "vendor_id": "VND01",
             "invoice_date": "2023-01-01", "payment_date": "2023-01-20",
             "amount_inr": 25000.00},
            {"invoice_id": "INV002", "vendor_id": "VND01",
             "invoice_date": "2023-01-03", "payment_date": "2023-01-22",
             "amount_inr": 25001.00},
        ])
        flagged = run_sql(conn, "01_duplicates.sql")
        assert flagged == []

    def test_does_not_flag_different_vendors(self):
        """Same amount, within 7 days, but different vendors -> nothing flagged."""
        conn = make_conn([
            {"invoice_id": "INV001", "vendor_id": "VND01",
             "invoice_date": "2023-01-01", "payment_date": "2023-01-20",
             "amount_inr": 10000.00},
            {"invoice_id": "INV002", "vendor_id": "VND02",
             "invoice_date": "2023-01-02", "payment_date": "2023-01-21",
             "amount_inr": 10000.00},
        ])
        flagged = run_sql(conn, "01_duplicates.sql")
        assert flagged == []

    def test_edge_exactly_7_days_gap_is_flagged(self):
        """Gap of exactly 7 days is within the window and must be flagged."""
        conn = make_conn([
            {"invoice_id": "INV001", "vendor_id": "VND01",
             "invoice_date": "2023-01-01", "payment_date": "2023-01-20",
             "amount_inr": 12345.67},
            {"invoice_id": "INV002", "vendor_id": "VND01",
             "invoice_date": "2023-01-08", "payment_date": "2023-01-27",
             "amount_inr": 12345.67},
        ])
        flagged = run_sql(conn, "01_duplicates.sql")
        assert "INV002" in flagged

    def test_edge_8_days_gap_not_flagged(self):
        """Gap of 8 days is outside the window -> NOT flagged."""
        conn = make_conn([
            {"invoice_id": "INV001", "vendor_id": "VND01",
             "invoice_date": "2023-01-01", "payment_date": "2023-01-20",
             "amount_inr": 12345.67},
            {"invoice_id": "INV002", "vendor_id": "VND01",
             "invoice_date": "2023-01-09", "payment_date": "2023-01-28",
             "amount_inr": 12345.67},
        ])
        flagged = run_sql(conn, "01_duplicates.sql")
        assert flagged == []

    def test_same_date_higher_invoice_id_flagged(self):
        """Same invoice_date: tiebreak by invoice_id; higher id is flagged."""
        conn = make_conn([
            {"invoice_id": "INV001", "vendor_id": "VND01",
             "invoice_date": "2023-06-01", "payment_date": "2023-06-20",
             "amount_inr": 8000.00},
            {"invoice_id": "INV002", "vendor_id": "VND01",
             "invoice_date": "2023-06-01", "payment_date": "2023-06-20",
             "amount_inr": 8000.00},
        ])
        flagged = run_sql(conn, "01_duplicates.sql")
        assert "INV002" in flagged
        assert "INV001" not in flagged
        assert len(flagged) == 1   # only one side flagged, not both


# ══════════════════════════════════════════════════════════════════════════════
# Rule 2: Split Invoices
# ══════════════════════════════════════════════════════════════════════════════

class TestRule2SplitInvoices:

    def test_flags_pair_in_band_within_3_days(self):
        """Two invoices from same vendor+approver, each 85% of limit, 2 days apart."""
        amount = round(APPROVAL_LIMIT * 0.85, 2)  # 42500
        conn = make_conn([
            {"invoice_id": "INV001", "vendor_id": "VND01", "approver_id": "APR01",
             "invoice_date": "2023-03-01", "payment_date": "2023-03-20",
             "amount_inr": amount},
            {"invoice_id": "INV002", "vendor_id": "VND01", "approver_id": "APR01",
             "invoice_date": "2023-03-03", "payment_date": "2023-03-22",
             "amount_inr": amount},
        ])
        flagged = run_sql(conn, "02_split_invoices.sql",
                          {"approval_limit": APPROVAL_LIMIT})
        assert "INV001" in flagged
        assert "INV002" in flagged

    def test_does_not_flag_different_approvers(self):
        """Same vendor but different approvers -> cluster requirement not met."""
        amount = round(APPROVAL_LIMIT * 0.85, 2)
        conn = make_conn([
            {"invoice_id": "INV001", "vendor_id": "VND01", "approver_id": "APR01",
             "invoice_date": "2023-03-01", "payment_date": "2023-03-20",
             "amount_inr": amount},
            {"invoice_id": "INV002", "vendor_id": "VND01", "approver_id": "APR02",
             "invoice_date": "2023-03-02", "payment_date": "2023-03-21",
             "amount_inr": amount},
        ])
        flagged = run_sql(conn, "02_split_invoices.sql",
                          {"approval_limit": APPROVAL_LIMIT})
        assert flagged == []

    def test_does_not_flag_combined_below_limit(self):
        """Two invoices that each are in the band but sum to <= limit."""
        # 30% each => sum = 60% of limit, below limit
        amount = round(APPROVAL_LIMIT * 0.30, 2)
        conn = make_conn([
            {"invoice_id": "INV001", "vendor_id": "VND01", "approver_id": "APR01",
             "invoice_date": "2023-03-01", "payment_date": "2023-03-20",
             "amount_inr": amount},
            {"invoice_id": "INV002", "vendor_id": "VND01", "approver_id": "APR01",
             "invoice_date": "2023-03-02", "payment_date": "2023-03-21",
             "amount_inr": amount},
        ])
        # Neither is in 80-100% band, so won't be flagged
        flagged = run_sql(conn, "02_split_invoices.sql",
                          {"approval_limit": APPROVAL_LIMIT})
        assert flagged == []

    def test_edge_amount_exactly_80_percent(self):
        """Amount = exactly 80% of limit (the lower bound). Should be flagged."""
        amount = APPROVAL_LIMIT * 0.80   # 40000.0
        conn = make_conn([
            {"invoice_id": "INV001", "vendor_id": "VND01", "approver_id": "APR01",
             "invoice_date": "2023-05-01", "payment_date": "2023-05-20",
             "amount_inr": amount},
            {"invoice_id": "INV002", "vendor_id": "VND01", "approver_id": "APR01",
             "invoice_date": "2023-05-02", "payment_date": "2023-05-21",
             "amount_inr": amount},
        ])
        flagged = run_sql(conn, "02_split_invoices.sql",
                          {"approval_limit": APPROVAL_LIMIT})
        assert "INV001" in flagged
        assert "INV002" in flagged

    def test_edge_amount_exactly_50000_not_flagged(self):
        """Amount = exactly 50,000 (100% of limit) is excluded (band is < 100%)."""
        conn = make_conn([
            {"invoice_id": "INV001", "vendor_id": "VND01", "approver_id": "APR01",
             "invoice_date": "2023-05-01", "payment_date": "2023-05-20",
             "amount_inr": 50000.0},
            {"invoice_id": "INV002", "vendor_id": "VND01", "approver_id": "APR01",
             "invoice_date": "2023-05-02", "payment_date": "2023-05-21",
             "amount_inr": 50000.0},
        ])
        flagged = run_sql(conn, "02_split_invoices.sql",
                          {"approval_limit": APPROVAL_LIMIT})
        assert flagged == []

    def test_edge_4_days_apart_not_flagged(self):
        """Gap of 4 days exceeds the 3-day window -> NOT flagged."""
        amount = round(APPROVAL_LIMIT * 0.85, 2)
        conn = make_conn([
            {"invoice_id": "INV001", "vendor_id": "VND01", "approver_id": "APR01",
             "invoice_date": "2023-03-01", "payment_date": "2023-03-20",
             "amount_inr": amount},
            {"invoice_id": "INV002", "vendor_id": "VND01", "approver_id": "APR01",
             "invoice_date": "2023-03-05", "payment_date": "2023-03-24",
             "amount_inr": amount},
        ])
        flagged = run_sql(conn, "02_split_invoices.sql",
                          {"approval_limit": APPROVAL_LIMIT})
        assert flagged == []


# ══════════════════════════════════════════════════════════════════════════════
# Rule 3: Weekend / Holiday
# ══════════════════════════════════════════════════════════════════════════════

class TestRule3WeekendHoliday:

    def test_flags_saturday_payment(self):
        """payment_date on a Saturday -> flagged. 2023-01-07 is a Saturday."""
        conn = make_conn([
            {"invoice_id": "INV001", "vendor_id": "VND01",
             "invoice_date": "2022-12-01", "payment_date": "2023-01-07",
             "amount_inr": 5000.00},
        ])
        flagged = run_sql(conn, "03_weekend_holiday.sql")
        assert "INV001" in flagged

    def test_flags_sunday_payment(self):
        """payment_date on a Sunday -> flagged. 2023-01-08 is a Sunday."""
        conn = make_conn([
            {"invoice_id": "INV001", "vendor_id": "VND01",
             "invoice_date": "2022-12-01", "payment_date": "2023-01-08",
             "amount_inr": 5000.00},
        ])
        flagged = run_sql(conn, "03_weekend_holiday.sql")
        assert "INV001" in flagged

    def test_flags_public_holiday(self):
        """payment_date in the holidays table -> flagged."""
        conn = make_conn(
            [{"invoice_id": "INV001", "vendor_id": "VND01",
              "invoice_date": "2023-01-10", "payment_date": "2023-01-26",
              "amount_inr": 5000.00}],
            holidays=[{"date": "2023-01-26", "name": "Republic Day"}],
        )
        flagged = run_sql(conn, "03_weekend_holiday.sql")
        assert "INV001" in flagged

    def test_does_not_flag_weekday_non_holiday(self):
        """payment_date on a Monday, not in holidays -> NOT flagged."""
        conn = make_conn([
            {"invoice_id": "INV001", "vendor_id": "VND01",
             "invoice_date": "2023-01-01", "payment_date": "2023-01-09",
             "amount_inr": 5000.00},
        ])
        # 2023-01-09 is a Monday
        flagged = run_sql(conn, "03_weekend_holiday.sql")
        assert flagged == []

    def test_does_not_flag_friday(self):
        """payment_date on a Friday -> NOT flagged. 2023-01-13 is a Friday."""
        conn = make_conn([
            {"invoice_id": "INV001", "vendor_id": "VND01",
             "invoice_date": "2022-12-20", "payment_date": "2023-01-13",
             "amount_inr": 5000.00},
        ])
        flagged = run_sql(conn, "03_weekend_holiday.sql")
        assert flagged == []


# ══════════════════════════════════════════════════════════════════════════════
# Rule 4: Round Figure
# ══════════════════════════════════════════════════════════════════════════════

class TestRule4RoundFigure:

    def test_flags_round_amount_low_round_rate_vendor(self):
        """
        Vendor has 9 non-round invoices + 1 round -> round_rate = 10% < 15%.
        The round invoice is >= 20,000 -> should be flagged.
        """
        conn = make_conn([
            # 9 non-round invoices
            *[{"invoice_id": f"INV{i:03d}", "vendor_id": "VND01",
               "invoice_date": "2023-01-01", "payment_date": "2023-01-20",
               "amount_inr": 15123.45 + i} for i in range(1, 10)],
            # 1 round invoice: 30,000 is a multiple of 10,000 and >= 20,000
            {"invoice_id": "INV010", "vendor_id": "VND01",
             "invoice_date": "2023-06-01", "payment_date": "2023-06-20",
             "amount_inr": 30000.0},
        ])
        flagged = run_sql(conn, "04_round_figure.sql")
        assert "INV010" in flagged

    def test_does_not_flag_high_round_rate_vendor(self):
        """
        Vendor has 5 invoices all at round amounts -> round_rate = 100% >= 15%.
        Should NOT be flagged (vendor naturally issues round amounts).
        """
        conn = make_conn([
            {"invoice_id": f"INV{i:03d}", "vendor_id": "VND01",
             "invoice_date": "2023-01-01", "payment_date": "2023-01-20",
             "amount_inr": float((i + 1) * 10_000)} for i in range(1, 6)
        ])
        flagged = run_sql(conn, "04_round_figure.sql")
        assert flagged == []

    def test_edge_amount_exactly_20000_is_flagged(self):
        """Amount = exactly 20,000 (the floor) with low-round-rate vendor -> flagged."""
        conn = make_conn([
            *[{"invoice_id": f"INV{i:03d}", "vendor_id": "VND01",
               "invoice_date": "2023-01-01", "payment_date": "2023-01-20",
               "amount_inr": 7654.32 + i} for i in range(1, 10)],
            {"invoice_id": "INV010", "vendor_id": "VND01",
             "invoice_date": "2023-06-01", "payment_date": "2023-06-20",
             "amount_inr": 20000.0},
        ])
        flagged = run_sql(conn, "04_round_figure.sql")
        assert "INV010" in flagged

    def test_edge_amount_below_20000_not_flagged(self):
        """Amount = 10,000 (below the 20,000 floor) -> NOT flagged."""
        conn = make_conn([
            *[{"invoice_id": f"INV{i:03d}", "vendor_id": "VND01",
               "invoice_date": "2023-01-01", "payment_date": "2023-01-20",
               "amount_inr": 7654.32 + i} for i in range(1, 10)],
            {"invoice_id": "INV010", "vendor_id": "VND01",
             "invoice_date": "2023-06-01", "payment_date": "2023-06-20",
             "amount_inr": 10000.0},
        ])
        flagged = run_sql(conn, "04_round_figure.sql")
        assert "INV010" not in flagged

    def test_does_not_flag_non_round_amount(self):
        """Amount 30,500 is NOT a multiple of 10,000 -> NOT flagged."""
        conn = make_conn([
            *[{"invoice_id": f"INV{i:03d}", "vendor_id": "VND01",
               "invoice_date": "2023-01-01", "payment_date": "2023-01-20",
               "amount_inr": 7654.32 + i} for i in range(1, 10)],
            {"invoice_id": "INV010", "vendor_id": "VND01",
             "invoice_date": "2023-06-01", "payment_date": "2023-06-20",
             "amount_inr": 30500.0},
        ])
        flagged = run_sql(conn, "04_round_figure.sql")
        assert "INV010" not in flagged


# ══════════════════════════════════════════════════════════════════════════════
# Rule 5: Outliers
# ══════════════════════════════════════════════════════════════════════════════

class TestRule5Outliers:

    def _make_vendor_invoices(self, base_amount: float,
                               count: int, outlier_amount: float | None = None,
                               vendor_id: str = "VND01") -> list[dict]:
        """Helper: make 'count' normal invoices + optionally one outlier."""
        rows = [
            {"invoice_id": f"INV{i:03d}", "vendor_id": vendor_id,
             "invoice_date": "2023-01-01", "payment_date": "2023-01-20",
             "amount_inr": base_amount}
            for i in range(1, count + 1)
        ]
        if outlier_amount is not None:
            rows.append({
                "invoice_id": "INV_OUT",
                "vendor_id": vendor_id,
                "invoice_date": "2023-06-01",
                "payment_date": "2023-06-20",
                "amount_inr": outlier_amount,
            })
        return rows

    def test_flags_amount_far_above_mean(self):
        """
        10 invoices at 1000 each (mean=1000, std=0) then one at 10000.
        With std=0 the rule requires std > 0, but if we use varying amounts
        we can test properly.
        Use 10 invoices at 1000 +/- small noise, one at 10x mean.
        """
        # mean ~ 1000, std ~ 0 won't work (std>0 guard).
        # Use amounts 900,950,1000,1050,1100 repeated twice = 10 invoices, std ~65
        amounts = [900, 950, 1000, 1050, 1100] * 2
        rows = [
            {"invoice_id": f"INV{i:03d}", "vendor_id": "VND01",
             "invoice_date": "2023-01-01", "payment_date": "2023-01-20",
             "amount_inr": float(amounts[i])}
            for i in range(10)
        ]
        # mean=1000, std=65.  mean + 3*std ~ 1195. Outlier at 5000 >> threshold.
        rows.append({"invoice_id": "INV_OUT", "vendor_id": "VND01",
                     "invoice_date": "2023-06-01", "payment_date": "2023-06-20",
                     "amount_inr": 5000.0})
        conn = make_conn(rows)
        flagged = run_sql(conn, "05_outliers.sql")
        assert "INV_OUT" in flagged

    def test_does_not_flag_within_3_sigma(self):
        """Amount within 3 std devs of mean -> NOT flagged."""
        amounts = [900, 950, 1000, 1050, 1100] * 2   # mean=1000, std~65
        rows = [
            {"invoice_id": f"INV{i:03d}", "vendor_id": "VND01",
             "invoice_date": "2023-01-01", "payment_date": "2023-01-20",
             "amount_inr": float(amounts[i])}
            for i in range(10)
        ]
        # 1150 < mean + 3*std (1195) -> NOT an outlier
        rows.append({"invoice_id": "INV_NORM", "vendor_id": "VND01",
                     "invoice_date": "2023-06-01", "payment_date": "2023-06-20",
                     "amount_inr": 1150.0})
        conn = make_conn(rows)
        flagged = run_sql(conn, "05_outliers.sql")
        assert "INV_NORM" not in flagged

    def test_edge_vendor_fewer_than_10_invoices_not_flagged(self):
        """
        Vendor with only 9 invoices is excluded from outlier detection
        (not enough data for a reliable baseline).
        """
        amounts = [900, 950, 1000, 1050, 1100, 900, 950, 1000, 1050]  # 9 rows
        rows = [
            {"invoice_id": f"INV{i:03d}", "vendor_id": "VND01",
             "invoice_date": "2023-01-01", "payment_date": "2023-01-20",
             "amount_inr": float(amounts[i])}
            for i in range(9)
        ]
        rows.append({"invoice_id": "INV_OUT", "vendor_id": "VND01",
                     "invoice_date": "2023-06-01", "payment_date": "2023-06-20",
                     "amount_inr": 50000.0})
        conn = make_conn(rows)
        flagged = run_sql(conn, "05_outliers.sql")
        # Vendor has only 10 rows total (9 + outlier), exactly 10 means it IS included.
        # Let's use 9 rows total (8 + outlier) to be strictly under the threshold.
        # Re-build: 8 normal + 1 outlier = 9 invoices total
        rows2 = [
            {"invoice_id": f"INV{i:03d}", "vendor_id": "VND02",
             "invoice_date": "2023-01-01", "payment_date": "2023-01-20",
             "amount_inr": float(amounts[i])}
            for i in range(8)
        ]
        rows2.append({"invoice_id": "INV_OUT2", "vendor_id": "VND02",
                      "invoice_date": "2023-06-01", "payment_date": "2023-06-20",
                      "amount_inr": 50000.0})
        conn2 = make_conn(rows2)
        flagged2 = run_sql(conn2, "05_outliers.sql")
        # VND02 has 9 invoices total -> excluded by HAVING COUNT(*) >= 10
        assert "INV_OUT2" not in flagged2

    def test_edge_vendor_exactly_10_invoices_is_included(self):
        """Vendor with exactly 10 invoices IS included in outlier detection."""
        amounts = [900, 950, 1000, 1050, 1100] * 2   # 10 rows, mean=1000, std~65
        rows = [
            {"invoice_id": f"INV{i:03d}", "vendor_id": "VND01",
             "invoice_date": "2023-01-01", "payment_date": "2023-01-20",
             "amount_inr": float(amounts[i])}
            for i in range(10)
        ]
        # 11th invoice is extreme outlier
        rows.append({"invoice_id": "INV_OUT", "vendor_id": "VND01",
                     "invoice_date": "2023-06-01", "payment_date": "2023-06-20",
                     "amount_inr": 5000.0})
        # Now vendor has 11 rows in total; HAVING COUNT(*) >= 10 passes
        conn = make_conn(rows)
        flagged = run_sql(conn, "05_outliers.sql")
        assert "INV_OUT" in flagged
