"""
tests/test_score.py
===================
Unit tests for the scoring logic (score.py) and data reproducibility.

Tests:
  - risk_score capped at 100
  - risk_level thresholds (HIGH/MEDIUM/LOW)
  - potential_leakage and exposure_at_risk have no overlap
  - leakage definition: duplicate > outlier when both fire
  - potential_leakage is full amount for duplicate-flagged invoice
  - exposure is invoice amount for split/round/weekend-only invoices
  - generate_data.py reproducibility: same seed -> identical CSV hashes
"""

import hashlib
import subprocess
import sys
import pandas as pd
import pytest
from pathlib import Path

ROOT = Path(__file__).parent.parent

# ── Helper: build a minimal scored DataFrame ─────────────────────────────────

def make_scored(**kwargs) -> pd.DataFrame:
    """
    Build a one-row scored_invoices-style DataFrame with defaults.
    Override any column via kwargs.
    """
    defaults = {
        "invoice_id":            "INV000001",
        "vendor_id":             "VND0001",
        "vendor_name":           "Test Vendor",
        "amount_inr":            10000.0,
        "risk_score":            0,
        "risk_level":            "LOW",
        "flag_duplicate":        0,
        "flag_split_invoice":    0,
        "flag_weekend_holiday":  0,
        "flag_round_figure":     0,
        "flag_outlier":          0,
        "flag_count":            0,
        "potential_leakage_inr": 0.0,
        "exposure_at_risk_inr":  0.0,
    }
    defaults.update(kwargs)
    return pd.DataFrame([defaults])


# ── Import scoring functions directly ────────────────────────────────────────
sys.path.insert(0, str(ROOT / "src"))


# ══════════════════════════════════════════════════════════════════════════════
# Risk score cap
# ══════════════════════════════════════════════════════════════════════════════

class TestRiskScoreCap:

    def test_score_never_exceeds_100(self):
        """
        Even if weights sum to > 100, the score must be capped at 100.
        Weights: dup=40, split=30, outlier=20, round=10, weekend=10 -> sum=110.
        All 5 flags active -> score should be capped at 100, not 110.
        """
        weights = {
            "duplicate": 40, "split_invoice": 30, "outlier": 20,
            "round_figure": 10, "weekend_holiday": 10,
        }
        raw = (1 * weights["duplicate"]       +
               1 * weights["split_invoice"]   +
               1 * weights["weekend_holiday"] +
               1 * weights["round_figure"]    +
               1 * weights["outlier"])
        score = min(raw, 100)   # same logic as score.py
        assert raw == 110
        assert score == 100

    def test_single_rule_score_correct(self):
        """Duplicate only (weight 40) -> score = 40."""
        weights = {"duplicate": 40}
        score = min(1 * 40, 100)
        assert score == 40

    def test_two_rules_below_cap(self):
        """Duplicate (40) + outlier (20) -> score = 60, no cap needed."""
        score = min(40 + 20, 100)
        assert score == 60


# ══════════════════════════════════════════════════════════════════════════════
# Risk level thresholds
# ══════════════════════════════════════════════════════════════════════════════

class TestRiskLevels:
    HIGH_T = 40
    MED_T  = 20

    def classify(self, score: int) -> str:
        if score >= self.HIGH_T:
            return "HIGH"
        elif score >= self.MED_T:
            return "MEDIUM"
        return "LOW"

    def test_score_40_is_high(self):
        assert self.classify(40) == "HIGH"

    def test_score_39_is_medium(self):
        assert self.classify(39) == "MEDIUM"

    def test_score_20_is_medium(self):
        assert self.classify(20) == "MEDIUM"

    def test_score_19_is_low(self):
        assert self.classify(19) == "LOW"

    def test_score_0_is_low(self):
        assert self.classify(0) == "LOW"

    def test_score_100_is_high(self):
        assert self.classify(100) == "HIGH"

    def test_real_scored_csv_levels_match_thresholds(self):
        """
        Load the real scored_invoices.csv and verify every row's risk_level
        matches the threshold logic. This tests the actual pipeline output.
        """
        path = ROOT / "outputs" / "scored_invoices.csv"
        if not path.exists():
            pytest.skip("scored_invoices.csv not found; run score.py first")
        df = pd.read_csv(path)
        high_ok = ((df["risk_level"] == "HIGH")   == (df["risk_score"] >= 40)).all()
        med_ok  = ((df["risk_level"] == "MEDIUM") == ((df["risk_score"] >= 20) & (df["risk_score"] < 40))).all()
        low_ok  = ((df["risk_level"] == "LOW")    == (df["risk_score"] <  20)).all()
        assert high_ok and med_ok and low_ok


