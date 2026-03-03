from sqlalchemy.orm import declarative_base

Base = declarative_base()


class TimestampMixin:
    """
    Mixin to add created_at and updated_at timestamp fields to models.
    """

    from sqlalchemy import Column, DateTime, func

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
