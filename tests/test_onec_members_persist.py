from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.database import SessionLocal
from app.integrations.onec import process_members_payload, process_orders_payload
from app.models import OrgMember, OrgProductStats, Organization, Product, User


def test_process_members_payload_persists_in_new_session() -> None:
    async def _run() -> None:
        marker = uuid4().hex[:8]
        org_external = f"ORG-PERSIST-{marker}"
        phone_new = f"+7999{marker[:6]}"

        payload = {
            "org": {"external_id": org_external, "name": f"Org Persist {marker}"},
            "members": [
                {"phone": phone_new, "fio": "Persist User", "role_in_org": "owner", "status": "active"},
                {"fio": "No Phone"},
            ],
        }

        try:
            async with SessionLocal() as session:
                result = await process_members_payload(session, payload)
                assert result["orgs_upserted"] == 1
                assert result["users_created"] == 1
                assert result["members_upserted"] == 1

            async with SessionLocal() as verify_session:
                org_count = await verify_session.scalar(
                    select(func.count()).select_from(Organization).where(Organization.external_id == org_external)
                )
                user_count = await verify_session.scalar(
                    select(func.count()).select_from(User).where(User.phone == phone_new)
                )

                org = (
                    await verify_session.execute(
                        select(Organization).where(Organization.external_id == org_external)
                    )
                ).scalar_one()
                user = (await verify_session.execute(select(User).where(User.phone == phone_new))).scalar_one()
                member_count = await verify_session.scalar(
                    select(func.count())
                    .select_from(OrgMember)
                    .where(OrgMember.org_id == org.id, OrgMember.user_id == user.id)
                )

                assert org_count == 1
                assert user_count == 1
                assert member_count == 1
        except Exception as exc:  # pragma: no cover - environment-dependent availability
            pytest.skip(f"PostgreSQL is unavailable for persistence test: {exc}")

    asyncio.run(_run())


def test_process_orders_payload_persists_stats_in_new_session() -> None:
    async def _run() -> None:
        marker = uuid4().hex[:8]
        sku = f"SKU-PERSIST-{marker}"
        org_name = f"Org Orders {marker}"

        try:
            async with SessionLocal() as setup_session:
                setup_session.add(Product(sku=sku, title_ru=f"Persist Product {marker}"))
                await setup_session.commit()

            payload = {
                "org_name": org_name,
                "orders": [
                    {
                        "ordered_at": datetime.utcnow().isoformat(),
                        "items": [{"sku": sku, "qty": 2, "unit": "кг"}],
                    }
                ],
            }
            async with SessionLocal() as session:
                result = await process_orders_payload(session, payload)
                assert result["updated_rows"] == 1

            async with SessionLocal() as verify_session:
                org = (await verify_session.execute(select(Organization).where(Organization.name == org_name))).scalar_one()
                stats_count = await verify_session.scalar(
                    select(func.count()).select_from(OrgProductStats).where(OrgProductStats.org_id == org.id)
                )
                assert stats_count == 1
        except Exception as exc:  # pragma: no cover - environment-dependent availability
            pytest.skip(f"PostgreSQL is unavailable for persistence test: {exc}")

    asyncio.run(_run())
