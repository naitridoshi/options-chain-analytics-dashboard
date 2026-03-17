from libs.utils.common.runtime_store.src import RuntimeDashboardService


class OptionChainDashboardService:
    @classmethod
    async def get_dashboard_data(
        cls,
        *,
        symbol: str | None = None,
        timeline_limit: int = 100,
    ) -> dict:
        return await RuntimeDashboardService.get_dashboard_data(
            symbol=symbol,
            timeline_limit=timeline_limit,
        )
