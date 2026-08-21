from prodkit_control_core.reconciliation.adapters import MappingReconciler


class GitReconciler(MappingReconciler):
    """Normalize git provider evidence for ProdKit reconciliation."""

    def __init__(self) -> None:
        super().__init__("git")


__all__ = ("GitReconciler",)
