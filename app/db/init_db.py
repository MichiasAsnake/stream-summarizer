"""DB init helper."""
from app.db.session import init_db

if __name__ == "__main__":
    init_db()
    print("db ready")
