from datetime import datetime
from typing import Any, Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field

# Type variable for generic response data
T = TypeVar("T")


# Custom JSON encoders for datetime fields
def datetime_encoder(dt: datetime) -> str | None:
    """
    Serialize datetime to ISO 8601 format with 'Z' suffix for UTC.

    Args:
        dt: Datetime object to serialize

    Returns:
        ISO 8601 string with 'Z' suffix if UTC, otherwise with timezone offset
    """
    if dt is None:
        return None

    # Convert to UTC if not already
    if dt.tzinfo is None:
        # Naive datetime - treat as UTC
        return dt.isoformat() + "Z"
    elif dt.utcoffset().total_seconds() == 0:
        # UTC timezone - use 'Z' suffix
        return dt.replace(tzinfo=None).isoformat() + "Z"
    else:
        # Other timezone - keep offset
        return dt.isoformat()


# Base ConfigDict for all DTOs
base_config = ConfigDict(
    populate_by_name=True,
    json_encoders={datetime: datetime_encoder},
)


class ErrorDetailDTO(BaseModel):
    """Error detail structure."""

    model_config = base_config

    code: str
    details: Any | None = None


class BaseResponseDTO(BaseModel, Generic[T]):
    """Base response structure for all API responses."""

    model_config = base_config

    success: bool = True
    data: T | None = None
    message: str | None = None
    error: ErrorDetailDTO | None = None


class BasePaginationDTO(BaseModel):
    """Base pagination structure."""

    model_config = base_config

    total: int
    page: int
    page_size: int = Field(..., alias="pageSize")
    total_pages: int = Field(..., alias="totalPages")


class BaseListResponseDataDTO(BaseModel, Generic[T]):
    """This is the *data* part for paginated list endpoints."""

    model_config = base_config

    pagination: BasePaginationDTO
    items: List[T]


class ListResponseDTO(BaseResponseDTO[BaseListResponseDataDTO[T]], Generic[T]):
    """This is the full envelope response for list endpoints."""

    model_config = base_config


class ErrorResponseDTO(BaseModel):
    """Error response structure."""

    model_config = base_config

    success: bool = False
    message: str
    code: str
    details: Any | None = None
