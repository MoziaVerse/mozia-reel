"""合并上游 v0.27 与 matrix 分支的迁移链

本发行版的 matrix 改造（custom_provider 加 owner_sso_sub、api_call 加 gateway_request_id）
与上游的 custom_endpoint 建表各自接在同一个祖先上，形成两个 head。

合并而不是把本分支的 down_revision 改指到上游 head：已部署的库里 alembic_version 停在本分支
的 head，重接后上游那一支就落在"当前版本之下"，upgrade 会认为它已应用而静默跳过——表现是
custom_endpoint 表根本没建，直到运行时报 no such table。合并节点让两支都被真正走一遍。

结构无变更，仅接线。
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0a4b49210d55"
down_revision: str | Sequence[str] | None = ("a1c7e94f0d23", "b64490270183")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
