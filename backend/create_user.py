"""Create (or update the password of) a user.

Usage:  python create_user.py <email> <password>
"""
import sys

import auth
import db


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: python create_user.py <email> <password>")
    email = sys.argv[1].lower().strip()
    password = sys.argv[2]

    # Single-user editions get one account. Updating THAT account's password is
    # still allowed — it is how someone locks themselves out and back in — but a
    # second person is not, because nothing downstream is built to separate them.
    import edition
    if not edition.multi_tenant():
        with db.connect() as conn:
            other = conn.execute("SELECT email FROM users WHERE email <> %s LIMIT 1",
                                 (email,)).fetchone()
        if other:
            sys.exit(f"This is a single-user installation and its account is "
                     f"{other['email']}. Change that password instead, or set "
                     f"ENTRYSTATION_EDITION=cloud to run multi-user.")
    pw_hash = auth.hash_password(password)
    with db.connect() as conn:
        row = conn.execute(
            """INSERT INTO users (email, password_hash) VALUES (%s, %s)
               ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash
               RETURNING id""",
            (email, pw_hash),
        ).fetchone()
        # An API key, unasked. This is the self-hosted onboarding path, and the
        # app issues one on signup anyway — arriving to an empty API keys panel
        # is a detour, not a decision.
        if not conn.execute("SELECT 1 FROM api_keys WHERE user_id = %s", (row["id"],)).fetchone():
            raw = auth.generate_api_key()
            prefix, last_four = auth.key_display(raw)
            conn.execute(
                "INSERT INTO api_keys (user_id, name, key_prefix, last_four, key_hash, key_plain) "
                "VALUES (%s, 'Default', %s, %s, %s, %s)",
                (row["id"], prefix, last_four, auth.hash_key(raw), raw))
            print(f"api key issued: {raw}")
        conn.commit()
    print(f"user ready: {email}")


if __name__ == "__main__":
    main()
