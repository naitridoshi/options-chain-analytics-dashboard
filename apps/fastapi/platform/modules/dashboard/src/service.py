from libs.utils.common.runtime_store.src import RuntimeDashboardService
from libs.utils.db.postgres.operations.src import OptionChainDashboardOperations


class OptionChainDashboardService:
    @classmethod
    async def get_dashboard_data(
        cls,
        *,
        symbol: str | None = None,
        timeline_limit: int = 100,
    ) -> dict:
        runtime_payload = await RuntimeDashboardService.get_dashboard_data(
            symbol=symbol,
            timeline_limit=timeline_limit,
        )
        if runtime_payload:
            return runtime_payload

        return await OptionChainDashboardOperations.get_dashboard_data(
            symbol=symbol,
            timeline_limit=timeline_limit,
        )
