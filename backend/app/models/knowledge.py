"""标签树、本体概念树与知识实体的 ORM 映射。

表结构由 ``migrations/001_init.sql``（knowledge_entities）与
``migrations/005_taxonomy_and_roles.sql``（tags / ontology_concepts）定义，
这里只做映射。

三张表都把多值字段存成 JSON 文本，不拆关联表：标签 ID、概念 ID、来源、
关系都是整份读写，没有「按单个元素反查」的场景，拆表只换来 join 成本。
唯一留在列上的是标签/概念名，它要参与唯一约束与 ``q`` 模糊检索。

``KnowledgeEntity.tag_ids_json`` 只存**直接**打上的标签；契约里的
``effectiveTagIds`` / ``inheritedTagIds`` 由服务层沿父链算出来，不落库。
这样调整标签树层级时，历史知识条目不需要跟着批量重写。
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Tag(Base):
    """知识标签，单父树。父链深度由服务层限制，避免退化成链表。"""

    __tablename__ = "tags"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parent_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # active / inactive，只有 active 才有唯一名约束
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    # 互斥标签 ID 列表（JSON 数组），双向语义由服务层对称写入
    conflicts_with_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    owner_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class Concept(Base):
    """本体概念，单父树。结构与标签一致，但少了互斥关系。"""

    __tablename__ = "ontology_concepts"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parent_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    owner_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class KnowledgeEntity(Base):
    """知识条目：人物 / 典故 / 概念 / 符号。

    软停用（status='inactive'）而非删除：作品快照里存着 entityId，
    删条目会让历史作品的来源追溯断链。
    """

    __tablename__ = "knowledge_entities"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    # person / story / concept / symbol
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    alias_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_references_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    concept_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    tag_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    relations_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    time_theme: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    owner_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