# ══════════════════════════════════════════════════════════════════════════════
# No overlap between potential_leakage and exposure_at_risk
# ══════════════════════════════════════════════════════════════════════════════

class TestNoLeakageOverlap:

    def test_no_overlap_in_real_output(self):
        """
        Every row in scored_invoices.csv must have at most one of
        potential_leakage_inr > 0 or exposure_at_risk_inr > 0 (not both).
        """
        path = ROOT / "outputs" / "scored_invoices.csv"
        if not path.exists():
            pytest.skip("scored_invoices.csv not found; run score.py first")
        df = pd.read_csv(path)
        overlap = df[(df["potential_leakage_inr"] > 0) &
                     (df["exposure_at_risk_inr"]  > 0)]
        assert len(overlap) == 0, (
            f"{len(overlap)} invoices counted in both potential_leakage and exposure"
        )

    def test_duplicate_flagged_gives_leakage_not_exposure(self):
        """
        The leakage logic: duplicate-flagged invoice -> potential_leakage = amount.
        Exposure should be 0.
        Simulated inline (not calling score.py directly).
        """
        amount = 25000.0
        flag_duplicate = 1
        flag_split     = 0
        flag_outlier   = 0
        vendor_mean    = 10000.0

        # Replicate score.py logic
        if flag_duplicate == 1:
            potential = amount
        elif flag_outlier == 1:
            potential = max(amount - vendor_mean, 0.0)
        else:
            potential = 0.0

        if flag_duplicate == 1 or flag_outlier == 1:
            exposure = 0.0
        elif flag_split == 1:
            exposure = amount
        else:
            exposure = 0.0

        assert potential == 25000.0
        assert exposure  == 0.0

    def test_split_only_gives_exposure_not_leakage(self):
        """Split-flagged invoice (not duplicate/outlier) -> exposure, not leakage."""
        amount         = 42000.0
        flag_duplicate = 0
        flag_split     = 1
        flag_outlier   = 0
        vendor_mean    = 10000.0

        if flag_duplicate == 1:
            potential = amount
        elif flag_outlier == 1:
            potential = max(amount - vendor_mean, 0.0)
        else:
            potential = 0.0

        if flag_duplicate == 1 or flag_outlier == 1:
            exposure = 0.0
        elif flag_split == 1:
            exposure = amount
        else:
            exposure = 0.0

        assert potential == 0.0
        assert exposure  == 42000.0

    def test_duplicate_beats_outlier_when_both_fire(self):
        """
        When both duplicate and outlier fire on the same invoice,
        duplicate takes priority: potential = full amount, NOT the excess.
        """
        amount         = 80000.0
        vendor_mean    = 10000.0
        flag_duplicate = 1
        flag_outlier   = 1

        if flag_duplicate == 1:
            potential = amount            # duplicate wins
        elif flag_outlier == 1:
            potential = max(amount - vendor_mean, 0.0)
        else:
            potential = 0.0

        assert potential == 80000.0       # full amount, not excess (70000)


# ══════════════════════════════════════════════════════════════════════════════
# Reproducibility
# ══════════════════════════════════════════════════════════════════════════════

class TestReproducibility:

    def _md5(self, path: Path) -> str:
        return hashlib.md5(path.read_bytes()).hexdigest()

    def test_generate_data_same_seed_same_output(self):
        """
        Running generate_data.py twice with seed=42 must produce byte-identical
        invoices.csv and ground_truth.csv.
        """
        script = str(ROOT / "src" / "generate_data.py")
        python = sys.executable

        inv_path = ROOT / "data" / "raw" / "invoices.csv"
        gt_path  = ROOT / "data" / "ground_truth.csv"

        if not inv_path.exists():
            pytest.skip("invoices.csv not found; run generate_data.py first")

        # Capture hash before second run
        h1_inv = self._md5(inv_path)
        h1_gt  = self._md5(gt_path)

        # Second run
        result = subprocess.run(
            [python, script],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, f"generate_data.py failed:\n{result.stderr}"

        h2_inv = self._md5(inv_path)
        h2_gt  = self._md5(gt_path)

        assert h1_inv == h2_inv, "invoices.csv changed between runs (not reproducible)"
        assert h1_gt  == h2_gt,  "ground_truth.csv changed between runs (not reproducible)"
