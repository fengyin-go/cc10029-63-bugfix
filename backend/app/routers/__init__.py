"""业务路由汇总：专用接口在前，通用资源路由兜底，均由领域服务完成分派。"""
from __future__ import annotations

from app.routers import improve_plan, resource

ROUTERS = [improve_plan, resource]
