from prodkit_control_core.reconciliation.adapters import MappingReconciler


class RegistryReconciler(MappingReconciler):
    """Normalize package/container registry evidence for ProdKit reconciliation."""

    def __init__(self) -> None:
        super().__init__("registry")


__all__ = ("RegistryReconciler",)
