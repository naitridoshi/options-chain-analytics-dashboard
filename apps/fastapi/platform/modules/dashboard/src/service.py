from libs.utils.db.postgres.operations.src import OptionChainDashboardOperations


class OptionChainDashboardService:
    @classmethod
    async def get_dashboard_data(
        cls,
        *,
        symbol: str | None = None,
        timeline_limit: int = 100,
    ) -> dict:
        return await OptionChainDashboardOperations.get_dashboard_data(
            symbol=symbol,
            timeline_limit=timeline_limit,
        )
