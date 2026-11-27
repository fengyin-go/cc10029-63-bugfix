"""改进方案正式反馈引用校验与签收联动测试。

覆盖验收场景：
1. feedback:99999 等不存在的正式编号不能建出可审批的方案；
2. 反馈签收结果同步到方案审核待办（登记、提交审核、审核方案三处复核）；
3. 纯文字历史方案仍可登记查阅；
4. 正式编号属于另一位教师时不能拿该条记录代替；
5. F-12 签收中：资格查询能区分“待补签收资格”与“签收后从原申请继续建方案”；
6. 断网重发原请求（幂等键）沿用原单号、不另开可办事项。
"""
from __future__ import annotations

import pytest

from app.routers import improve_plan as plan_router
from app.routers.resource import create_entry as http_create_entry
from app.schemas import EntryPayload
from app.store import store
from app.services.feedback import FeedbackService
from app.services.improve_plan import ImprovePlanService


def clear_all() -> None:
    for name in store.module_names():
        store.rows(name).clear()
    store._idempotency.clear()

def make_feedback(teacher: str = "王老师", *, sign: bool | None = None) -> int:
    """创建反馈单并按需流转状态，返回反馈单ID。

    sign=None 保持草稿；sign=False 置为待签收；sign=True 完成签收。
    """
    fb_svc = FeedbackService()
    fb, _ = fb_svc.create_entry({
        "反馈对象": teacher,
        "反馈要点": "课堂互动不足",
        "改进建议": "增加提问与小组讨论",
        "接收教师": teacher,
    })
    if sign is False:
        fb["status"] = "待签收"
    elif sign is True:
        fb_svc.run_action(fb["id"], "签收反馈")
    return fb["id"]


class TestFormalFeedbackValidation:
    """正式反馈引用的建方案资格校验。"""

    def setup_method(self):
        clear_all()
        self.plan_svc = ImprovePlanService()

    def test_nonexistent_feedback_ref_rejected(self):
        """feedback:99999 查无出处：不能建出可审批的单子，审核列表无残留。"""
        with pytest.raises(ValueError, match="不存在"):
            self.plan_svc.create_entry({
                "关联反馈": "feedback:99999",
                "改进目标": "提升互动性",
                "改进措施": "每周一次课堂讨论",
            })
        # 关键：审核列表/方案表中不能残留这份方案
        assert store.rows("improve_plan") == []

    def test_nonexistent_feedback_ref_http_returns_business_failure(self):
        """HTTP 层返回 ok=False 而不是 500，且没有产生新单据。"""
        result = http_create_entry("improve_plan", EntryPayload(values={
            "关联反馈": "feedback:99999",
            "改进目标": "提升互动性",
            "改进措施": "每周一次课堂讨论",
        }))
        assert result.ok is False
        assert "不存在" in result.message
        assert result.entry is None
        assert store.rows("improve_plan") == []

    def test_unsigned_feedback_ref_rejected(self):
        """仍在签收中的同教师反馈不能建方案，避免与签收回执对不上。"""
        fb_id = make_feedback("王老师", sign=False)
        with pytest.raises(ValueError, match="签收"):
            self.plan_svc.create_entry({
                "关联反馈": f"feedback:{fb_id}",
                "改进目标": "提升互动性",
                "改进措施": "每周一次课堂讨论",
                "负责教师": "王老师",
            })
        assert store.rows("improve_plan") == []

    def test_draft_feedback_ref_rejected(self):
        """草稿状态的反馈同样不具备签收资格。"""
        fb_id = make_feedback("王老师")  # 草稿
        with pytest.raises(ValueError, match="签收"):
            self.plan_svc.create_entry({
                "关联反馈": f"feedback:{fb_id}",
                "改进目标": "提升互动性",
                "改进措施": "每周一次课堂讨论",
            })

    def test_signed_feedback_ref_accepted_and_marked_formal(self):
        """已签收反馈可建方案，并带正式引用标记便于追溯回执。"""
        fb_id = make_feedback("王老师", sign=True)
        plan, missing = self.plan_svc.create_entry({
            "关联反馈": f"feedback:{fb_id}",
            "改进目标": "提升互动性",
            "改进措施": "每周一次课堂讨论",
            "负责教师": "王老师",
        })
        assert missing == []
        assert plan is not None
        assert plan["正式引用"] is True
        assert plan["status"] == "草稿"

    def test_other_teacher_feedback_cannot_substitute(self):
        """正式编号属于另一位教师时，不能拿该条记录代替。"""
        fb_id = make_feedback("李老师", sign=True)
        with pytest.raises(ValueError, match="不一致"):
            self.plan_svc.create_entry({
                "关联反馈": f"feedback:{fb_id}",
                "改进目标": "提升互动性",
                "改进措施": "每周一次课堂讨论",
                "负责教师": "王老师",
            })
        assert store.rows("improve_plan") == []

    def test_plain_text_historical_plan_still_allowed(self):
        """纯文字登记的历史方案仍能登记和查阅，不做跨模块拦截。"""
        plan, missing = self.plan_svc.create_entry({
            "关联反馈": "上周听课口头反馈（无正式编号）",
            "改进目标": "提升互动性",
            "改进措施": "每周一次课堂讨论",
        })
        assert missing == []
        assert plan is not None
        assert plan.get("正式引用") is None
        # 列表仍可查阅
        rows, total = self.plan_svc.list_entries()
        assert total == 1
        assert rows[0]["id"] == plan["id"]

    def test_malformed_numeric_ref_not_treated_as_formal(self):
        """feedback:abc 这类非编号引用按纯文字历史登记处理，不冒充正式引用。"""
        plan, _ = self.plan_svc.create_entry({
            "关联反馈": "feedback:abc",
            "改进目标": "提升互动性",
            "改进措施": "每周一次课堂讨论",
        })
        assert plan is not None
        assert plan.get("正式引用") is None


