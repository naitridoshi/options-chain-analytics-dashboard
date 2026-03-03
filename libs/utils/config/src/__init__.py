import os.path
from os import path
from pathlib import Path

from dotenv import dotenv_values

BASE_DIR = str(Path(__file__).resolve().parent.parent.parent.parent.parent)

env_path = os.path.join(BASE_DIR, ".env")

if not path.exists(env_path):
    raise Exception(".env file not found")

config = dotenv_values(env_path)
