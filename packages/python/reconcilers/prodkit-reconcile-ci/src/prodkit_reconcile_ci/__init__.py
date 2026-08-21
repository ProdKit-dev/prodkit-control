from prodkit_control_core.reconciliation.adapters import MappingReconciler


class CIReconciler(MappingReconciler):
    """Normalize ci provider evidence for ProdKit reconciliation."""

    def __init__(self) -> None:
        super().__init__("ci")


__all__ = ("CIReconciler",)
