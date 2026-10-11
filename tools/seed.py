"""FarFromHome Demo Data Seeder"""
import os
import sys
from pathlib import Path

# Ensure root is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from backend.app.db import SessionLocal, Base, engine
from backend.app.routers.demo import seed_demo_data

def main():
    print("Initializing database tables...")
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    print("Seeding demo users, Kozhikode trips, offline placeholder photos, stamps, and badges...")
    result = seed_demo_data(db)
    print("Seed complete:", result)
    db.close()

if __name__ == "__main__":
    main()
