"""改进方案专用接口：反馈签收资格查询。

通用登记/动作仍走 resource 路由；这里只放改进方案特有的资格核对，
供主任在反馈签收与方案登记冲突时判断本次方案是否已有办理资格。
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.services.improve_plan import ImprovePlanService

router = APIRouter(prefix="/api/improve_plan", tags=["改进方案"])


@router.get("/feedback-eligibility")
def feedback_eligibility(
    feedback_ref: str = Query(..., description="关联反馈引用，如 feedback:12 或纯文字"),
    responsible_teacher: str | None = Query(default=None, description="方案负责教师（可选）"),
) -> dict[str, object]:
    """核对反馈引用当前能否支撑方案登记与审核。

    - 纯文字引用：formal=False，历史方案仍可查阅但无正式签收资格；
    - 正式编号查不到：formal=True、eligible=False，不能拿别的记录代替；
    - 反馈仍在签收中：eligible=False，提示签收完成后从原申请继续建方案；
    - 教师不符：eligible=False；
    - 已签收且教师一致：eligible=True，可按本次签收资格提交方案。
    """
    service = ImprovePlanService()
    result = service.feedback_plan_eligibility(
        feedback_ref.strip(),
        responsible_teacher.strip() if responsible_teacher else None,
    )
    return {"ok": True, **result}
