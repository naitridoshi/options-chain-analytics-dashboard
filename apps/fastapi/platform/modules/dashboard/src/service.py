from libs.utils.common.fyers_client.src import FyersClientService
from libs.utils.db.postgres.operations.src import OptionChainDashboardOperations


class OptionChainDashboardService:
    @classmethod
    async def get_dashboard_data(
        cls,
        *,
        symbol: str | None = None,
        timeline_limit: int = 100,
    ) -> dict:
        dashboard_data = await OptionChainDashboardOperations.get_dashboard_data(
            symbol=symbol,
            timeline_limit=timeline_limit,
        )
        token_status = await FyersClientService.get_today_token_status()
        dashboard_data["fyers_status"] = token_status
        return dashboard_data
