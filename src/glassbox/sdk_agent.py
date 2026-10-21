"""Raw OpenAI SDK agent loop -- the reference implementation.

Deliberately framework-free so the mechanics are visible: call the model, inspect
what it asked for, validate, execute, feed the result back, repeat until it calls
`finish` or we hit the step cap.

The loop is the only place that mutates conversation state, which means the
invariants we care about hold in exactly one readable function.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .events import EventLog
from .policy import PolicyError, Workspace
from .provider import Completion, Provider
from .registry import ToolError, ToolRegistry, ToolSpec

SYSTEM_PROMPT = """You are a careful coding agent working inside a single workspace.

Operating rules:
1. Never guess about code. Before answering or editing, use the read tools to get
   the exact lines you need. Prefer read_range over assuming file contents.
2. Make one atomic move at a time. Locate, read, then act.
3. Tool arguments are JSON. All paths are relative to the workspace root.
4. When you have completed the task, call `finish` with a short summary and the
   evidence (file paths, line numbers, or command output) that proves it.

Available tools:
{tool_list}
"""


@dataclass
class Step:
    """One recorded turn of the loop, retained for evaluation and debugging."""

    index: int
    tool_name: str | None
    tool_args: dict[str, Any] | None
    tool_ok: bool | None
    tool_result: Any = None
    error: str | None = None
    duration_ms: int = 0


@dataclass
class AgentResult:
    finished: bool
    summary: str | None
    evidence: Any
    steps: list[Step] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    reason: str = "finish"


class Agent:
    def __init__(
        self,
        provider: Provider,
        registry: ToolRegistry,
        workspace: Workspace,
        log: EventLog,
        *,
        model: str | None = None,
        max_steps: int = 12,
        verbose: bool = True,
        run_start_fields: dict[str, Any] | None = None,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.workspace = workspace
        self.log = log
        self.model = model or provider.default_model
        self.max_steps = max_steps
        self.verbose = verbose
        # Extra declared fields merged into run_start (e.g. profile, profile_source).
        self.run_start_fields = run_start_fields or {}

    # ------------------------------------------------------------------ public
    def run(self, task: str) -> AgentResult:
        messages = self._initial_messages(task)
        steps: list[Step] = []

        self.log.emit(
            "run_start",
            task=task,
            model=self.model,
            tools=self.registry.names(),
            workspace=str(self.workspace.root),
            **self.run_start_fields,
        )

        for index in range(1, self.max_steps + 1):
            completion = self._ask(messages, index)
            messages.append(self._assistant_message(completion))

            if not completion.wants_tools:
                # The model answered in prose instead of using the protocol. This is
                # the most common real failure: it did the work, then stopped talking.
                # Recover by asking it to convert its own answer into a finish() call.
                recovered = self._recover_finish(messages, completion, index, steps)
                if recovered is not None:
                    step, call_id, verified = recovered
                    steps.append(step)
                    messages.append(self._assistant_message(completion))
                    messages.append(self._tool_message(call_id, {"acknowledged": True}))
                    reason = "finish_recovered" if verified else "finish_unverified"
                    self.log.emit("run_end", reason=reason, steps=len(steps))
                    return AgentResult(
                        finished=verified,
                        summary=(step.tool_args or {}).get("summary"),
                        evidence=(step.tool_args or {}).get("evidence"),
                        steps=steps,
                        messages=messages,
                        reason=reason,
                    )

                self.log.emit("stall", step=index, content=completion.content)
                # A run must have exactly one terminal event, whatever the exit.
                # Missing this made 1 of 4 historical runs look unfinished.
                self.log.emit("run_end", reason="stall", steps=len(steps))
                return AgentResult(
                    finished=False,
                    summary=None,
                    evidence=None,
                    steps=steps,
                    messages=messages,
                    reason="model stopped without calling a tool",
                )

            terminating = False
            for call in completion.tool_calls:
                step, finish_payload = self._execute(call, index, messages)
                steps.append(step)
                if finish_payload is not None:
                    terminating = True
                    messages.append(self._tool_message(call.id, {"acknowledged": True}))

            if terminating:
                last = steps[-1]
                self.log.emit("run_end", reason="finish", steps=len(steps))
                self._print_done(last)
                return AgentResult(
                    finished=True,
                    summary=(last.tool_args or {}).get("summary"),
                    evidence=(last.tool_args or {}).get("evidence"),
                    steps=steps,
                    messages=messages,
                    reason="finish",
                )

        self.log.emit("run_end", reason="step_cap", steps=len(steps))
        return AgentResult(
            finished=False,
            summary=None,
            evidence=None,
            steps=steps,
            messages=messages,
            reason=f"hit step cap ({self.max_steps})",
        )

    # ----------------------------------------------------------------- internal
    def _initial_messages(self, task: str) -> list[dict[str, Any]]:
        tool_list = "\n".join(f"- {s.name}: {s.description}" for s in
                              (self.registry.get(n) for n in self.registry.names()))
        return [
            {"role": "system", "content": SYSTEM_PROMPT.format(tool_list=tool_list)},
            {"role": "user", "content": task},
        ]

    def _ask(self, messages: list[dict[str, Any]], step: int) -> Completion:
        started = time.perf_counter()
        try:
            completion = self.provider.complete(
                messages,
                model=self.model,
                tools=self.registry.schemas(),
            )
        except Exception as exc:  # noqa: BLE001 - provider failure is a run-level event
            self.log.emit("provider_error", step=step, error=f"{type(exc).__name__}: {exc}")
            raise

        duration = int((time.perf_counter() - started) * 1000)
        self.log.emit(
            "model_call",
            step=step,
            duration_ms=duration,
            finish_reason=completion.finish_reason,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            tool_calls=[c.function.name for c in completion.tool_calls],
            # Real reasoning trace, returned out-of-band by Ollama thinking models.
            reasoning=completion.reasoning,
            content=completion.content,
        )
        if self.verbose:
            trace = (completion.reasoning or "").strip().replace("\n", " ")
            if trace:
                print(f"  [think] {trace[:180]}")
            for c in completion.tool_calls:
                print(f"  [call ] {c.function.name}({c.function.arguments})")
        return completion

    def _assistant_message(self, completion: Completion) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": completion.content or ""}
        if completion.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.function.name, "arguments": c.function.arguments},
                }
                for c in completion.tool_calls
            ]
        return msg

    @staticmethod
    def _tool_message(call_id: str, payload: Any) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "content": json.dumps(payload, default=str, ensure_ascii=False),
        }

    def _execute(
        self, call: Any, index: int, messages: list[dict[str, Any]]
    ) -> tuple[Step, dict[str, Any] | None]:
        """Dispatch one tool call. Returns (step, finish_payload).

        TODO(request-capability-escalation): the `unknown tool` path below is the
        escalation trigger. When the model calls a tool outside its profile, that
        is a request for more capability -- the planned behaviour is to record a
        `capability_requested` event and, once approved, swap in a wider profile
        for subsequent turns instead of just returning an error.
        Tracked in backlog.json; see `python -m glassbox.backlog show request-capability-escalation`.
        """
        name = call.function.name
        started = time.perf_counter()

        args, parse_error = self._parse_args(call.function.arguments)
        if parse_error:
            return self._fail(call, index, name, parse_error, started, messages)

        if name == "finish":
            step = Step(index=index, tool_name=name, tool_args=args, tool_ok=True,
                        duration_ms=int((time.perf_counter() - started) * 1000))
            self.log.emit("finish", step=index, summary=args.get("summary"),
                          evidence=args.get("evidence"))
            return step, args

        try:
            spec = self.registry.get(name)
        except ToolError as exc:
            return self._fail(call, index, name, str(exc), started, messages)

        try:
            result = spec.handler(**args)
        except (ToolError, PolicyError) as exc:
            return self._fail(call, index, name, str(exc), started, messages, spec=spec)
        except TypeError as exc:
            # Wrong/missing arguments -- a model mistake, not a crash.
            return self._fail(call, index, name, f"bad arguments: {exc}", started, messages, spec=spec)
        except Exception as exc:  # noqa: BLE001 - unexpected tool crash must not kill the run
            return self._fail(call, index, name, f"{type(exc).__name__}: {exc}", started,
                              messages, spec=spec)

        duration = int((time.perf_counter() - started) * 1000)
        self.log.emit("tool_call", step=index, tool=name, risk=spec.risk,
                      args=args, ok=True, duration_ms=duration, result=result)
        messages.append(self._tool_message(call.id, result))
        if self.verbose:
            print(f"  [ok   ] {name} -> {self._brief(result)}")
        return Step(index=index, tool_name=name, tool_args=args, tool_ok=True,
                    tool_result=result, duration_ms=duration), None

    def _fail(
        self,
        call: Any,
        index: int,
        name: str,
        message: str,
        started: float,
        messages: list[dict[str, Any]],
        *,
        spec: ToolSpec | None = None,
    ) -> tuple[Step, None]:
        """Record a failed call and hand the error back to the model to recover from."""
        duration = int((time.perf_counter() - started) * 1000)
        payload = {"error": message}
        self.log.emit("tool_call", step=index, tool=name,
                      risk=spec.risk if spec else "unknown",
                      ok=False, error=message, duration_ms=duration)
        messages.append(self._tool_message(call.id, payload))
        if self.verbose:
            print(f"  [err  ] {name}: {message}")
        return Step(index=index, tool_name=name, tool_args=None, tool_ok=False,
                    error=message, duration_ms=duration), None

    def _recover_finish(
        self,
        messages: list[dict[str, Any]],
        completion: Completion,
        index: int,
        steps: list[Step],
    ) -> tuple[Step, str, bool] | None:
        """Convert a prose answer into a protocol `finish` call.

        Why this exists
        ---------------
        Measured across six runs: three ended with `finish_reason="stop"` and no
        tool call. In each case the agent had already read the right file and
        written a correct answer -- it simply replied in prose instead of calling
        `finish`. Treating that as a hard failure throws away good work.

        The trap this deliberately avoids
        ---------------------------------
        Blindly forcing `finish` would turn an honest stall on an EDIT task into a
        FALSE SUCCESS: the model would be compelled to claim completion for work
        it never did. So recovery is only *verified* when the task was read-only.
        If a mutating tool was offered but never called, the run is reported as
        `finish_unverified` with finished=False regardless of what the model claims.
        A confident summary is not evidence.

        Returns (step, tool_call_id, verified) or None when recovery is unavailable.
        """
        if "finish" not in self.registry:
            return None

        # A mutating capability that was offered but never used means the work may
        # not have been done; never upgrade that to a success.
        offered_mutation = any(
            self.registry.get(name).risk in ("write", "exec") for name in self.registry.names()
        )
        used_mutation = any(s.tool_name and s.tool_ok and s.tool_name != "finish"
                            and self._risk_of(s.tool_name) in ("write", "exec")
                            for s in steps)
        verified = not offered_mutation or used_mutation

        probe = list(messages)
        probe.append({"role": "user", "content": (
            "You replied with prose instead of using a tool. Convert your answer "
            "into exactly one `finish` call, reusing your own words. Do not invent "
            "work you did not do."
        )})

        started = time.perf_counter()
        try:
            forced = self.provider.complete(
                probe,
                model=self.model,
                tools=self.registry.schemas(),
                tool_choice={"type": "function", "function": {"name": "finish"}},
            )
        except Exception as exc:  # noqa: BLE001 - recovery is best-effort
            self.log.emit("recovery_failed", step=index,
                          error=f"{type(exc).__name__}: {exc}")
            return None

        call = next((c for c in forced.tool_calls if c.function.name == "finish"), None)
        if call is None:
            self.log.emit("recovery_failed", step=index, error="forced finish was not returned")
            return None

        args, parse_error = self._parse_args(call.function.arguments)
        if parse_error:
            self.log.emit("recovery_failed", step=index, error=parse_error)
            return None

        duration = int((time.perf_counter() - started) * 1000)
        step = Step(index=index, tool_name="finish", tool_args=args,
                    tool_ok=True, duration_ms=duration)
        self.log.emit(
            "finish",
            step=index,
            summary=args.get("summary"),
            evidence=args.get("evidence"),
            # Distinguish a model-driven finish from a coerced one; a reader must
            # be able to tell whether the protocol was followed voluntarily.
            recovered=True,
            verified=verified,
            original_content=completion.content,
        )
        return step, call.id, verified

    def _risk_of(self, tool_name: str) -> str:
        try:
            return self.registry.get(tool_name).risk
        except ToolError:
            return "read"

    @staticmethod
    def _parse_args(raw: str | None) -> tuple[dict[str, Any], str | None]:
        if not raw:
            return {}, None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            return {}, f"tool arguments were not valid JSON: {exc}"
        if not isinstance(parsed, dict):
            return {}, "tool arguments must be a JSON object"
        return parsed, None

    @staticmethod
    def _brief(result: Any) -> str:
        if isinstance(result, dict):
            if "hits" in result:
                return f"{len(result['hits'])} hits"
            if "lines" in result:
                return f"lines {result.get('start')}-{result.get('end')} of {result.get('total_lines')}"
            if "entries" in result:
                return f"{result.get('count')} entries"
            if "definitions" in result:
                return f"{len(result['definitions'])} defs, {result.get('reference_count')} refs"
        return str(result)[:80]

    def _print_done(self, step: Step) -> None:
        if not self.verbose:
            return
        args = step.tool_args or {}
        print(f"  [done ] {args.get('summary')}")
        if args.get("evidence"):
            print(f"  [proof] {json.dumps(args['evidence'], default=str)[:300]}")
