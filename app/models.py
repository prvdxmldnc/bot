from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    external_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    members = relationship("OrgMember", back_populates="organization")
    orders = relationship("Order", back_populates="organization")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    fio: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str] = mapped_column(String(32), unique=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    work_time: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_24h: Mapped[bool] = mapped_column(Boolean, default=False)
    role: Mapped[str] = mapped_column(String(32), default="client")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    org_memberships = relationship("OrgMember", back_populates="user")
    messages = relationship("Message", back_populates="author")


class OrgMember(Base):
    __tablename__ = "org_members"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_org_members_org_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    role_in_org: Mapped[str] = mapped_column(String(32), default="member")
    status: Mapped[str] = mapped_column(String(32), default="active")

    organization = relationship("Organization", back_populates="members")
    user = relationship("User", back_populates="org_memberships")


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    title_ru: Mapped[str] = mapped_column(String(255))
    title_lat: Mapped[str | None] = mapped_column(String(255), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0)

    products = relationship("Product", back_populates="category")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    title_ru: Mapped[str] = mapped_column(String(255))
    title_lat: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    stock_qty: Mapped[int] = mapped_column(Integer, default=0)
    price: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    image_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)

    category = relationship("Category", back_populates="products")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    delivery_day: Mapped[str | None] = mapped_column(String(16), nullable=True)
    delivery_today: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    organization = relationship("Organization", back_populates="orders")
    items = relationship("OrderItem", back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    qty: Mapped[int] = mapped_column(Integer, default=1)
    price_at_time: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    order = relationship("Order", back_populates="items")
    product = relationship("Product")


class Thread(Base):
    __tablename__ = "threads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    messages = relationship("Message", back_populates="thread")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("threads.id"))
    author_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    author_name_snapshot: Mapped[str] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    thread = relationship("Thread", back_populates="messages")
    author = relationship("User", back_populates="messages")


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SearchLog(Base):
    __tablename__ = "search_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    raw_text: Mapped[str] = mapped_column(Text)
    parsed_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OrgProductStats(Base):
    __tablename__ = "org_product_stats"
    __table_args__ = (
        UniqueConstraint("org_id", "product_id", name="uq_org_product_stats_org_product"),
        Index(
            "ix_org_product_stats_org_ordered",
            "org_id",
            "orders_count",
            "last_order_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    orders_count: Mapped[int] = mapped_column(Integer, default=0)
    qty_sum: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    last_order_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_qty: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    last_unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OrgAlias(Base):
    __tablename__ = "org_aliases"
    __table_args__ = (
        UniqueConstraint("org_id", "normalized_alias", "product_id", name="uq_org_aliases_org_alias_product"),
        Index("ix_org_aliases_org_alias", "org_id", "normalized_alias"),
        Index("ix_org_aliases_org_weight", "org_id", "weight", "last_used_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    alias_text: Mapped[str] = mapped_column(String(255))
    normalized_alias: Mapped[str] = mapped_column(String(255))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    weight: Mapped[int] = mapped_column(Integer, default=1)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SearchAlias(Base):
    __tablename__ = "search_aliases"
    __table_args__ = (
        UniqueConstraint("org_id", "src", name="uq_search_aliases_org_src"),
        Index("ix_search_aliases_org_src", "org_id", "src"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    src: Mapped[str] = mapped_column(String(255))
    dst: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(16), default="token")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)



class CategoryFacetConfig(Base):
    __tablename__ = "category_facet_configs"
    __table_args__ = (
        Index("ix_category_facet_configs_category", "category_id", "facet_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    facet_key: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
