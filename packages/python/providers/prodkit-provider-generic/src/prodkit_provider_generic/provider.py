from __future__ import annotations

from collections.abc import Awaitable, Callable

from prodkit_control_core import CanonicalModelRequest, CanonicalModelResponse


class GenericModelProvider:
    def __init__(
        self,
        name: str,
        invoke: Callable[[CanonicalModelRequest], Awaitable[CanonicalModelResponse]],
    ) -> None:
        self.name = name
        self._invoke = invoke

    async def invoke(self, request: CanonicalModelRequest) -> CanonicalModelResponse:
        response = await self._invoke(request)
        if response.request_id != request.request_id:
            raise ValueError("provider response request_id does not match request")
        if response.provider_name != request.provider_name:
            raise ValueError("provider response name does not match request")
        return response
