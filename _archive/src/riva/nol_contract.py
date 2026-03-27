"""NOL contract generation and verification.

Translates plan acceptance criteria into NOL assembly with inline POST
conditions. Provides content-addressable contracts via intent hashing,
and structural verification via the nolang assembler + verifier.

Adapted from RIVA archive: code_mode_legacy/src/code_mode/intent_to_nol.py
and code_mode_legacy/src/code_mode/nol_bridge.py.

The NOL layer is optional — if the nolang binary is not available,
contracts are created without NOL assembly but with typed criteria.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# nolang binary location from environment, with shutil.which fallback
_NOL_BINARY_ENV = "NOLANG_BINARY"


def _get_nol_binary() -> Path | None:
    """Resolve the nolang binary path from environment or PATH."""
    env_path = os.environ.get(_NOL_BINARY_ENV)
    if env_path:
        p = Path(env_path)
        return p if p.exists() else None

    import shutil

    found = shutil.which("nolang")
    return Path(found) if found else None


@dataclass(frozen=True)
class NolContractResult:
    """Result of translating plan criteria to NOL assembly."""

    assembly: str
    intent_hash: str
    verified: bool
    verify_error: str | None = None


@dataclass(frozen=True)
class NolVerifyResult:
    """Result of verifying NOL assembly via the nolang binary."""

    success: bool
    instruction_count: int = 0
    error: str | None = None


def compute_intent_hash(title: str, acceptance_criteria: list[str]) -> str:
    """Compute a deterministic hash for a contract intent.

    Same title + same sorted criteria = same hash = content-addressable.
    """
    content = title + "\n" + "\n".join(sorted(acceptance_criteria))
    return hashlib.sha256(content.encode()).hexdigest()


def criteria_to_nol_assembly(
    title: str,
    acceptance_criteria: list[str],
) -> str:
    """Translate plan title and acceptance criteria into NOL assembly.

    Each criterion becomes a POST condition comment. The assembly is a
    minimal skeleton (CONST I64 0 0 / HALT) that assembles immediately.
    The value is in the inline contracts, not the computation.

    Args:
        title: The plan title (becomes INTENT comment).
        acceptance_criteria: List of verifiable criteria from plan steps.

    Returns:
        NOL assembly text with inline POST conditions.
    """
    lines: list[str] = []

    # Intent description
    lines.append(f"; INTENT: {title[:80]}")
    lines.append(f"; HASH: {compute_intent_hash(title, acceptance_criteria)[:16]}")

    # Each criterion becomes a POST condition
    for i, criterion in enumerate(acceptance_criteria):
        lines.append(f"; POST[{i}]: {criterion}")

    # Minimal body — the contract's value is in the POST conditions
    lines.append("CONST I64 0 0")
    lines.append("HALT")

    return "\n".join(lines) + "\n"


def verify_nol_assembly(
    assembly: str,
    nol_binary: Path | None = None,
    timeout: float = 10.0,
) -> NolVerifyResult:
    """Assemble and verify NOL assembly via the nolang CLI.

    Args:
        assembly: NOL assembly text.
        nol_binary: Path to the nolang binary. Defaults to env/PATH lookup.
        timeout: Subprocess timeout in seconds.

    Returns:
        NolVerifyResult with success status and any errors.
    """
    binary = nol_binary or _get_nol_binary()

    if binary is None:
        return NolVerifyResult(
            success=False,
            error="nolang binary not found (set NOLANG_BINARY env var)",
        )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".nol", delete=True
    ) as asm_file:
        asm_file.write(assembly)
        asm_file.flush()

        # Step 1: Assemble
        try:
            asm_result = subprocess.run(
                [str(binary), "assemble", asm_file.name],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return NolVerifyResult(success=False, error="Assembly timed out")

        if asm_result.returncode != 0:
            return NolVerifyResult(
                success=False,
                error=f"Assembly failed: {asm_result.stderr.strip()}",
            )

        # The assembler outputs the binary path
        binary_path = asm_result.stdout.strip()
        if not binary_path or not Path(binary_path).exists():
            binary_path = asm_file.name.replace(".nol", ".nolb")

        if not Path(binary_path).exists():
            return NolVerifyResult(
                success=False,
                error="Assembly produced no output binary",
            )

        # Step 2: Verify
        try:
            ver_result = subprocess.run(
                [str(binary), "verify", binary_path],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return NolVerifyResult(success=False, error="Verification timed out")

        if ver_result.returncode != 0:
            return NolVerifyResult(
                success=False,
                error=f"Verification failed: {ver_result.stderr.strip()}",
            )

        # Parse instruction count from verify output if available
        instruction_count = 0
        for line in ver_result.stdout.splitlines():
            if "instruction" in line.lower():
                try:
                    instruction_count = int("".join(c for c in line if c.isdigit()))
                except ValueError:
                    pass

        return NolVerifyResult(success=True, instruction_count=instruction_count)


def create_nol_contract(
    title: str,
    acceptance_criteria: list[str],
    *,
    verify: bool = True,
    nol_binary: Path | None = None,
) -> NolContractResult:
    """Create a NOL contract from plan title and acceptance criteria.

    Translates criteria to NOL assembly with inline POST conditions,
    computes the content-addressable intent hash, and optionally
    verifies the assembly via the nolang binary.

    Args:
        title: Plan title.
        acceptance_criteria: Verifiable criteria from plan steps.
        verify: Whether to run the NOL verifier (requires nolang binary).
        nol_binary: Override path to nolang binary.

    Returns:
        NolContractResult with assembly, hash, and verification status.
    """
    assembly = criteria_to_nol_assembly(title, acceptance_criteria)
    intent_hash = compute_intent_hash(title, acceptance_criteria)

    verified = False
    verify_error = None

    if verify:
        result = verify_nol_assembly(assembly, nol_binary=nol_binary)
        verified = result.success
        verify_error = result.error
        if verified:
            logger.info(
                "NOL contract verified: %s (hash=%s, instructions=%d)",
                title[:50],
                intent_hash[:12],
                result.instruction_count,
            )
        else:
            logger.warning(
                "NOL contract verification failed: %s — %s",
                title[:50],
                verify_error,
            )

    return NolContractResult(
        assembly=assembly,
        intent_hash=intent_hash,
        verified=verified,
        verify_error=verify_error,
    )
