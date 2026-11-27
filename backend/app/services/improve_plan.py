"""改进方案领域服务：方案登记、审核、驳回，含跨模块关联反馈校验。

正式引用必须写成 ``feedback:<反馈单ID>``：
- 反馈单必须存在，且已完成签收（已签收/已办结），否则拒绝登记，
  避免审核列表残留没有签收回执出处的方案；
- 方案负责教师必须与反馈单归属教师一致，不能拿其他教师的反馈代替；
- 纯文字登记的历史方案不做跨模块拦截，仍可登记和查阅，但不作为
  正式签收凭证。

提交审核、审核方案两个环节会再次按反馈单当前签收结果复核，
让反馈签收状态同步到方案审核待办。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.cross_module import (
    check_plan_feedback_eligibility,
    feedback_owner_teacher,
    find_by_module_and_id,
    parse_feedback_ref,
)

# 进入审核流转前必须复核反馈签收资格的动作
REVIEW_ACTIONS = ("提交审核", "审核方案")


class ImprovePlanService(BaseModuleService):
    MODULE = "improve_plan"
    ENTITY = "改进方案"
    OPERATION_TERM = "审核方案"
    LIST_FIELDS = ["关联反馈", "改进目标", "改进措施", "预期产出", "负责教师", "启动日期", "完成期限", "备注说明"]
    REQUIRED_FIELDS = ["关联反馈", "改进目标", "改进措施"]
    STATUS_ORDER = ["草稿", "待审核", "审核通过", "已驳回"]
    ACTION_RULES = {"审核方案": "审核通过", "提交审核": "待审核", "驳回方案": "已驳回"}
    NEGATIVE_ACTIONS = ["驳回方案"]

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """创建改进方案：正式引用的反馈单必须已签收且归属教师一致。"""
        feedback_ref = str(values.get("关联反馈", "") or "").strip()
        responsible_teacher = str(values.get("负责教师", "") or "").strip() or None

        # 正式 feedback:ID 引用做完整资格核对；纯文字历史登记放行
        check_plan_feedback_eligibility(
            feedback_ref,
            responsible_teacher=responsible_teacher,
            context="改进方案的关联反馈",
        )

        entry, missing = super().create_entry(values)
        if entry is not None and parse_feedback_ref(feedback_ref) is not None:
            # 标记为正式反馈引用，审核列表可据此追溯签收回执
            entry["正式引用"] = True
        return entry, missing

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """执行动作，含业务约束与反馈签收结果同步复核。"""
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"

        # 审核方案：必须是待审核状态
        if action == "审核方案":
            if entry.get("status") not in ("待审核", "草稿"):
                return None, f"{self.ENTITY} {entry_id} 当前状态为'{entry.get('status')}'，不可审核"

        # 提交审核：只有草稿和已驳回可以提交
        if action == "提交审核":
            if entry.get("status") not in ("草稿", "已驳回"):
                return None, f"{self.ENTITY} {entry_id} 当前状态为'{entry.get('status')}'，不可提交审核"

        # 驳回方案：必须是待审核状态
        if action == "驳回方案":
            if entry.get("status") not in ("待审核", "草稿"):
                return None, f"{self.ENTITY} {entry_id} 当前状态为'{entry.get('status')}'，不可驳回"

        # 进入审核流转前按反馈单最新签收结果复核，保证审批判断与实际反馈一致
        if action in REVIEW_ACTIONS and parse_feedback_ref(entry.get("关联反馈")) is not None:
            try:
                check_plan_feedback_eligibility(
                    entry.get("关联反馈"),
                    responsible_teacher=str(entry.get("负责教师", "") or "").strip() or None,
                    context=f"{self.ENTITY} {entry_id} 的关联反馈",
                )
            except ValueError as exc:
                return None, str(exc)

        return super().run_action(entry_id, action)

    def feedback_plan_eligibility(
        self, feedback_ref: str, responsible_teacher: str | None = None
    ) -> dict[str, Any]:
        """供前端/接口查询某条反馈引用当前是否具备建方案、办审核的资格。

        纯文字引用返回 formal=False；正式引用返回 formal=True，
        并带反馈单当前状态与教师归属，eligible 表示能否据此提交方案。
        """
        fb_id = parse_feedback_ref(feedback_ref)
        if fb_id is None:
            return {
                "formal": False,
                "eligible": False,
                "message": "该关联反馈为纯文字历史登记，可查阅历史方案，但不具备正式签收凭证，不能代替正式反馈引用",
            }

        feedback_row = find_by_module_and_id("feedback", fb_id)
        if feedback_row is None:
            return {
                "formal": True,
                "eligible": False,
                "feedback_id": fb_id,
                "feedback_status": None,
                "message": f"正式编号 feedback:{fb_id} 在反馈模块中查不到，不能拿其他记录代替，请核对反馈单号",
            }

        teacher = str(responsible_teacher or "").strip() or None
        owner = feedback_owner_teacher(feedback_row)
        status = str(feedback_row.get("status", "") or "")
        teacher_ok = True
        if teacher and owner and teacher != owner:
            teacher_ok = False
        status_ok = status in ("已签收", "已办结")
        eligible = status_ok and teacher_ok

        if eligible:
            message = f"反馈单 #{fb_id} 状态为'{status}'，具备本次签收资格，可登记并提交改进方案"
        elif not status_ok:
            message = (
                f"反馈单 #{fb_id} 当前状态为'{status}'，尚在签收流程中，"
                "本次方案没有待补签收的办理资格，请在签收完成后从原申请继续建方案"
            )
        else:
            message = (
                f"反馈单 #{fb_id} 归属教师为'{owner}'，与负责教师'{teacher}'不一致，"
                "不能用其他教师的反馈单代替"
            )

        return {
            "formal": True,
            "eligible": eligible,
            "feedback_id": fb_id,
            "feedback_status": status,
            "owner_teacher": owner,
            "responsible_teacher": teacher,
            "message": message,
        }
