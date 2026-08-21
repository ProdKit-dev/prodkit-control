from prodkit_control_core.reconciliation.adapters import MappingReconciler


class DatabaseReconciler(MappingReconciler):
    """Normalize database provider evidence for ProdKit reconciliation."""

    def __init__(self) -> None:
        super().__init__("database")


__all__ = ("DatabaseReconciler",)
