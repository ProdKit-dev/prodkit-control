from prodkit_control_core.reconciliation.adapters import MappingReconciler


class GitHubReconciler(MappingReconciler):
    """Normalize github provider evidence for ProdKit reconciliation."""

    def __init__(self) -> None:
        super().__init__("github")


__all__ = ("GitHubReconciler",)
