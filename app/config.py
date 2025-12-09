# app/config.py
from pathlib import Path
import os

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Database Configuration
# Ensure these match your local PostgreSQL setup
DB = {
    "dbname": "lexflow_db",
    "user": "postgres",
    "password": "root",  # <--- UPDATE THIS if you have a password
    "host": "localhost",
    "port": 5432,
}

# Uploads Directory Configuration
# This is the line your error is complaining about:
UPLOAD_DIR = BASE_DIR / "uploads"

# Create the directory if it doesn't exist
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)