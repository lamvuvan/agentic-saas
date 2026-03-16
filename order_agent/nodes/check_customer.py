"""Check customer node — looks up customer via Tool Registry."""

from __future__ import annotations

import logging

from shared.tool_registry_client import ToolRegistryClient, ToolRegistryError

logger = logging.getLogger(__name__)


async def check_customer(
    customer_name: str | None,
    tool_registry_url: str,
    token: str = "",
) -> tuple[str | None, str | None, str | None]:
    """
    Look up customer by name in Tool Registry.

    Returns:
        (customer_id, resolved_name, input_request)
        - input_request is set when user input is needed (not found, multiple matches)
        - customer_id is None when not resolved
    """
    if not customer_name:
        return None, None, None

    client = ToolRegistryClient(base_url=tool_registry_url)
    try:
        result = await client.execute(
            "customer__get_customers",
            {"search": customer_name, "limit": 5},
        )
        customers = result if isinstance(result, list) else result.get("data", [])

        if not customers:
            return (
                None,
                customer_name,
                f"Không tìm thấy khách hàng '{customer_name}'. Bạn có muốn tạo mới không? (có/không)",
            )

        if len(customers) == 1:
            c = customers[0]
            return str(c.get("id", "")), c.get("name", customer_name), None

        # Multiple matches — ask user to confirm
        options = "\n".join(
            f"{i+1}. {c.get('name', '?')} (ID: {c.get('id', '?')})"
            for i, c in enumerate(customers[:5])
        )
        return (
            None,
            customer_name,
            f"Tìm thấy nhiều khách hàng:\n{options}\nVui lòng chọn số (1-{len(customers[:5])})",
        )

    except ToolRegistryError as exc:
        logger.warning(
            "check_customer_tool_error",
            extra={"customer": customer_name, "error": str(exc)},
        )
        # Proceed without customer rather than blocking order
        return None, customer_name, None
