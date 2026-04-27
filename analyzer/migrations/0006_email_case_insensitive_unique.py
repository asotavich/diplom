"""
Audit finding M-6 — case-insensitive UNIQUE constraint on auth_user.email.

Django's default User model only enforces email uniqueness at the
application layer (and even then only via DRF serializer validators).
Two concurrent registrations / profile updates with the same email can
race past that check and persist, which becomes an account-takeover
primitive the moment a "reset password by email" flow is added.

We add a functional UNIQUE INDEX on ``LOWER(email)`` so the database
becomes the source of truth. Postgres supports the expression index
natively; SQLite (used by the in-memory test runner) supports it as
well via ``CREATE UNIQUE INDEX ... ON auth_user (LOWER(email))``.

The migration is idempotent for existing rows when no duplicates are
present. Operators upgrading a fleet with pre-existing duplicate emails
must dedupe first (no automatic merge — too dangerous).
"""

from __future__ import annotations

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("analyzer", "0005_add_report_slug"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "auth_user_email_lower_uniq ON auth_user (LOWER(email))"
            ),
            reverse_sql=(
                "DROP INDEX IF EXISTS auth_user_email_lower_uniq"
            ),
        ),
    ]
