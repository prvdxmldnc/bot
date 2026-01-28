from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, Order, OrderItem, Organization, Product, Thread, User


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


async def find_product_by_text(session: AsyncSession, text: str) -> Product | None:
    result = await session.execute(select(Product).where(Product.title_ru.ilike(f"%{text}%")))
    return result.scalars().first()


async def create_order(session: AsyncSession, user: User, product: Product, qty: int = 1) -> Order:
    org_id = user.org_memberships[0].org_id if user.org_memberships else None
    order = Order(org_id=org_id, created_by_user_id=user.id)
    session.add(order)
    await session.flush()
    item = OrderItem(order_id=order.id, product_id=product.id, qty=qty, price_at_time=product.price)
    session.add(item)
    await session.flush()
    return order


async def list_orders_for_user(session: AsyncSession, user: User) -> list[Order]:
    result = await session.execute(select(Order).where(Order.created_by_user_id == user.id).order_by(Order.created_at.desc()))
    return list(result.scalars().all())


async def create_thread(session: AsyncSession, org_id: int | None, title: str) -> Thread:
    thread = Thread(org_id=org_id, title=title)
    session.add(thread)
    await session.flush()
    return thread
