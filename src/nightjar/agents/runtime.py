from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

from nightjar.agents.mock import MockAgentRuntime
from nightjar.agents.prompts import (
    CRITIC_SYSTEM,
    INVESTIGATOR_SYSTEM,
    PLANNER_SYSTEM,
    TRIAGE_SYSTEM,
    critique_prompt,
    investigation_prompt,
    plan_prompt,
    triage_prompt,
)
from nightjar.agents.types import (
    AgentOutcome,
    AgentRuntime,
    CritiqueInput,
    InvestigationInput,
    InvestigationOutput,
    PlanInput,
    TriageInput,
)
from nightjar.api.settings import Settings
from nightjar.core.models import ActionPlan, Critique, TriageResult

ProviderName = Literal["mock", "openai", "anthropic", "google", "ollama"]


def _build_model(provider: str, model_name: str, api_key: str | None, base_url: str | None) -> Any:
    """Construct a provider model without importing optional SDKs until needed."""
    try:
        if provider == "openai":
            from pydantic_ai.models.openai import OpenAIChatModel
            from pydantic_ai.providers.openai import OpenAIProvider

            return OpenAIChatModel(
                model_name,
                provider=OpenAIProvider(api_key=api_key, base_url=base_url),
            )
        if provider == "anthropic":
            from pydantic_ai.models.anthropic import AnthropicModel
            from pydantic_ai.providers.anthropic import AnthropicProvider

            return AnthropicModel(
                model_name,
                provider=AnthropicProvider(api_key=api_key, base_url=base_url),
            )
        if provider == "google":
            from pydantic_ai.models.google import GoogleModel
            from pydantic_ai.providers.google import GoogleProvider

            return GoogleModel(
                model_name,
                provider=GoogleProvider(api_key=api_key),
            )
        if provider == "ollama":
            from pydantic_ai.models.ollama import OllamaModel
            from pydantic_ai.providers.ollama import OllamaProvider

            return OllamaModel(
                model_name,
                provider=OllamaProvider(
                    base_url=base_url or "http://127.0.0.1:11434/v1",
                    api_key=api_key or "ollama",
                ),
            )
    except ImportError as exc:
        raise RuntimeError(
            f"provider {provider!r} is not installed; install nightjar[{provider}]"
        ) from exc
    raise RuntimeError(f"unsupported model provider: {provider}")


class PydanticAgentRuntime:
    """Provider-agnostic implementation of the four Nightjar roles.

    Each role is a separate agent with a typed output schema and stable
    instructions. Swapping providers changes only the model object constructed
    here; prompts, schemas, evidence, policy and verification are untouched.
    """

    def __init__(
        self,
        *,
        provider: str,
        model_name: str,
        api_key: str | None = None,
        base_url: str | None = None,
        critic_provider: str | None = None,
        critic_model_name: str | None = None,
        critic_api_key: str | None = None,
        critic_base_url: str | None = None,
        timeout_seconds: float = 180.0,
    ) -> None:
        self._provider = provider
        self._model_name = model_name
        self._critic_provider = critic_provider or provider
        self._critic_model_name = critic_model_name or model_name
        self._timeout = timeout_seconds
        self._model = _build_model(provider, model_name, api_key, base_url)
        self._critic_model = (
            self._model
            if self._critic_provider == provider and self._critic_model_name == model_name
            else _build_model(
                self._critic_provider,
                self._critic_model_name,
                critic_api_key,
                critic_base_url,
            )
        )
        self._agents: dict[str, Any] = {}

    def _agent(self, role: str, output_type: Any, instructions: str) -> Any:
        if role not in self._agents:
            from pydantic_ai import Agent

            model = self._critic_model if role == "critic" else self._model
            self._agents[role] = Agent(
                model,
                output_type=output_type,
                instructions=instructions,
                retries=2,
            )
        return self._agents[role]

    async def _run(
        self,
        role: str,
        output_type: Any,
        instructions: str,
        prompt: str,
    ) -> AgentOutcome[Any]:
        agent = self._agent(role, output_type, instructions)
        started = time.perf_counter()
        result = await asyncio.wait_for(agent.run(prompt), timeout=self._timeout)
        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = result.usage
        provider = self._critic_provider if role == "critic" else self._provider
        model = self._critic_model_name if role == "critic" else self._model_name
        return AgentOutcome(
            output=result.output,
            provider=provider,
            model=model,
            latency_ms=latency_ms,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cost_usd=float(getattr(usage, "cost", 0.0) or 0.0),
        )

    async def triage(self, payload: TriageInput) -> AgentOutcome[TriageResult]:
        return await self._run("triage", TriageResult, TRIAGE_SYSTEM, triage_prompt(payload))

    async def investigate(self, payload: InvestigationInput) -> AgentOutcome[InvestigationOutput]:
        return await self._run(
            "investigator", InvestigationOutput, INVESTIGATOR_SYSTEM, investigation_prompt(payload)
        )

    async def critique(self, payload: CritiqueInput) -> AgentOutcome[Critique]:
        return await self._run("critic", Critique, CRITIC_SYSTEM, critique_prompt(payload))

    async def plan(self, payload: PlanInput) -> AgentOutcome[ActionPlan]:
        return await self._run("planner", ActionPlan, PLANNER_SYSTEM, plan_prompt(payload))


def build_runtime(settings: Settings) -> AgentRuntime:
    if settings.model_provider == "mock":
        return MockAgentRuntime()
    return PydanticAgentRuntime(
        provider=settings.model_provider,
        model_name=settings.model_name,
        api_key=settings.model_api_key,
        base_url=settings.model_base_url,
        critic_provider=settings.critic_provider,
        critic_model_name=settings.critic_model_name,
        critic_api_key=settings.critic_api_key,
        critic_base_url=settings.critic_base_url,
    )


__all__ = ["MockAgentRuntime", "PydanticAgentRuntime", "build_runtime"]
