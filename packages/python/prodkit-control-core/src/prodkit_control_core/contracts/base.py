from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
TraceId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{32}$")]
SpanId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{16}$")]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)
