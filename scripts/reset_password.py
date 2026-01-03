#!/usr/bin/env python3
"""
Reset user password utility
"""

import argparse
import sys
from getpass import getpass
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.database import SessionLocal
from backend.models.user import User
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def reset_password(username: str, new_password: str = None):
    """Reset password for a user"""
    db = SessionLocal()

    try:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            print(f"❌ Error: User '{username}' not found")
            sys.exit(1)

        if not new_password:
            new_password = getpass("Enter new password: ")
            confirm = getpass("Confirm password: ")

            if new_password != confirm:
                print("❌ Error: Passwords do not match")
                sys.exit(1)

        if len(new_password) < 6:
            print("❌ Error: Password must be at least 6 characters")
            sys.exit(1)

        user.hashed_password = pwd_context.hash(new_password)
        db.commit()

        print(f"Password reset successfully for user '{username}'")

    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reset user password")
    parser.add_argument("--username", required=True, help="Username")
    parser.add_argument(
        "--new-password", help="New password (optional, will prompt if not provided)"
    )

    args = parser.parse_args()
    reset_password(args.username, args.new_password)
