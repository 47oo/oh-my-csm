"""Management CLI commands (seed database, reset admin, key rotation hook)."""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.core.database import dispose_engine, get_session_factory
from app.core.security import CredentialCipher
from app.models.credentials import Credential
from app.services.seed import seed_all


async def _cmd_seed(reset_admin: bool) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        stats = await seed_all(session, force_reset_admin_password=reset_admin)
        await session.commit()
    await dispose_engine()
    print(f"Seed complete: {stats}")


async def _cmd_rotate_keys(to_version: str) -> None:
    cipher = CredentialCipher.from_settings()
    session_factory = get_session_factory()
    rotated = 0
    async with session_factory() as session:
        rows = list(
            (await session.execute(select(Credential).where(Credential.deleted_at.is_(None))))
            .scalars()
            .all()
        )
        for cred in rows:
            if cred.key_version == to_version:
                continue
            plaintext = cipher.decrypt(cred.encrypted_secret)
            new_enc, new_ver = cipher.encrypt(plaintext, version=to_version)
            cred.encrypted_secret = new_enc
            cred.key_version = new_ver
            rotated += 1
        await session.commit()
    await dispose_engine()
    print(f"Rotated {rotated} credentials to key version '{to_version}'")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="oh-my-csm-cli", description="Oh-My-CSM management CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    seed_parser = sub.add_parser("seed", help="Seed built-in roles, default zone, dicts, and admin")
    seed_parser.add_argument(
        "--reset-admin-password",
        action="store_true",
        help="Force reset initial admin password to INITIAL_ADMIN_PASSWORD",
    )

    rot_parser = sub.add_parser(
        "rotate-credential-keys",
        help="Re-encrypt stored credentials to a target key version in CSM_SECRET_KEYS",
    )
    rot_parser.add_argument("--to-version", required=True, help="Target key version")

    args = parser.parse_args(argv)
    if args.command == "seed":
        asyncio.run(_cmd_seed(reset_admin=args.reset_admin_password))
        return 0
    if args.command == "rotate-credential-keys":
        asyncio.run(_cmd_rotate_keys(to_version=args.to_version))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
