"""Memory-aware ReAct loop for the Customer Agent — T133.

MemoryAwareReActLoop (Customer variant):
    1. Check contact_alias memory → if hit, return immediately (memory_hit=True)
    2. Retrieve semantic memories + similar past tasks from MemoryService
    3. Build memory context string injected into params._memory_context
    4. Execute tool calls with HITL gate for mutating tools
    5. Extract ≤ 3 contact facts via GPT-4o-mini (best-effort)
    6. Record task history (best-effort)

Memory types stored:
    contact_alias    — "anh Lâm" → {customer_id, full_name, phone}
    customer_profile — customer_id → name, phone, address summary
    lookup_pattern   — tenant-level query patterns (e.g., "anh X" → strip honorific)
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from shared.memory_service import MemoryService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HITL prompts (Customer variant)
# ---------------------------------------------------------------------------

_CONFIRM_PROMPT = """
Bạn là trợ lý tạo thông báo xác nhận cho người dùng trước khi thực hiện thao tác quan trọng.
Viết một câu thông báo ngắn gọn bằng tiếng Việt (tối đa 2 câu) mô tả:
1. Hành động sắp được thực hiện
2. Tác động với dữ liệu (dựa vào Impact description)
Sau đó hỏi: "Bạn có xác nhận không?"
Không được thêm nội dung khác. Chỉ trả về thông báo thuần văn bản.
""".strip()

_CLASSIFY_HITL_PROMPT = """
Phân loại phản hồi của người dùng sau khi được hỏi xác nhận thao tác vào đúng 1 trong 4 loại:
- confirm: Người dùng đồng ý (xác nhận, ok, được, yes, đúng rồi, ...)
- cancel: Người dùng từ chối (huỷ, không, thôi, bỏ đi, cancel, ...)
- modify: Người dùng muốn sửa đổi thông tin (sửa thành, thay bằng, đổi lại, ...)
- scope_change: Người dùng đổi hoàn toàn yêu cầu sang chủ đề khác

Khi không chắc chắn → chọn cancel.
Trả về JSON: {"intent": "confirm" | "modify" | "cancel" | "scope_change"}
""".strip()

# ---------------------------------------------------------------------------
# Learning extraction prompt
# ---------------------------------------------------------------------------

_EXTRACT_LEARNINGS_PROMPT = """
Dựa trên thao tác khách hàng vừa thực hiện, extract tối đa 3 facts hữu ích cho tương lai.
Output JSON array, mỗi fact gồm: {"memory_type", "key", "content", "confidence"}.

Các loại fact hữu ích (customer-agent):
- contact_alias:    "anh Lâm" → {customer_id, full_name, phone}   (key: alias text)
- customer_profile: customer_id → name/phone/address summary       (key: customer_id)
- lookup_pattern:   tenant-level alias pattern                     (key: tenant_id)

