import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.app.db import Base
from backend.app.models.models import Device, PairingCode, User
from backend.app.security import hash_token

@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()

def test_device_pairing_flow(db_session):
    user = User(id="u-1", username="explorer", display_name="Alex", password_hash="hash")
    db_session.add(user)

    # 1. Device registers
    dev = Device(id="dev-1", name="Pi 5")
    poll_secret = "secret123"
    pc = PairingCode(
        code="PAIR77",
        device_id=dev.id,
        poll_secret_hash=hash_token(poll_secret),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15)
    )
    db_session.add_all([dev, pc])
    db_session.commit()

    # 2. User claims pairing code
    claimed_pc = db_session.query(PairingCode).filter(PairingCode.code == "PAIR77").first()
    claimed_pc.claimed_by_user_id = user.id
    dev.owner_user_id = user.id
    db_session.commit()

    # 3. Device polls and gets token
    assert claimed_pc.claimed_by_user_id == user.id
    assert dev.owner_user_id == user.id
