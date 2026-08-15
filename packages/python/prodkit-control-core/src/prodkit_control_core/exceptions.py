"""Domain exceptions that preserve fail-closed behavior."""


class ProdKitControlError(RuntimeError):
    """Base exception for ProdKit Control failures."""


class IntegrityViolationError(ProdKitControlError):
    """Raised when an event or artifact fails integrity verification."""


class AuthorizationDeniedError(ProdKitControlError):
    """Raised when policy explicitly denies an action."""


class ApprovalRequiredError(ProdKitControlError):
    """Raised when an exact action requires a human or external approval."""

    def __init__(self, action_id: str, action_digest: str) -> None:
        self.action_id = action_id
        self.action_digest = action_digest
        super().__init__(f"Approval required for action {action_id} ({action_digest})")


class ApprovalDeniedError(ProdKitControlError):
    """Raised when approval is denied or invalid for the proposed action."""


class DuplicateActionError(ProdKitControlError):
    """Raised when an idempotency key conflicts with a different action digest."""


class ExecutorNotFoundError(ProdKitControlError):
    """Raised when no controlled executor is registered for an action."""


class UnsupportedSchemaError(ProdKitControlError):
    """Raised when a record uses an unsupported canonical schema version."""


class IncompleteLineageError(ProdKitControlError):
    """Raised when a production observation lacks a complete, acceptable lineage."""

    def __init__(self, missing_requirements: tuple[str, ...]) -> None:
        self.missing_requirements = missing_requirements
        missing = ", ".join(missing_requirements)
        super().__init__(f"Production lineage is incomplete: {missing}")
