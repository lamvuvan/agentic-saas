"""Memory-aware wrapper for the BI Agent task execution.

MemoryAwareReActLoop (BI variant):
    1. Retrieve semantic memories + similar past tasks from MemoryService
    2. Build memory context string injected into params._memory_context
    3. Invoke run_bi_graph with the augmented params
    4. Extract ≤ 3 facts via GPT-4o-mini (best-effort — never blocks task)
    5. Record task history (best-effort)

Memory types stored:
    sql_pattern     — "doanh thu theo ngày" → correct SQL template
    glossary_fix    — "doanh thu" = net_revenue column
    column_alias    — business alias → column name mapping
    query_template  — reusable query structure for common intents
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
# HITL prompts (BI variant — same logic as order_agent, future-proofing for BI write tools)
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

# Module-level tools config cache
_TOOLS_CONFIG_CACHE: dict | None = None

# ---------------------------------------------------------------------------
# Learning extraction prompt
# ---------------------------------------------------------------------------

_EXTRACT_LEARNINGS_PROMPT = """
Dựa trên câu hỏi BI và SQL vừa thực hiện, extract tối đa 3 facts hữu ích cho tương lai.
Output JSON array, mỗi fact gồm: {"memory_type", "key", "content", "confidence"}.

Các loại fact hữu ích (bi-agent):
- sql_pattern:    "doanh thu theo ngày" → SQL template đúng         (key: query_intent)
- glossary_fix:   "doanh thu" = cột net_revenue trong bảng orders   (key: business_term)
- column_alias:   "khách mới" → WHERE created_at >= ...             (key: alias_phrase)
- query_template: cấu trúc SQL tái sử dụng cho loại query này       (key: query_type)

QUAN TRỌNG: Chỉ lưu pattern kỹ thuật, KHÔNG lưu dữ liệu kinh doanh cụ thể (doanh thu ngày X, tên khách).
Nếu không có fact nào đáng lưu → trả về [].
""".strip()


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
        extra_log={"agent": "bi-agent", "tool": tool_name},
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
            extra_log={"agent": "bi-agent", "tool": tool_name},
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
    """Assemble the BI Agent system prompt from A2ATaskPayload + memory context.

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


class MemoryAwareReActLoop:
    """Wraps run_bi_graph with memory retrieval, injection, and learning extraction."""

    def __init__(self, skill: str = "bi_query") -> None:
        self._skill = skill

    async def _execute_tool(
        self,
        tool_name: str,
        args: dict,
        task_id: str,
        redis: "aioredis.Redis",
        tool_registry_url: str = "",
        tools_config: dict | None = None,
    ) -> dict:
        """Execute a tool with HITL gate check (BI variant — future-proofing for BI write tools).

        Returns ``{"__hitl__": True, ...}`` if the tool requires confirmation,
        otherwise executes directly.
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
        """Handle user response after a HITL pause (BI variant)."""
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
        """Execute BI Agent pipeline with memory augmentation.

        Args:
            payload: Optional A2ATaskPayload — if provided, build_agent_system_prompt()
                     injects instructions, original_message, and dependency_results.

        Returns same dict as run_bi_graph.
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

        # ── Step 2: Inject memory context + payload system prompt into params ──────
        memory_context = _build_memory_context(semantic_mem, similar_tasks)
        augmented_params = {
            **params,
            "_memory_context": memory_context,
            "_system_prompt": build_agent_system_prompt(
                base_prompt=params.get("_base_system_prompt", ""),
                payload=payload,
                memory_context=memory_context,
            ),
        }

        # ── Step 3: Run the BI Agent pipeline ─────────────────────────────
        from bi_agent.graph import run_bi_graph  # noqa: PLC0415

        result = await run_bi_graph(task_id=task_id, params=augmented_params, redis=redis)

        duration_ms = int((time.monotonic() - t0) * 1000)
        outcome = _outcome_from_result(result)

        # ── Steps 4–5: Extract learnings + store (best-effort) ────────────
        learnings_json = ""
        if memory is not None and outcome == "success":
            try:
                learnings_json = await _extract_learnings(message, result)
                await _store_learnings(memory, tenant_id, learnings_json)
            except Exception as exc:
                logger.debug("learning_extraction_failed", extra={"error": str(exc)})

        # ── Step 6: Record task history (best-effort) ─────────────────────
        if memory is not None:
            input_summary = f"bi_query: {message[:180]}"[:200]
            try:
                await memory.record_task(
                    tenant_id=tenant_id,
                    plan_id=plan_id,
                    skill=self._skill,
                    input_summary=input_summary,
                    outcome=outcome,
                    key_decisions={"sql": result.get("output", {}).get("generated_sql", "")[:200]},
                    learnings=learnings_json,
                    duration_ms=duration_ms,
                )
            except Exception as exc:
                logger.debug("record_task_failed", extra={"error": str(exc)})

        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
            blocks.append(f"- Query tương tự: \"{str(t['input_summary'])[:80]}\"")
            if t.get("key_decisions"):
                kd = t["key_decisions"]
                if isinstance(kd, str):
                    try:
                        kd = json.loads(kd)
                    except Exception:
                        kd = {}
                sql = kd.get("sql", "")
                if sql:
                    blocks.append(f"  SQL đã dùng: {str(sql)[:120]}")
            if t.get("learnings"):
                blocks.append(f"  Bài học: {str(t['learnings'])[:100]}")

    if blocks:
        blocks.append("\n## Lưu Ý")
        blocks.append(
            "Dùng kiến thức trên để viết SQL chính xác hơn, nhưng LUÔN kiểm tra lại với schema hiện tại."
        )

    return "\n".join(blocks)


async def _extract_learnings(message: str, result: dict[str, Any]) -> str:
    """Call GPT-4o-mini to extract ≤ 3 useful BI facts from the task."""
    from shared.llm_client import chat_completion_async  # noqa: PLC0415

    output = result.get("output", {})
    sql = output.get("generated_sql", "") if isinstance(output, dict) else ""
    user_content = (
        f"Câu hỏi: {message[:200]}\n"
        f"SQL thực thi: {sql[:300]}\n"
        f"Kết quả: {str(output.get('message', ''))[:200]}"
    )

    content, _ = await chat_completion_async(
        messages=[{"role": "user", "content": user_content}],
        task_type="extract_learnings",
        extra_log={"agent": "bi-agent"},
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
                memory_type=fact.get("memory_type", "sql_pattern"),
                key=str(fact.get("key", "")),
                content=str(fact.get("content", "")),
                confidence=float(fact.get("confidence", 0.8)),
            )
    except Exception:
        pass  # Best-effort — never blocks task completion


def _outcome_from_result(result: dict[str, Any]) -> str:
    output = result.get("output", {})
    status = output.get("status", "") if isinstance(output, dict) else ""
    if status == "success":
        return "success"
    if status in ("rejected", "error"):
        return "failed"
    return "success"
