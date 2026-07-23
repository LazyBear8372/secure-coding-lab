from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DatabaseSession

from secure_coding_lab.db import Base
from secure_coding_lab.models import Product, Report, User, Wallet


@pytest.fixture
def database_session() -> DatabaseSession:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with DatabaseSession(engine) as session:
        yield session


def make_user(username: str) -> User:
    return User(username=username, password_hash="argon2-placeholder")


def test_username_is_unique(database_session: DatabaseSession) -> None:
    database_session.add_all([make_user("alice"), make_user("alice")])

    with pytest.raises(IntegrityError):
        database_session.commit()


def test_product_price_cannot_be_negative(database_session: DatabaseSession) -> None:
    seller = make_user("seller")
    database_session.add(seller)
    database_session.flush()
    database_session.add(
        Product(
            seller_id=seller.id,
            name="상품",
            description="설명",
            price=-1,
            image_key="products/example.jpg",
        )
    )

    with pytest.raises(IntegrityError):
        database_session.commit()


def test_report_requires_exactly_one_target(database_session: DatabaseSession) -> None:
    reporter = make_user("reporter")
    target = make_user("target")
    database_session.add_all([reporter, target])
    database_session.flush()
    database_session.add(
        Report(
            reporter_id=reporter.id,
            target_user_id=target.id,
            target_product_id=uuid4(),
            reason="잘못된 신고 대상",
        )
    )

    with pytest.raises(IntegrityError):
        database_session.commit()


def test_wallet_balance_cannot_be_negative(database_session: DatabaseSession) -> None:
    user = make_user("wallet-owner")
    database_session.add(user)
    database_session.flush()
    database_session.add(Wallet(user_id=user.id, balance=-1))

    with pytest.raises(IntegrityError):
        database_session.commit()
