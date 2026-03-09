"""Scout package — combines all scout sub-routers into a single router."""
from fastapi import APIRouter

from .action_queue import router as action_queue_router
from .briefing import router as briefing_router
from .destinations import router as destinations_router
from .good_price import router as good_price_router
from .insights import router as insights_router
from .market_context import router as market_context_router
from .price_baseline import router as price_baseline_router
from .signal_health import router as signal_health_router
from .verdict import router as verdict_router

router = APIRouter(prefix="/api/scout", tags=["scout"])

router.include_router(verdict_router)
router.include_router(destinations_router)
router.include_router(signal_health_router)
router.include_router(price_baseline_router)
router.include_router(action_queue_router)
router.include_router(market_context_router)
router.include_router(good_price_router)
router.include_router(insights_router)
router.include_router(briefing_router)
