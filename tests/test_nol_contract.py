"""Tests for the NOL contract module."""

from __future__ import annotations

from riva.nol_contract import (
    compute_intent_hash,
    create_nol_contract,
    criteria_to_nol_assembly,
)


class TestIntentHash:
    """Tests for content-addressable intent hashing."""

    def test_deterministic(self):
        """Same inputs always produce the same hash."""
        h1 = compute_intent_hash("title", ["criterion A", "criterion B"])
        h2 = compute_intent_hash("title", ["criterion A", "criterion B"])
        assert h1 == h2

    def test_order_independent(self):
        """Criteria order does not affect the hash (sorted internally)."""
        h1 = compute_intent_hash("title", ["B", "A"])
        h2 = compute_intent_hash("title", ["A", "B"])
        assert h1 == h2

    def test_different_title_different_hash(self):
        """Different titles produce different hashes."""
        h1 = compute_intent_hash("title A", ["criterion"])
        h2 = compute_intent_hash("title B", ["criterion"])
        assert h1 != h2

    def test_different_criteria_different_hash(self):
        """Different criteria produce different hashes."""
        h1 = compute_intent_hash("title", ["A"])
        h2 = compute_intent_hash("title", ["B"])
        assert h1 != h2

    def test_hash_is_hex_string(self):
        """Hash is a hex string."""
        h = compute_intent_hash("test", ["c"])
        assert all(c in "0123456789abcdef" for c in h)
        assert len(h) == 64  # SHA-256


class TestNolAssembly:
    """Tests for NOL assembly generation."""

    def test_has_intent_comment(self):
        """Assembly contains INTENT comment."""
        asm = criteria_to_nol_assembly("My Plan", ["file exists"])
        assert "; INTENT: My Plan" in asm

    def test_has_hash_comment(self):
        """Assembly contains HASH comment."""
        asm = criteria_to_nol_assembly("My Plan", ["file exists"])
        assert "; HASH:" in asm

    def test_has_post_conditions(self):
        """Each criterion becomes a POST condition."""
        asm = criteria_to_nol_assembly("Plan", ["crit A", "crit B", "crit C"])
        assert "; POST[0]: crit A" in asm
        assert "; POST[1]: crit B" in asm
        assert "; POST[2]: crit C" in asm

    def test_has_valid_body(self):
        """Assembly has CONST and HALT instructions."""
        asm = criteria_to_nol_assembly("Plan", ["criterion"])
        assert "CONST I64 0 0" in asm
        assert "HALT" in asm

    def test_empty_criteria(self):
        """Assembly with no criteria still has INTENT and body."""
        asm = criteria_to_nol_assembly("Plan", [])
        assert "; INTENT: Plan" in asm
        assert "HALT" in asm
        assert "POST" not in asm

    def test_title_truncated_at_80(self):
        """Long titles are truncated to 80 chars in INTENT comment."""
        long_title = "A" * 200
        asm = criteria_to_nol_assembly(long_title, [])
        intent_line = [line for line in asm.split("\n") if "INTENT" in line][0]
        # INTENT: + 80 chars
        assert len(intent_line) <= len("; INTENT: ") + 80


class TestCreateNolContract:
    """Tests for the full contract creation pipeline."""

    def test_without_verification(self):
        """Create contract with verify=False skips nolang binary."""
        result = create_nol_contract(
            "My Plan",
            ["file_exists: src/foo.py", "tests pass"],
            verify=False,
        )
        assert result.assembly is not None
        assert result.intent_hash is not None
        assert result.verified is False
        assert result.verify_error is None

    def test_content_addressable(self):
        """Same inputs produce the same hash."""
        r1 = create_nol_contract("Plan", ["A", "B"], verify=False)
        r2 = create_nol_contract("Plan", ["A", "B"], verify=False)
        assert r1.intent_hash == r2.intent_hash
        assert r1.assembly == r2.assembly

    def test_verify_without_binary(self):
        """Verification fails gracefully when nolang binary is missing."""
        result = create_nol_contract(
            "Plan", ["criterion"], verify=True
        )
        # Without NOLANG_BINARY set and nolang not on PATH,
        # verification should fail but not crash
        assert result.assembly is not None
        assert result.intent_hash is not None
        # verified may be True or False depending on whether nolang is installed
