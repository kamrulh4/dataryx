# core/core/configs/settings.py
import argparse
import os
import platform
import tempfile

from dotenv import find_dotenv, load_dotenv

from core.configs.utils import MutableBool
from shared.storage_config import storage

# Load environment variables from .env file.
# find_dotenv(usecwd=True) is required for frozen/packaged builds (PyInstaller Windows .exe):
# Without usecwd=True, dotenv traverses the Python call stack to find .env, which causes
# an AssertionError when frame.f_back becomes None in a packaged binary.
_dotenv_path = find_dotenv(usecwd=True)
load_dotenv(dotenv_path=_dotenv_path if _dotenv_path else None)

# Constants for server and worker configuration
DEFAULT_SERVER_HOST = "0.0.0.0"
DEFAULT_SERVER_PORT = 63578
DEFAULT_WORKER_PORT = 63579

# Single file mode flag, this determines where worker requests are being send to.
SINGLE_FILE_MODE: MutableBool = MutableBool(
    os.environ.get("DATARYX_SINGLE_FILE_MODE", "0") == "1"
)

# Offload to worker flag, this determines if the worker should handle processing tasks.
OFFLOAD_TO_WORKER: MutableBool = MutableBool(
    os.environ.get("DATARYX_OFFLOAD_TO_WORKER", "0") == "1"
)


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description="Dataryx Backend Server")
    parser.add_argument(
        "--host", type=str, default=DEFAULT_SERVER_HOST, help="Host to bind to"
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_SERVER_PORT, help="Port to bind to"
    )
    parser.add_argument(
        "--worker-port",
        type=int,
        help="Port for the worker process",
    )
    args = parser.parse_known_args()[0]

    return args


def get_temp_dir() -> str:
    """Get the appropriate temp directory path based on environment"""
    # Check for Docker environment variable first
    docker_temp = os.getenv("TEMP_DIR")
    if docker_temp:
        return docker_temp

    return tempfile.gettempdir()


def get_default_worker_url(worker_port=None):
    """
    Get the default worker URL based on environment and settings

    Args:
        worker_port: Optional port override (used when passed as command line arg)
    """
    # Check for Docker environment first
    worker_host = os.getenv("WORKER_HOST", None)

    if worker_port is None:
        worker_port = os.getenv("DATARYX_WORKER_PORT", DEFAULT_WORKER_PORT)

    # Convert to int if it's a string
    worker_port = int(worker_port) if isinstance(worker_port, str) else worker_port

    if worker_host:
        worker_url = f"http://{worker_host}:{worker_port}"

    elif platform.system() == "Windows":
        worker_url = f"http://127.0.0.1:{worker_port}"
    else:
        worker_url = f"http://0.0.0.0:{worker_port}"
    worker_url += "/worker" if SINGLE_FILE_MODE else ""
    return worker_url


args = parse_args()

SERVER_HOST = args.host if args.host is not None else DEFAULT_SERVER_HOST
SERVER_PORT = args.port if args.port is not None else DEFAULT_SERVER_PORT
WORKER_PORT = (
    args.worker_port
    if args.worker_port is not None
    else int(os.getenv("DATARYX_WORKER_PORT", DEFAULT_WORKER_PORT))
)
WORKER_HOST = os.getenv(
    "WORKER_HOST", "0.0.0.0" if platform.system() != "Windows" else "127.0.0.1"
)

DEBUG: bool = os.getenv("DEBUG", "False").lower() in ("true", "1", "t", "y", "yes")
FILE_LOCATION = os.getenv("FILE_LOCATION", ".\\files\\")
AVAILABLE_RAM = int(os.getenv("AVAILABLE_RAM", "8"))
WORKER_URL = os.getenv("DATARYX_WORKER_URL", get_default_worker_url(WORKER_PORT))
TEMP_DIR = storage.temp_directory
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://127.0.0.1:8000")

# DATARYX_MODE: Determines the runtime environment
# Possible values: "electron" (desktop app), "package" (Python package), "docker" (container)
DATARYX_MODE = os.getenv("DATARYX_MODE", "electron")

# Preserve DATARYX_MODE for internal logic cross-referencing if needed, but primary is DATARYX_MODE
DATARYX_MODE = DATARYX_MODE


def is_docker_mode() -> bool:
    """Check if running in Docker container mode"""
    return DATARYX_MODE == "docker"


def is_electron_mode() -> bool:
    """Check if running in Electron desktop app mode"""
    return DATARYX_MODE == "electron"


def is_package_mode() -> bool:
    """Check if running as Python package"""
    return DATARYX_MODE == "package"


# Legacy compatibility - will be removed in future versions
IS_RUNNING_IN_DOCKER = is_docker_mode()

ACCESS_TOKEN_EXPIRE_MINUTES = 120
