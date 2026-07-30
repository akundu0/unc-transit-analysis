"""initial schema — vehicle_positions and trip_updates

Revision ID: 530e5be364fe
Revises: 
Create Date: 2026-07-30 01:05:10.868590
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '530e5be364fe'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vehicle_positions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("vehicle_id", sa.String(), nullable=False),
        sa.Column("trip_id", sa.String(), nullable=True),
        sa.Column("route_id", sa.String(), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("bearing", sa.Float(), nullable=True),
        sa.Column("speed", sa.Float(), nullable=True),
        sa.Column("current_status", sa.Integer(), nullable=True),
        sa.Column("stop_id", sa.String(), nullable=True),
        sa.Column("stop_sequence", sa.Integer(), nullable=True),
        sa.Column("polled_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vp_polled_route", "vehicle_positions", ["polled_at", "route_id"])
    op.create_index("ix_vp_route_id", "vehicle_positions", ["route_id"])

    op.create_table(
        "trip_updates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trip_id", sa.String(), nullable=False),
        sa.Column("route_id", sa.String(), nullable=True),
        sa.Column("stop_id", sa.String(), nullable=True),
        sa.Column("stop_sequence", sa.Integer(), nullable=True),
        sa.Column("predicted_arrival_delay_seconds", sa.Integer(), nullable=True),
        sa.Column("predicted_departure_delay_seconds", sa.Integer(), nullable=True),
        sa.Column("arrival_time", sa.DateTime(), nullable=True),
        sa.Column("polled_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tu_polled_route", "trip_updates", ["polled_at", "route_id"])
    op.create_index("ix_tu_route_id", "trip_updates", ["route_id"])


def downgrade() -> None:
    op.drop_index("ix_tu_route_id", table_name="trip_updates")
    op.drop_index("ix_tu_polled_route", table_name="trip_updates")
    op.drop_table("trip_updates")
    op.drop_index("ix_vp_route_id", table_name="vehicle_positions")
    op.drop_index("ix_vp_polled_route", table_name="vehicle_positions")
    op.drop_table("vehicle_positions")
