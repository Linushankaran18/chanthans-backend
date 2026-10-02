"""Create an admin user. Run: python -m scripts.create_admin
Reset a forgotten password:  python -m scripts.create_admin --reset

Reads ADMIN_EMAIL / ADMIN_PASSWORD from the environment, or prompts for them.
"""
import asyncio
import getpass
import os
import sys

from app.core.database import get_sessionmaker
from app.core.security import hash_password
from app.models.user import ADMIN_ROLE, User
from app.repositories.user_repository import UserRepository

MIN_PASSWORD_LENGTH = 12


async def create_admin(email: str, password: str, reset: bool = False) -> None:
    async with get_sessionmaker()() as session:
        repo = UserRepository(session)
        existing = await repo.get_by_email(email)
        if existing and reset:
            existing.password_hash = hash_password(password)
            await session.commit()
            print(f"Password reset for {email}")
            return
        if existing:
            sys.exit(f"User {email} already exists (use --reset to change the password)")
        await repo.add(User(email=email, password_hash=hash_password(password), role=ADMIN_ROLE))
        await session.commit()
    print(f"Admin {email} created")


def main() -> None:
    email = os.environ.get("ADMIN_EMAIL") or input("Admin email: ").strip()
    password = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Password: ")
    if "@" not in email:
        sys.exit("Invalid email")
    if len(password) < MIN_PASSWORD_LENGTH:
        sys.exit(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    asyncio.run(create_admin(email, password, reset="--reset" in sys.argv))


if __name__ == "__main__":
    main()
