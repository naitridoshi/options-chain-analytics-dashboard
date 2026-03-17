from libs.utils.db.redis.src import RedisWebSocketTicketStore


class RuntimeWebSocketTicketService:
    @staticmethod
    async def create_ticket(*, subject: str, symbol: str) -> str:
        return await RedisWebSocketTicketStore.create_ticket(
            subject=subject,
            symbol=symbol,
        )

    @staticmethod
    async def consume_ticket(ticket: str):
        return await RedisWebSocketTicketStore.consume_ticket(ticket)