QUAN TRỌNG: Chỉ lưu alias/profile cụ thể, KHÔNG lưu dữ liệu nhạy cảm (mật khẩu, CCCD).
Nếu không có fact nào đáng lưu → trả về [].
""".strip()

# Module-level tools config cache
_TOOLS_CONFIG_CACHE: dict | None = None


def _load_tools_config() -> dict:
    """Load tools.yaml and return a dict keyed by tool name. Cached after first load."""
    global _TOOLS_CONFIG_CACHE
    if _TOOLS_CONFIG_CACHE is not None:
        return _TOOLS_CONFIG_CACHE
    try:
        import yaml  # noqa: PLC0415

        path = os.environ.get("TOOLS_YAML_PATH", "config/tools.yaml")
        with open(path) as f:
            raw = yaml.safe_load(f)
        _TOOLS_CONFIG_CACHE = {t["name"]: t for t in raw.get("tools", [])}
    except Exception as exc:
        logger.warning("tools_config_load_failed", extra={"error": str(exc)})
        _TOOLS_CONFIG_CACHE = {}
    return _TOOLS_CONFIG_CACHE


async def _generate_confirm_message(tool_name: str, args: dict, impact_template: str) -> str:
    """Generate Vietnamese HITL confirmation message via GPT-4o-mini."""
    from shared.llm_client import chat_completion_async  # noqa: PLC0415

    try:
        rendered_impact = impact_template.format(**args)
    except KeyError:
        rendered_impact = impact_template

    user_content = (
        f"Tool: {tool_name}\n"
        f"Args: {json.dumps(args, ensure_ascii=False)[:200]}\n"
        f"Impact description: {rendered_impact}"
    )
    content, _ = await chat_completion_async(
        messages=[
            {"role": "system", "content": _CONFIRM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        task_type="confirm_message",
        extra_log={"agent": "customer-agent", "tool": tool_name},
    )
    return content


async def _classify_hitl_response(user_response: str, tool_name: str) -> str:
    """Classify user response to HITL pause into confirm|modify|cancel|scope_change."""
    from shared.llm_client import chat_completion_async  # noqa: PLC0415

    schema = {
        "name": "hitl_classification",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": ["confirm", "modify", "cancel", "scope_change"],
                }
            },
            "required": ["intent"],
            "additionalProperties": False,
        },
    }
    user_content = f"Tool cần xác nhận: {tool_name}\nPhản hồi người dùng: {user_response}"
    try:
        content, _ = await chat_completion_async(
            messages=[
                {"role": "system", "content": _CLASSIFY_HITL_PROMPT},
                {"role": "user", "content": user_content},
            ],
            task_type="classify_hitl_response",
            json_schema=schema,
            extra_log={"agent": "customer-agent", "tool": tool_name},
        )
        return json.loads(content).get("intent", "cancel")
    except Exception as exc:
        logger.debug("hitl_classify_failed", extra={"error": str(exc)})
        return "cancel"


def build_agent_system_prompt(
    base_prompt: str,
    payload: Any = None,
    memory_context: str = "",
) -> str:
    """Assemble the Customer Agent system prompt from A2ATaskPayload + memory context.

    Sections injected (only when non-empty):
    1. Base agent role prompt (always present)
    2. ## Nhiệm Vụ Hiện Tại — payload.instructions (per-step goal from Plan LLM)
    3. ## Yêu Cầu Gốc — payload.original_message (raw user message)
    4. ## Kết Quả Từ Bước Trước — payload.dependency_results (upstream results)
    5. memory_context (## Kiến Thức Tích Lũy + ## Bài Học Từ Task Tương Tự)
    """
    parts = [base_prompt]

    if payload is not None:
        if getattr(payload, "instructions", ""):
            parts.append(f"## Nhiệm Vụ Hiện Tại\n{payload.instructions}")
        if getattr(payload, "original_message", ""):
            parts.append(f"## Yêu Cầu Gốc\n{payload.original_message}")
        dep_results = getattr(payload, "dependency_results", {})
        if dep_results:
            lines = ["## Kết Quả Từ Bước Trước"]
            for agent_name, result in dep_results.items():
                output = result.get("output", result) if isinstance(result, dict) else result
                lines.append(f"- **{agent_name}**: {json.dumps(output, ensure_ascii=False)[:300]}")
            parts.append("\n".join(lines))

    if memory_context:
        parts.append(memory_context)

    return "\n\n".join(parts)


def _resolve_alias_from_memory(
    alias: str,
    semantic_mem: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return first contact_alias memory entry that matches the alias key."""
    for m in semantic_mem:
        if m.get("memory_type") == "contact_alias" and m.get("key") == alias:
            try:
                content = m["content"]
                return json.loads(content) if isinstance(content, str) else content
            except (json.JSONDecodeError, KeyError):
                pass
    return None


class MemoryAwareReActLoop:
    """Wraps customer lookup/create/update with memory retrieval, HITL, and learning extraction."""

    def __init__(self, skill: str = "lookup_customer") -> None:
        self._skill = skill

    async def _execute_tool(
        self,
        tool_name: str,
        args: dict,
        task_id: str = "",
        redis: "aioredis.Redis | None" = None,
        tool_registry_url: str = "",
        tools_config: dict | None = None,
    ) -> dict:
        """Execute a tool with HITL gate check (Customer variant).

        Returns ``{"__hitl__": True, ...}`` if the tool requires confirmation,
        otherwise executes directly via ToolRegistryClient.
        """
        config = tools_config if tools_config is not None else _load_tools_config()
        tool_def = config.get(tool_name, {})

        if tool_def.get("requires_confirmation", False):
            impact_template = tool_def.get("impact_template", "")
            try:
                confirm_msg = await _generate_confirm_message(tool_name, args, impact_template)
            except Exception as exc:
                logger.debug("confirm_message_failed", extra={"error": str(exc)})
                confirm_msg = f"Xác nhận thực hiện '{tool_name}'?"

            hitl_state = {
                "pending_tool": tool_name,
                "pending_args": args,
                "confirm_message": confirm_msg,
            }
            if redis is not None and task_id:
                await redis.set(
                    f"hitl:{task_id}",
                    json.dumps(hitl_state, ensure_ascii=False),
                    ex=3600,
                )
            return {
                "__hitl__": True,
                "question": confirm_msg,
                "pending_tool": tool_name,
                "pending_args": args,
            }
        else:
            from shared.tool_registry_client import ToolRegistryClient  # noqa: PLC0415

            url = tool_registry_url or os.environ.get("TOOL_REGISTRY_URL", "")
            client = ToolRegistryClient(base_url=url)
            return await client.execute(tool_name, args)

    async def resume_after_hitl(
        self,
        user_response: str,
        task_id: str,
        redis: "aioredis.Redis",
        tool_registry_url: str = "",
    ) -> dict:
        """Handle user response after a HITL pause (Customer variant)."""
        hitl_raw = await redis.get(f"hitl:{task_id}")
        if not hitl_raw:
            logger.warning("hitl_state_missing", extra={"task_id": task_id})
            return {"status": "error", "error": f"HITL state not found for task {task_id}"}

        raw = hitl_raw if isinstance(hitl_raw, str) else hitl_raw.decode()
        hitl_state = json.loads(raw)
        pending_tool = hitl_state["pending_tool"]
        pending_args = hitl_state["pending_args"]

        try:
            intent = await _classify_hitl_response(user_response, pending_tool)
        except Exception as exc:
            logger.debug("hitl_resume_classify_failed", extra={"error": str(exc)})
            intent = "cancel"

        await redis.delete(f"hitl:{task_id}")

        if intent == "confirm":
            from shared.tool_registry_client import ToolRegistryClient  # noqa: PLC0415

            url = tool_registry_url or os.environ.get("TOOL_REGISTRY_URL", "")
            client = ToolRegistryClient(base_url=url)
            try:
                result = await client.execute(pending_tool, pending_args)
                return {"status": "confirmed", "result": result, "tool": pending_tool}
            except Exception as exc:
                return {"status": "error", "error": str(exc), "tool": pending_tool}
        elif intent == "modify":
            return {"status": "modify", "user_feedback": user_response, "tool": pending_tool}
        elif intent == "scope_change":
            return {"__scope_change__": True, "new_request": user_response, "tool": pending_tool}
        else:
            return {
                "status": "cancelled",
                "message": "Đã huỷ thao tác theo yêu cầu của bạn.",
                "tool": pending_tool,
            }

    async def run(
        self,
        task_id: str,
        params: dict[str, Any],
        memory: "MemoryService | None",
        payload: Any = None,
        redis: "aioredis.Redis | None" = None,
    ) -> dict[str, Any]:
        """Execute Customer Agent pipeline with memory augmentation.

        Steps:
        1. Retrieve memories (contact_alias, customer_profile, lookup_pattern)
        2. If contact_alias hit → return immediately (memory_hit=True)
        3. Execute tool call(s) via _execute_tool (HITL gate applies)
        4. Extract contact facts (best-effort)
        5. Record task history (best-effort)

        Args:
            payload: Optional A2ATaskPayload — if provided, build_agent_system_prompt()
                     injects instructions, original_message, and dependency_results.

        Returns dict with keys: status, output (CustomerLookupResult fields), error.
        """
        t0 = time.monotonic()
        tenant_id = params.get("_tenant_id", "default")
        plan_id = params.get("_plan_id")
        message = (
            getattr(payload, "original_message", None)
            or params.get("message", "")
        )

        if payload is not None:
            tenant_id = getattr(payload, "tenant_id", tenant_id)
            plan_id = getattr(payload, "plan_id", plan_id)

        # ── Step 1: Retrieve memories ─────────────────────────────────────
        semantic_mem: list[dict[str, Any]] = []
        similar_tasks: list[dict[str, Any]] = []
        if memory is not None:
            try:
                keyword = message[:50] if message else ""
                semantic_mem = await memory.retrieve(
                    tenant_id=tenant_id,
                    query_context={"keyword": keyword},
                    limit=5,
                )
                similar_tasks = await memory.find_similar_tasks(
                    tenant_id=tenant_id,
                    skill=self._skill,
                    input_summary=message,
                    limit=3,
                )
            except Exception as exc:
                logger.debug("memory_retrieval_failed", extra={"error": str(exc)})

        # ── Step 2: Check contact_alias memory (lookup_customer only) ─────
        if self._skill == "lookup_customer" and semantic_mem:
            alias_hit = _resolve_alias_from_memory(message.strip(), semantic_mem)
            if alias_hit is not None:
                customer_id = alias_hit.get("customer_id", "")
                full_name = alias_hit.get("full_name", "")
                phone = alias_hit.get("phone", "")
                logger.debug(
                    "contact_alias_hit",
                    extra={"alias": message[:50], "customer_id": customer_id},
                )
                duration_ms = int((time.monotonic() - t0) * 1000)
                if memory is not None:
                    try:
                        await memory.record_task(
                            tenant_id=tenant_id,
                            plan_id=plan_id,
                            skill=self._skill,
                            input_summary=f"lookup:{message[:180]}"[:200],
                            outcome="success",
                            key_decisions={"memory_hit": True, "customer_id": customer_id},
                            learnings="",
                            duration_ms=duration_ms,
                        )
                    except Exception as exc:
                        logger.debug("record_task_failed", extra={"error": str(exc)})

                return {
                    "status": "completed",
                    "output": {
                        "skill": self._skill,
                        "customers": [
                            {
                                "customer_id": customer_id,
                                "name": full_name,
                                "phone": phone,
                            }
                        ],
                        "customer_id": customer_id,
                        "action": "lookup",
                        "memory_hit": True,
                        "message": f"Khách hàng '{message.strip()}' là {full_name} (ID: {customer_id}) — lấy từ bộ nhớ.",
                    },
                }

        # ── Step 3: Build memory context + augmented params ───────────────
        memory_context = _build_memory_context(semantic_mem, similar_tasks)

        # For lookup_customer: map message to get_customers tool call
        # For create/update: delegate to graph (placeholder — invoke tool directly)
        tool_name, tool_args = _map_skill_to_tool(self._skill, params, message)

        if not tool_name:
            return {"status": "error", "error": f"Unknown skill: {self._skill}"}

        tool_result = await self._execute_tool(
            tool_name=tool_name,
            args=tool_args,
            task_id=task_id,
            redis=redis,
            tools_config=_load_tools_config(),
        )

        # HITL gate triggered — propagate up
        if tool_result.get("__hitl__"):
            return tool_result

        duration_ms = int((time.monotonic() - t0) * 1000)
        outcome = "success" if "error" not in tool_result else "failed"

        # ── Steps 4–5: Extract contact facts + store (best-effort) ────────
        learnings_json = ""
        if memory is not None and outcome == "success" and self._skill == "lookup_customer":
            try:
                learnings_json = await _extract_learnings(message, tool_name, tool_result)
                await _store_learnings(memory, tenant_id, learnings_json)
            except Exception as exc:
                logger.debug("learning_extraction_failed", extra={"error": str(exc)})

        # ── Step 6: Record task history (best-effort) ─────────────────────
        if memory is not None:
            input_summary = f"{self._skill}:{message[:180]}"[:200]
            try:
                await memory.record_task(
                    tenant_id=tenant_id,
                    plan_id=plan_id,
                    skill=self._skill,
                    input_summary=input_summary,
                    outcome=outcome,
                    key_decisions={"tool": tool_name, "args": str(tool_args)[:200]},
                    learnings=learnings_json,
                    duration_ms=duration_ms,
                )
            except Exception as exc:
                logger.debug("record_task_failed", extra={"error": str(exc)})

        # Build output
        customers_raw = tool_result.get("data", tool_result.get("customers", []))
        if not isinstance(customers_raw, list):
            customers_raw = []

        return {
            "status": "completed" if outcome == "success" else "failed",
            "output": {
                "skill": self._skill,
                "customers": customers_raw,
                "customer_id": customers_raw[0].get("customer_id") if customers_raw else None,
                "action": _skill_to_action(self._skill),
                "memory_hit": False,
                "message": _build_response_message(self._skill, customers_raw, tool_result),
            },
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _map_skill_to_tool(
    skill: str,
    params: dict[str, Any],
    message: str,
) -> tuple[str, dict]:
    """Map a Customer Agent skill to a Tool Registry tool name + args."""
    if skill == "lookup_customer":
        query = params.get("query") or params.get("name") or message
        limit = params.get("limit", 5)
        return "customer__get_customers", {"query": query, "limit": limit}
    elif skill == "create_customer":
        return "customer__create_customer", {
            "name": params.get("name", ""),
            "phone": params.get("phone", ""),
            "address": params.get("address", ""),
        }
    elif skill == "update_customer":
        args: dict = {"customer_id": params.get("customer_id", "")}
        for field in ("name", "phone", "address"):
            if params.get(field):
                args[field] = params[field]
        return "customer__update_customer", args
    return "", {}


def _skill_to_action(skill: str) -> str:
    return {"lookup_customer": "lookup", "create_customer": "create", "update_customer": "update"}.get(
        skill, "lookup"
    )


def _build_response_message(skill: str, customers: list, tool_result: dict) -> str:
    if skill == "lookup_customer":
        if not customers:
            return "Không tìm thấy khách hàng phù hợp."
        names = ", ".join(c.get("name", "") for c in customers[:3])
        return f"Tìm thấy {len(customers)} khách hàng: {names}."
    elif skill == "create_customer":
        name = tool_result.get("name", "")
        return f"Đã tạo thành công khách hàng {name}." if name else "Tạo khách hàng thành công."
    elif skill == "update_customer":
        return "Đã cập nhật thông tin khách hàng thành công."
    return ""


def _build_memory_context(
    semantic_mem: list[dict[str, Any]],
    similar_tasks: list[dict[str, Any]],
) -> str:
    """Render memories as a text block for system prompt injection."""
    blocks: list[str] = []

    if semantic_mem:
        blocks.append("## Kiến Thức Tích Lũy (Semantic Memory)")
        for m in semantic_mem:
            blocks.append(f"- [{m['memory_type']}] {m['key']}: {m['content']}")

    if similar_tasks:
        blocks.append("\n## Bài Học Từ Task Tương Tự (Episodic Memory)")
        for t in similar_tasks:
            blocks.append(f"- Thao tác tương tự: \"{str(t['input_summary'])[:80]}\"")
            if t.get("key_decisions"):
                kd = t["key_decisions"]
                if isinstance(kd, str):
                    try:
                        kd = json.loads(kd)
                    except Exception:
                        kd = {}
                alias = kd.get("alias", "")
                if alias:
                    blocks.append(f"  Alias đã dùng: {str(alias)[:80]}")
            if t.get("learnings"):
                blocks.append(f"  Bài học: {str(t['learnings'])[:100]}")

    if blocks:
        blocks.append("\n## Lưu Ý")
        blocks.append(
            "Dùng kiến thức trên để tra cứu nhanh hơn, nhưng LUÔN xác minh lại nếu dữ liệu có thể đã thay đổi."
        )

    return "\n".join(blocks)


async def _extract_learnings(message: str, tool_name: str, result: dict[str, Any]) -> str:
    """Call GPT-4o-mini to extract ≤ 3 useful contact facts from the task."""
    from shared.llm_client import chat_completion_async  # noqa: PLC0415

    customers = result.get("data", result.get("customers", []))
    user_content = (
        f"Thao tác: {tool_name}\n"
        f"Yêu cầu: {message[:200]}\n"
        f"Kết quả: {json.dumps(customers[:3], ensure_ascii=False)[:300]}"
    )

    content, _ = await chat_completion_async(
        messages=[
            {"role": "system", "content": _EXTRACT_LEARNINGS_PROMPT},
            {"role": "user", "content": user_content},
        ],
        task_type="extract_learnings",
        extra_log={"agent": "customer-agent"},
    )
    return content


async def _store_learnings(
    memory: "MemoryService",
    tenant_id: str,
    learnings_json: str,
) -> None:
    """Parse learnings JSON and upsert each fact. Silently swallows errors."""
    try:
        facts = json.loads(learnings_json)
        if not isinstance(facts, list):
            return
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            await memory.store(
                tenant_id=tenant_id,
                memory_type=fact.get("memory_type", "contact_alias"),
                key=str(fact.get("key", "")),
                content=str(fact.get("content", "")),
                confidence=float(fact.get("confidence", 0.8)),
            )
    except Exception:
        pass  # Best-effort — never blocks task completion


def _outcome_from_result(result: dict[str, Any]) -> str:
    if result.get("__hitl__"):
        return "hitl"
    if "error" in result:
        return "failed"
    return "success"
