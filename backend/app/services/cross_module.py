"""跨模块查询和校验辅助函数。

为每个业务模块提供跨模块的数据完整性验证、状态联动、
幂等性和冲突检测能力。
"""
from __future__ import annotations

import re
from typing import Any

from app.store import store

# 正式反馈引用：feedback:<数字ID>，例如 feedback:12
FEEDBACK_REF_PATTERN = re.compile(r"^feedback:(\d+)$")

# 反馈签收后（含办结）才允许据此登记、审核改进方案
PLAN_ELIGIBLE_FEEDBACK_STATUSES = ["已签收", "已办结"]


def parse_feedback_ref(feedback_ref: str | None) -> int | None:
    """解析正式反馈引用 feedback:<数字ID>。

    纯文字登记、空值或格式不符时返回 None，交由调用方按历史登记处理。
    """
    if feedback_ref is None:
        return None
    match = FEEDBACK_REF_PATTERN.fullmatch(str(feedback_ref).strip())
    if match is None:
        return None
    return int(match.group(1))


def feedback_owner_teacher(entry: dict[str, Any]) -> str:
    """反馈单的归属教师：优先取反馈对象，缺失时回退到接收教师。"""
    return (
        str(entry.get("反馈对象", "") or "").strip()
        or str(entry.get("接收教师", "") or "").strip()
    )


def find_by_module_and_id(module: str, entry_id: int) -> dict[str, Any] | None:
    """通用的跨模块实体查找。"""
    return store.find(module, entry_id)


def require_entity(module: str, entry_id: int, context: str) -> dict[str, Any]:
    """查找并验证实体存在，否则抛出带模块上下文的异常。"""
    entry = store.find(module, entry_id)
    if entry is None:
        raise ValueError(f"跨模块校验失败：{context}引用的{module} #{entry_id} 不存在")
    return entry


def find_by_field(
    module: str, field: str, value: str | None
) -> list[dict[str, Any]]:
    """按字段值在指定模块中搜索所有匹配记录。"""
    if value is None:
        return []
    rows = store.rows(module)
    return [
        row for row in rows
        if str(row.get(field, "")).casefold() == value.casefold()
    ]


def check_entity_status(
    module: str, entry_id: int, allowed_statuses: list[str], context: str
) -> dict[str, Any]:
    """验证实体状态在允许范围内。"""
    entry = require_entity(module, entry_id, context)
    current = entry.get("status", "")
    if current not in allowed_statuses:
        raise ValueError(
            f"跨模块校验失败：{context}要求{module} #{entry_id} 状态为{'/'.join(allowed_statuses)}，"
            f"当前状态为'{current}'"
        )
    return entry


def has_any_entry_in_status(module: str, statuses: list[str]) -> bool:
    """检查模块中是否有任何记录处于指定状态。"""
    return any(
        row.get("status") in statuses
        for row in store.rows(module)
    )


def count_by_field_value(module: str, field: str, value: str) -> int:
    """统计模块中指定字段匹配的记录数。"""
    return sum(
        1 for row in store.rows(module)
        if str(row.get(field, "")).casefold() == value.casefold()
    )


def module_snapshot(module: str) -> dict[str, Any]:
    """获取模块的数据快照。"""
    rows = store.rows(module)
    return {
        "module": module,
        "count": len(rows),
        "pending": sum(1 for r in rows if r.get("pending")),
        "abnormal": sum(1 for r in rows if r.get("abnormal")),
    }


def check_plan_feedback_eligibility(
    feedback_ref: str | None,
    *,
    responsible_teacher: str | None = None,
    context: str = "改进方案的关联反馈",
) -> dict[str, Any]:
    """核对正式反馈引用是否具备登记/审核改进方案的资格。

    规则：
    - 纯文字（非 feedback:ID）登记不做跨模块校验，历史方案仍可登记查阅；
    - 正式编号在反馈模块查不到时拒绝，不允许拿别的记录代替；
    - 反馈尚未签收（草稿/待签收等）时拒绝，避免与签收回执对不上；
    - 提供负责教师时，必须与反馈单归属教师（反馈对象，缺失时看接收教师）
      一致，属于另一位教师的反馈不能拿来建方案。

    校验通过时返回反馈单实体，便于调用方同步签收结果。
    """
    fb_id = parse_feedback_ref(feedback_ref)
    if fb_id is None:
        # 纯文字历史登记：保留查阅与登记能力，不视为正式签收凭证
        return {}

    feedback = require_entity("feedback", fb_id, context)

    current = str(feedback.get("status", "") or "")
    if current not in PLAN_ELIGIBLE_FEEDBACK_STATUSES:
        raise ValueError(
            f"跨模块校验失败：{context}引用的反馈单 #{fb_id} 当前状态为'{current}'，"
            f"须为{'/'.join(PLAN_ELIGIBLE_FEEDBACK_STATUSES)}（已完成签收）才能办理改进方案，"
            "请先完成反馈签收，再从原申请继续登记方案"
        )

    teacher = str(responsible_teacher or "").strip()
    owner = feedback_owner_teacher(feedback)
    if teacher and owner and teacher != owner:
        raise ValueError(
            f"跨模块校验失败：{context}引用的反馈单 #{fb_id} 归属教师为'{owner}'，"
            f"与方案负责教师'{teacher}'不一致，不能用其他教师的反馈单代替"
        )

    return feedback