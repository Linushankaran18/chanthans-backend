from fastapi import APIRouter, Depends

from app.dependencies.auth import require_admin
from app.dependencies.booking import get_dashboard_service
from app.schemas.dashboard import DashboardSummary
from app.services.dashboard_service import DashboardService

router = APIRouter(
    prefix="/admin/dashboard",
    tags=["admin: dashboard"],
    dependencies=[Depends(require_admin)],
)


@router.get("/summary", response_model=DashboardSummary)
async def summary(service: DashboardService = Depends(get_dashboard_service)) -> DashboardSummary:
    return await service.summary()
