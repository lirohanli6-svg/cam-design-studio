"""Export-first handoff to the independently implemented DXF verifier."""
from __future__ import annotations
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Callable
from .verification import VerificationInput, VerificationResult, verify_dxf
if TYPE_CHECKING:
    from .engine import Result


def audit_candidate(result: Result, cancelled: Callable[[],bool] | None = None) -> tuple[VerificationResult, bytes]:
    from .export import export_design
    with tempfile.TemporaryDirectory(prefix="solarcam-candidate-") as directory:
        dxf=export_design(result,Path(directory))["dxf"]
        q=VerificationInput.from_project(result.project,result.stats["cam_usage"]*360)
        # Reference is comparison only; deliberately no nominal steering input.
        audit=verify_dxf(dxf,q,reference=result.target_path,cancelled=cancelled)
        return audit,dxf.read_bytes()
