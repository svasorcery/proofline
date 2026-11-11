"""Public deterministic-validation interface."""

from proofline.validation.assembly import validate_assembly
from proofline.validation.result import ResolvedFragment, ValidationIssue, ValidationResult

__all__ = ["ResolvedFragment", "ValidationIssue", "ValidationResult", "validate_assembly"]