class TestSignoffSyncedToReviewTodo:
    """反馈签收结果同步到方案审核待办。"""

    def setup_method(self):
        clear_all()
        self.fb_svc = FeedbackService()
        self.plan_svc = ImprovePlanService()

    def test_submit_rechecks_latest_signoff(self):
        """提交审核时按反馈最新签收结果复核。"""
        fb_id = make_feedback("王老师", sign=True)
        plan, _ = self.plan_svc.create_entry({
            "关联反馈": f"feedback:{fb_id}",
            "改进目标": "提升互动性",
            "改进措施": "每周一次课堂讨论",
            "负责教师": "王老师",
        })
        # 签收回执被订正撤销，签收资格失效（模拟数据纠偏）
        store.find("feedback", fb_id)["status"] = "待签收"

        result, msg = self.plan_svc.run_action(plan["id"], "提交审核")
        assert result is None
        assert "签收" in msg
        # 方案状态仍停留在草稿，未进入待审核待办
        assert store.find("improve_plan", plan["id"])["status"] == "草稿"

    def test_approve_rechecks_latest_signoff(self):
        """审核方案时审批判断与实际反馈签收保持一致。"""
        fb_id = make_feedback("王老师", sign=True)
        plan, _ = self.plan_svc.create_entry({
            "关联反馈": f"feedback:{fb_id}",
            "改进目标": "提升互动性",
            "改进措施": "每周一次课堂讨论",
            "负责教师": "王老师",
        })
        self.plan_svc.run_action(plan["id"], "提交审核")
        assert store.find("improve_plan", plan["id"])["status"] == "待审核"

        # 签收资格在审核期间被订正撤销，审核必须被拦下
        store.find("feedback", fb_id)["status"] = "待签收"
        result, msg = self.plan_svc.run_action(plan["id"], "审核方案")
        assert result is None
        assert "签收" in msg
        assert store.find("improve_plan", plan["id"])["status"] == "待审核"

    def test_full_flow_signoff_then_submit_then_approve(self):
        """签收完成后负责人可按本次签收资格提交并通过审核。"""
        fb_id = make_feedback("王老师")
        # 先尝试建方案：未签收，拒绝
        with pytest.raises(ValueError):
            self.plan_svc.create_entry({
                "关联反馈": f"feedback:{fb_id}",
                "改进目标": "提升互动性",
                "改进措施": "每周一次课堂讨论",
                "负责教师": "王老师",
            })
        # 完成签收后，从原申请继续建方案
        self.fb_svc.run_action(fb_id, "签收反馈")
        plan, _ = self.plan_svc.create_entry({
            "关联反馈": f"feedback:{fb_id}",
            "改进目标": "提升互动性",
            "改进措施": "每周一次课堂讨论",
            "负责教师": "王老师",
        })
        self.plan_svc.run_action(plan["id"], "提交审核")
        approved, _ = self.plan_svc.run_action(plan["id"], "审核方案")
        assert approved["status"] == "审核通过"

    def test_settled_feedback_also_eligible(self):
        """已办结的反馈同样具备方案资格。"""
        fb_id = make_feedback("王老师", sign=True)
        self.fb_svc.run_action(fb_id, "确认办结")
        plan, _ = self.plan_svc.create_entry({
            "关联反馈": f"feedback:{fb_id}",
            "改进目标": "提升互动性",
            "改进措施": "每周一次课堂讨论",
        })
        assert plan is not None


