from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.app.db import get_db
from backend.app.models.models import SOSEvent, User
from backend.app.security import get_current_user

router = APIRouter(prefix="/sos", tags=["sos"])

@router.get("/events")
def list_sos_events(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    events = db.query(SOSEvent).filter(SOSEvent.user_id == user.id).order_by(SOSEvent.created_at.desc()).all()
    return [
        {
            "id": e.id,
            "device_id": e.device_id,
            "trip_id": e.trip_id,
            "lat": e.lat,
            "lng": e.lng,
            "status": e.status,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]
