from prodkit_control_core.reconciliation.adapters import MappingReconciler


class KubernetesReconciler(MappingReconciler):
    """Normalize kubernetes provider evidence for ProdKit reconciliation."""

    def __init__(self) -> None:
        super().__init__("kubernetes")


__all__ = ("KubernetesReconciler",)