class TestF12SignoffConflict:
    """F-12 正在签收时的方案资格判断。"""

    def setup_method(self):
        clear_all()
        self.plan_svc = ImprovePlanService()

    def test_eligibility_for_signing_feedback(self):
        """签收中的同教师反馈：没有待补签收的办理资格，需签收后从原申请继续。"""
        fb_id = make_feedback("王老师", sign=False)
        info = self.plan_svc.feedback_plan_eligibility(
            f"feedback:{fb_id}", responsible_teacher="王老师"
        )
        assert info["formal"] is True
        assert info["eligible"] is False
        assert info["feedback_status"] == "待签收"
        assert "从原申请继续" in info["message"]

    def test_eligibility_for_nonexistent_feedback(self):
        """不存在的反馈：正式编号查不到，不能拿别的记录代替。"""
        info = self.plan_svc.feedback_plan_eligibility(
            "feedback:99999", responsible_teacher="王老师"
        )
        assert info["formal"] is True
        assert info["eligible"] is False
        assert info["feedback_status"] is None
        assert "查不到" in info["message"]

    def test_eligibility_for_other_teacher_feedback(self):
        """同教师校验：反馈属于另一位教师时资格不成立。"""
        fb_id = make_feedback("李老师", sign=True)
        info = self.plan_svc.feedback_plan_eligibility(
            f"feedback:{fb_id}", responsible_teacher="王老师"
        )
        assert info["eligible"] is False
        assert info["owner_teacher"] == "李老师"
        assert "不一致" in info["message"]

    def test_eligibility_for_signed_same_teacher_feedback(self):
        """已签收且教师一致：具备本次签收资格，可直接提交。"""
        fb_id = make_feedback("王老师", sign=True)
        info = self.plan_svc.feedback_plan_eligibility(
            f"feedback:{fb_id}", responsible_teacher="王老师"
        )
        assert info["eligible"] is True
        assert info["feedback_status"] == "已签收"

    def test_eligibility_for_plain_text(self):
        """纯文字引用：非正式引用，历史可查但无签收资格。"""
        info = self.plan_svc.feedback_plan_eligibility(
            "口头反馈记录", responsible_teacher="王老师"
        )
        assert info["formal"] is False
        assert info["eligible"] is False

    def test_eligibility_endpoint_http(self):
        """资格查询接口对两种核对对象（不存在/签收中）分别返回正确结论。"""
        signing_id = make_feedback("王老师", sign=False)

        body_missing = plan_router.feedback_eligibility(
            feedback_ref="feedback:99999", responsible_teacher="王老师"
        )
        assert body_missing["ok"] is True
        assert body_missing["formal"] is True
        assert body_missing["eligible"] is False

        body_signing = plan_router.feedback_eligibility(
            feedback_ref=f"feedback:{signing_id}", responsible_teacher="王老师"
        )
        assert body_signing["formal"] is True
        assert body_signing["eligible"] is False
        assert body_signing["feedback_status"] == "待签收"


