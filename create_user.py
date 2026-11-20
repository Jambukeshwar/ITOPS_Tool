"""
User management script for Aria Credit Management Tool.

Usage:
  python3 create_user.py add <username> <password> [user|admin]   (default role: user)
  python3 create_user.py remove <username>
  python3 create_user.py passwd <username> <new_password>
  python3 create_user.py list
"""

import sys
import sqlite3
import hashlib
import os

DB_PATH = os.environ.get("DB_PATH", "users.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role     TEXT NOT NULL DEFAULT 'user'
        )
    """)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
    if "role" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
    conn.commit()
    return conn

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def add_user(username: str, password: str, role: str = "user"):
    if role not in ("user", "admin"):
        print("Role must be 'user' or 'admin'.")
        return
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
            (username, hash_password(password), role)
        )
        conn.commit()
        print(f"User '{username}' added ({role}).")
    except sqlite3.IntegrityError:
        print(f"User '{username}' already exists.")
    finally:
        conn.close()

def remove_user(username: str):
    conn = get_db()
    cursor = conn.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    if cursor.rowcount:
        print(f"User '{username}' removed.")
    else:
        print(f"User '{username}' not found.")
    conn.close()

def set_password(username: str, password: str):
    conn = get_db()
    n = conn.execute("UPDATE users SET password = ? WHERE username = ?", (hash_password(password), username)).rowcount
    conn.commit()
    conn.close()
    print(f"Password updated for '{username}'." if n else f"User '{username}' not found.")

def list_users():
    conn = get_db()
    rows = conn.execute("SELECT id, username, role FROM users").fetchall()
    conn.close()
    if not rows:
        print("No users found.")
    else:
        print(f"{'ID':<5} {'Username':<25} Role")
        print("-" * 40)
        for row in rows:
            print(f"{row[0]:<5} {row[1]:<25} {row[2]}")

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    cmd = args[0].lower()

    if cmd == "add" and len(args) in (3, 4):
        add_user(args[1], args[2], args[3].lower() if len(args) == 4 else "user")
    elif cmd == "remove" and len(args) == 2:
        remove_user(args[1])
    elif cmd == "passwd" and len(args) == 3:
        set_password(args[1], args[2])
    elif cmd == "list":
        list_users()
    else:
        print(__doc__)
