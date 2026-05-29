# ruff: noqa: E402

import os
from importlib.metadata import PackageNotFoundError, version

from core.utils.validate_setup import validate_setup

validate_setup()
from core.database.init_db import init_db
from core.dataryx.handler import DataryxHandler

if "DATARYX_MODE" not in os.environ:
    os.environ["DATARYX_MODE"] = "electron"

init_db()

class ServerRun:
    exit: bool = False


try:
    __version__ = version("Dataryx")
except PackageNotFoundError:
    __version__ = "0.5.0"

flow_file_handler = DataryxHandler()