class TestIdempotentCreate:
    """断网重发原请求：沿用原单号与原受理，不另开可办事项。"""

    def setup_method(self):
        clear_all()
        self.plan_svc = ImprovePlanService()

    def test_replay_same_request_key_returns_original_entry(self):
        fb_id = make_feedback("王老师", sign=True)
        payload = {
            "关联反馈": f"feedback:{fb_id}",
            "改进目标": "提升互动性",
            "改进措施": "每周一次课堂讨论",
            "负责教师": "王老师",
        }
        body1 = http_create_entry(
            "improve_plan", EntryPayload(values=payload, request_key="director-f12-req-001")
        )
        assert body1.ok is True
        original_id = body1.entry["id"]

        # 网络断开后再次发送原请求（同一 request_key、同一报文）
        body2 = http_create_entry(
            "improve_plan", EntryPayload(values=payload, request_key="director-f12-req-001")
        )
        assert body2.ok is True
        assert body2.entry["id"] == original_id
        assert "原单号" in body2.message

        # 审核列表只保留原事项，新回执没有另开可办事项
        rows = store.rows("improve_plan")
        assert len(rows) == 1
        assert rows[0]["id"] == original_id

        # 重新进入审核列表，原事项仍在
        listed, total = self.plan_svc.list_entries()
        assert total == 1
        assert listed[0]["id"] == original_id

    def test_replay_after_rejection_does_not_poison_idempotency(self):
        """校验失败的请求不写入幂等台账，纠正后可正常受理。"""
        bad = http_create_entry("improve_plan", EntryPayload(
            values={
                "关联反馈": "feedback:99999",
                "改进目标": "提升互动性",
                "改进措施": "每周一次课堂讨论",
            },
            request_key="req-bad-001",
        ))
        assert bad.ok is False
        assert store.rows("improve_plan") == []

        fb_id = make_feedback("王老师", sign=True)
        ok = http_create_entry("improve_plan", EntryPayload(
            values={
                "关联反馈": f"feedback:{fb_id}",
                "改进目标": "提升互动性",
                "改进措施": "每周一次课堂讨论",
            },
            request_key="req-bad-001",
        ))
        assert ok.ok is True
        assert len(store.rows("improve_plan")) == 1

    def test_different_request_keys_create_separate_entries(self):
        """不同请求键代表不同申请，各自独立建单。"""
        fb_id = make_feedback("王老师", sign=True)
        payload = {
            "关联反馈": f"feedback:{fb_id}",
            "改进目标": "提升互动性",
            "改进措施": "每周一次课堂讨论",
        }
        r1 = http_create_entry("improve_plan", EntryPayload(values=payload, request_key="k-1"))
        r2 = http_create_entry("improve_plan", EntryPayload(values=payload, request_key="k-2"))
        assert r1.entry["id"] != r2.entry["id"]
        assert len(store.rows("improve_plan")) == 2

    def test_no_request_key_keeps_original_behavior(self):
        """不带幂等键的请求维持原有登记行为。"""
        fb_id = make_feedback("王老师", sign=True)
        payload = {
            "关联反馈": f"feedback:{fb_id}",
            "改进目标": "提升互动性",
            "改进措施": "每周一次课堂讨论",
        }
        r1 = http_create_entry("improve_plan", EntryPayload(values=payload))
        r2 = http_create_entry("improve_plan", EntryPayload(values=payload))
        assert r1.ok is True and r2.ok is True
        assert len(store.rows("improve_plan")) == 2
