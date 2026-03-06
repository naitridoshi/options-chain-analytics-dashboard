from dataclasses import dataclass


@dataclass
class FyersOptionChainRequest:
    symbol: str
    strikecount: int
    timestamp: int | None = None
