from __future__ import annotations

import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.integrations.onec import process_members_payload
from app.models import Base, OrgMember, Organization, User


class AsyncSessionWrapper:
    def __init__(self, session: Session) -> None:
        self._session = session

    async def execute(self, statement):
        return self._session.execute(statement)

    async def flush(self):
        self._session.flush()

    def add(self, instance) -> None:
        self._session.add(instance)


def _make_session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_process_members_payload_upserts_users_and_members():
    session = _make_session()
    existing_user = User(
        fio="Existing",
        phone="+79990001122",
        email=None,
        password_hash="!",
        address=None,
        work_time=None,
        is_24h=False,
        role="client",
    )
    session.add(existing_user)
    session.flush()
    async_session = AsyncSessionWrapper(session)

    payload = {
        "org": {"external_id": "ORG-1", "name": "Org Name"},
        "members": [
            {"phone": "+79990001122", "fio": "Updated FIO", "role_in_org": "owner", "status": "active"},
            {"phone": "+79990002233", "fio": "New User", "role_in_org": "member", "status": "pending"},
        ],
    }

    result = asyncio.run(process_members_payload(async_session, payload))
    session.commit()

    assert result["orgs_received"] == 1
    assert result["members_received"] == 2
    assert result["orgs_upserted"] == 1
    assert result["users_created"] == 1
    assert result["members_upserted"] == 2
    assert result["skipped"] == 0

    org = session.query(Organization).one()
    assert org.external_id == "ORG-1"
    assert session.query(User).count() == 2
    assert session.query(OrgMember).count() == 2


def test_process_members_payload_skips_missing_phone():
    session = _make_session()
    async_session = AsyncSessionWrapper(session)

    payload = {
        "org_name": "Org Name",
        "members": [
            {"fio": "No Phone"},
        ],
    }

    result = asyncio.run(process_members_payload(async_session, payload))
    session.commit()

    assert result["members_received"] == 1
    assert result["skipped"] == 1
    assert result["users_created"] == 0
    assert session.query(OrgMember).count() == 0
