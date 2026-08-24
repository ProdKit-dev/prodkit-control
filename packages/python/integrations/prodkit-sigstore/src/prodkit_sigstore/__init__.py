"""Sigstore signing and verification integration."""

from .cosign import CosignClient as CosignClient
from .cosign import CosignCommandResult as CosignCommandResult
from .cosign import SigstoreIntegrationError as SigstoreIntegrationError

__version__ = "0.6.0"
