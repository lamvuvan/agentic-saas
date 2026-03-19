"""Date/time helper tool for business date operations."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from src.tools.base import BaseTool

logger = logging.getLogger(__name__)


class _DatetimeInput(BaseModel):
    operation: str = Field(
        description=(
            "Operation to perform: "
            "'now' (current datetime), "
            "'range' (date range for a period), "
            "'diff' (days between two dates), "
            "'add' (add/subtract days from a date), "
            "'format' (reformat a date string), "
            "'weekday' (get weekday name for a date)"
        )
    )
    period: Optional[str] = Field(
        default=None,
        description=(
            "Named period for 'range' operation: "
            "today, yesterday, this_week, last_week, "
            "this_month, last_month, this_quarter, last_quarter, "
            "this_year, last_year"
        ),
    )
    date: Optional[str] = Field(default=None, description="Date in YYYY-MM-DD format (for add, format, weekday)")
    date1: Optional[str] = Field(default=None, description="First date YYYY-MM-DD (for diff)")
    date2: Optional[str] = Field(default=None, description="Second date YYYY-MM-DD (for diff)")
    days: Optional[int] = Field(default=None, description="Number of days to add (negative = subtract, for add)")
    fmt: Optional[str] = Field(default=None, description="strftime pattern, e.g. '%d/%m/%Y' (for format)")


class DatetimeHelperTool(BaseTool):
    """Perform date/time operations for business use cases."""

    name: str = "datetime_helper"
    description: str = (
        "Perform date/time operations. "
        "Use 'now' to get current datetime. "
        "Use 'range' with a period name (this_month, last_month, this_quarter, etc.) to get start/end dates. "
        "Use 'diff' with date1 and date2 to count days between them. "
        "Use 'add' with date and days to compute a future/past date. "
        "Use 'format' with date and fmt to reformat. "
        "Use 'weekday' with date to get the day name."
    )
    args_schema: type[BaseModel] = _DatetimeInput

    timezone: str = "Asia/Ho_Chi_Minh"

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("DatetimeHelperTool is async-only; use _arun.")

    async def _arun(
        self,
        operation: str,
        period: Optional[str] = None,
        date: Optional[str] = None,
        date1: Optional[str] = None,
        date2: Optional[str] = None,
        days: Optional[int] = None,
        fmt: Optional[str] = None,
        **_: object,
    ) -> str:
        tz = ZoneInfo(self.timezone)
        now = datetime.now(tz)
        today = now.date()
        op = operation.lower()

        try:
            if op == "now":
                return json.dumps({
                    "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "date": str(today),
                    "time": now.strftime("%H:%M:%S"),
                    "weekday": now.strftime("%A"),
                    "timezone": self.timezone,
                })

            elif op == "range":
                p = (period or "this_month").lower()
                start, end = _get_period_range(today, p)
                return json.dumps({
                    "period": p,
                    "start": str(start),
                    "end": str(end),
                    "days": (end - start).days + 1,
                })

            elif op == "diff":
                if not date1 or not date2:
                    return "Error: 'diff' requires date1 and date2."
                d1 = _parse_date(date1)
                d2 = _parse_date(date2)
                diff = (d2 - d1).days
                return json.dumps({
                    "date1": str(d1), "date2": str(d2),
                    "days": diff,
                    "description": f"{abs(diff)} days {'after' if diff >= 0 else 'before'}",
                })

            elif op == "add":
                if days is None:
                    return "Error: 'add' requires 'days'."
                base = _parse_date(date) if date else today
                result = base + timedelta(days=days)
                return json.dumps({"result": str(result), "weekday": result.strftime("%A")})

            elif op == "format":
                d = datetime.fromisoformat(date or str(today))
                return d.strftime(fmt or "%d/%m/%Y")

            elif op == "weekday":
                d = _parse_date(date) if date else today
                return json.dumps({"date": str(d), "weekday": d.strftime("%A"), "weekday_number": d.isoweekday()})

            else:
                return f"Unknown operation: {op!r}. Supported: now, range, diff, add, format, weekday."

        except ValueError as exc:
            return f"Invalid value: {exc}"
        except Exception as exc:
            return f"Error: {exc}"


def _get_period_range(today: date, period: str) -> tuple[date, date]:
    year, month = today.year, today.month

    if period == "today":
        return today, today
    elif period == "yesterday":
        d = today - timedelta(days=1)
        return d, d
    elif period == "this_week":
        start = today - timedelta(days=today.weekday())  # Monday
        return start, start + timedelta(days=6)
    elif period == "last_week":
        start = today - timedelta(days=today.weekday() + 7)
        return start, start + timedelta(days=6)
    elif period == "this_month":
        start = date(year, month, 1)
        end = _month_end(year, month)
        return start, end
    elif period == "last_month":
        if month == 1:
            year, month = year - 1, 12
        else:
            month -= 1
        return date(year, month, 1), _month_end(year, month)
    elif period == "this_quarter":
        q_start_month = ((month - 1) // 3) * 3 + 1
        start = date(year, q_start_month, 1)
        end_month = q_start_month + 2
        return start, _month_end(year, end_month)
    elif period == "last_quarter":
        q = (month - 1) // 3  # 0-based current quarter
        if q == 0:
            year -= 1
            q = 3
        else:
            q -= 1
        q_start_month = q * 3 + 1
        start = date(year, q_start_month, 1)
        end_month = q_start_month + 2
        return start, _month_end(year, end_month)
    elif period == "this_year":
        return date(year, 1, 1), date(year, 12, 31)
    elif period == "last_year":
        return date(year - 1, 1, 1), date(year - 1, 12, 31)
    else:
        raise ValueError(f"Unknown period: {period!r}")


def _month_end(year: int, month: int) -> date:
    if month == 12:
        return date(year + 1, 1, 1) - timedelta(days=1)
    return date(year, month + 1, 1) - timedelta(days=1)
