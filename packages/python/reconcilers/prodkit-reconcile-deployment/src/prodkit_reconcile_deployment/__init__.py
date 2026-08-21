from prodkit_control_core.reconciliation.adapters import MappingReconciler


class DeploymentReconciler(MappingReconciler):
    """Normalize deployment provider evidence for ProdKit reconciliation."""

    def __init__(self) -> None:
        super().__init__("deployment")


__all__ = ("DeploymentReconciler",)
