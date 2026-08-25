from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from prodkit_control_core import CanonicalModelRequest, CanonicalModelResponse, ToolProposal

GoogleTransport = Callable[[str, Mapping[str, Any]], Awaitable[Mapping[str, Any]]]


class GoogleProvider:
    """Normalize Google Gemini generateContent responses into canonical provider contracts."""

    name = "google"

    def __init__(self, transport: GoogleTransport) -> None:
        self._transport = transport

    async def invoke(self, request: CanonicalModelRequest) -> CanonicalModelResponse:
        if request.provider_name != self.name:
            raise ValueError("GoogleProvider requires provider_name='google'")
        parameters = dict(request.parameters)
        for reserved in ("contents", "systemInstruction", "model"):
            if reserved in parameters:
                raise ValueError(f"provider parameters cannot override canonical {reserved}")
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []
        for message in request.messages:
            role = message.get("role")
            content = message.get("content")
            if role == "system":
                if isinstance(content, str):
                    system_parts.append(content)
                continue
            mapped_role = "model" if role == "assistant" else "user" if role == "user" else None
            if mapped_role is None:
                raise ValueError(f"unsupported Google message role: {role!r}")
            parts = content if isinstance(content, list) else [{"text": str(content or "")}]
            contents.append({"role": mapped_role, "parts": parts})
        payload: dict[str, Any] = {"contents": contents, **parameters}
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        path = f"/v1beta/models/{quote(request.model_name, safe='')}:generateContent"
        raw = await self._transport(path, payload)
        candidates = raw.get("candidates")
        if (
            not isinstance(candidates, list)
            or not candidates
            or not isinstance(candidates[0], Mapping)
        ):
            raise ValueError("Google response is missing candidates[0]")
        candidate = candidates[0]
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, Mapping) else None
        if not isinstance(parts, list):
            raise ValueError("Google response is missing candidate content parts")
        text_parts: list[str] = []
        proposals: list[ToolProposal] = []
        for index, part in enumerate(parts):
            if not isinstance(part, Mapping):
                continue
            text = part.get("text")
            if isinstance(text, str):
                text_parts.append(text)
            function_call = part.get("functionCall")
            if isinstance(function_call, Mapping):
                name = function_call.get("name")
                arguments = function_call.get("args", {})
                if not isinstance(name, str) or not name or not isinstance(arguments, dict):
                    raise ValueError("Google functionCall requires a name and object args")
                proposals.append(
                    ToolProposal(
                        tool_call_id=f"google-{index}-{name}",
                        tool_name=name,
                        arguments=arguments,
                    )
                )
        usage_raw = raw.get("usageMetadata")
        usage: dict[str, int] = {}
        if isinstance(usage_raw, Mapping):
            for canonical, provider_key in (
                ("input_tokens", "promptTokenCount"),
                ("output_tokens", "candidatesTokenCount"),
                ("total_tokens", "totalTokenCount"),
            ):
                value = usage_raw.get(provider_key)
                if isinstance(value, int) and value >= 0:
                    usage[canonical] = value
        response_id = raw.get("responseId")
        finish_reason = candidate.get("finishReason")
        return CanonicalModelResponse(
            request_id=request.request_id,
            provider_name=self.name,
            requested_model_name=request.model_name,
            returned_model_name=request.model_name,
            provider_run_id=response_id if isinstance(response_id, str) else None,
            completed_at=datetime.now(UTC),
            stop_reason=finish_reason if isinstance(finish_reason, str) and finish_reason else None,
            output_text="".join(text_parts) or None,
            tool_proposals=tuple(proposals),
            usage=usage,
        )
