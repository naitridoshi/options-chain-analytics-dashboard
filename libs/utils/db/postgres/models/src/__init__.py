# Import Base so Alembic can detect all models via Base.metadata
from libs.utils.db.postgres.models.src.base import Base  # noqa: F401
from libs.utils.db.postgres.models.src.expiry import Expiry  # noqa: F401
from libs.utils.db.postgres.models.src.fyers_token import (  # noqa: F401
    FyersToken,
)

# Import all models to register them with Base.metadata
from libs.utils.db.postgres.models.src.instrument import (  # noqa: F401
    Instrument,
)
from libs.utils.db.postgres.models.src.option_chain_snapshot import (  # noqa: F401
    OptionChainSnapshot,
)
from libs.utils.db.postgres.models.src.option_chain_strike import (  # noqa: F401
    OptionChainStrike,
)
from libs.utils.db.postgres.models.src.option_contract import (  # noqa: F401
    OptionContract,
)

__all__ = [
    "Base",
    "Instrument",
    "Expiry",
    "OptionContract",
    "OptionChainSnapshot",
    "OptionChainStrike",
    "FyersToken",
]
