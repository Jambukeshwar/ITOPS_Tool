"""
User management script for Aria Credit Management Tool.

Usage:
  python3 create_user.py add <username> <password>
  python3 create_user.py remove <username>
  python3 create_user.py list
"""

import sys
import sqlite3
import hashlib
import os

DB_PATH = "users.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def add_user(username: str, password: str):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, password) VALUES (?, ?)",
            (username, hash_password(password))
        )
        conn.commit()
        print(f"✅ User '{username}' added successfully.")
    except sqlite3.IntegrityError:
        print(f"❌ User '{username}' already exists.")
    finally:
        conn.close()

def remove_user(username: str):
    conn = get_db()
    cursor = conn.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    if cursor.rowcount:
        print(f"✅ User '{username}' removed successfully.")
    else:
        print(f"❌ User '{username}' not found.")
    conn.close()

def list_users():
    conn = get_db()
    rows = conn.execute("SELECT id, username FROM users").fetchall()
    conn.close()
    if not rows:
        print("No users found.")
    else:
        print(f"{'ID':<5} {'Username'}")
        print("-" * 20)
        for row in rows:
            print(f"{row[0]:<5} {row[1]}")

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    cmd = args[0].lower()

    if cmd == "add" and len(args) == 3:
        add_user(args[1], args[2])
    elif cmd == "remove" and len(args) == 2:
        remove_user(args[1])
    elif cmd == "list":
        list_users()
    else:
        print(__doc__)