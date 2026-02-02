from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Category, Order, OrderItem, Organization, Product, SearchLog, Thread, User


async def get_user_by_phone(session: AsyncSession, phone: str) -> User | None:
    result = await session.execute(select(User).where(User.phone == phone))
    return result.scalar_one_or_none()


async def get_user_by_tg_id(session: AsyncSession, tg_id: int) -> User | None:
    result = await session.execute(select(User).where(User.tg_id == tg_id))
    return result.scalar_one_or_none()


async def create_organization(session: AsyncSession, name: str, owner_id: int) -> Organization:
    organization = Organization(name=name, owner_user_id=owner_id)
    session.add(organization)
    await session.flush()
    return organization


async def list_root_categories(session: AsyncSession) -> list[Category]:
    result = await session.execute(select(Category).where(Category.parent_id.is_(None)).order_by(Category.order_index))
    return list(result.scalars().all())


async def list_subcategories(session: AsyncSession, parent_id: int) -> list[Category]:
    result = await session.execute(select(Category).where(Category.parent_id == parent_id).order_by(Category.order_index))
    return list(result.scalars().all())


async def list_products_by_category(session: AsyncSession, category_id: int) -> list[Product]:
    result = await session.execute(select(Product).where(Product.category_id == category_id))
    return list(result.scalars().all())


async def find_products_by_text(session: AsyncSession, text: str, limit: int = 10) -> list[Product]:
    query = select(Product).where(Product.title_ru.ilike(f"%{text}%"))
    if any(char.isdigit() for char in text):
        query = select(Product).where(
            or_(Product.sku.ilike(f"%{text}%"), Product.title_ru.ilike(f"%{text}%"))
        )
    result = await session.execute(query.limit(limit))
    return list(result.scalars().all())


async def create_search_log(
    session: AsyncSession,
    user_id: int | None,
    text: str,
    parsed_json: str | None = None,
    selected_json: str | None = None,
    confidence: float | None = None,
) -> None:
    session.add(
        SearchLog(
            user_id=user_id,
            raw_text=text,
            parsed_json=parsed_json,
            selected_json=selected_json,
            confidence=confidence,
        )
    )
    await session.flush()


async def get_or_create_draft_order(session: AsyncSession, user: User) -> Order:
    result = await session.execute(
        select(Order)
        .where(Order.created_by_user_id == user.id, Order.status == "draft")
        .options(selectinload(Order.items))
    )
    order = result.scalar_one_or_none()
    if order:
        return order
    org_id = user.org_memberships[0].org_id if user.org_memberships else None
    order = Order(org_id=org_id, created_by_user_id=user.id, status="draft")
    session.add(order)
    await session.flush()
    return order


async def add_item_to_order(session: AsyncSession, order: Order, product: Product, qty: int = 1) -> None:
    result = await session.execute(
        select(OrderItem).where(OrderItem.order_id == order.id, OrderItem.product_id == product.id)
    )
    item = result.scalar_one_or_none()
    if item:
        item.qty += qty
        item.price_at_time = product.price
    else:
        item = OrderItem(order_id=order.id, product_id=product.id, qty=qty, price_at_time=product.price)
        session.add(item)
    await session.flush()


async def list_orders_for_user(session: AsyncSession, user: User) -> list[Order]:
    result = await session.execute(
        select(Order)
        .where(Order.created_by_user_id == user.id)
        .options(selectinload(Order.items).selectinload(OrderItem.product))
        .order_by(Order.created_at.desc())
    )
    return list(result.scalars().all())


async def create_thread(session: AsyncSession, org_id: int | None, title: str) -> Thread:
    thread = Thread(org_id=org_id, title=title)
    session.add(thread)
    await session.flush()
    return thread
