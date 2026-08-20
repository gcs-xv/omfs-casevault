#!/usr/bin/env python3
from pathlib import Path
from alembic import command
from alembic.config import Config
Path("data").mkdir(exist_ok=True);Path("cache").mkdir(exist_ok=True)
command.upgrade(Config("alembic.ini"),"head")
print("CaseVault database initialized.")

