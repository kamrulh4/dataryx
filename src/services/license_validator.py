"""
License validation module with HWID locking.
Validates license keys and ensures they match the current hardware.
Supports tiered licensing (Basic/Pro/Enterprise) and 90-day trials.
"""
import base64
import json
import os
from datetime import datetime, timedelta
from typing import Optional, Tuple
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.exceptions import InvalidSignature
from .hwid import generate_hwid


LICENSE_PREFIX = "DRYX-"
LICENSE_FILE = "license.dat"
TRIAL_FILE = "trial.json"
TRIAL_DAYS = 90  # 3 months trial as requested by client

VALID_TIERS = ("Basic", "Pro", "Enterprise")
TIER_FLOW_LIMITS = {
    "Trial": 3,
    "Basic": 3,
    "Pro": 10,
    "Enterprise": 30,
}


class LicenseError(Exception):
    """Base exception for license-related errors."""
    pass


class LicenseValidator:
    """Validates license keys with HWID locking."""

    def __init__(self, public_key_pem: Optional[str] = None):
        if public_key_pem is None:
            public_key_pem = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAmpKjmnIyWKrBuiDHyKDM
12CUXLPQR2PxpjhojaAJ2S0z8SKvauQI9gF/an7cjtPs7tjCV9tU7G4KPGoexmLJ
HIWREgRqxjufaNhkPhJafGIPVUKw1g1nLIQn3PJ91X/AgrRVZApAGPqt2k0bz7Eq
nG7EUF+YlhCRYEX8YZUJtQ5e/Lhfk0W5wQzguC5V+qUi4kn7P4xh2y9n0qlyCz/3
/Il7zSTFETsqZQAoxozcxgGueJrb2nNPw3bUgcaiDKFV5DynLL1SvqASYkzM4cxU
lAErRDGCWbXf7i2L0DD2VItZ/bjQiki5UWZ8xSZGWuKXzs/qLgnSv92A3YB+zvw2
3QIDAQAB
-----END PUBLIC KEY-----"""

        try:
            self.public_key = serialization.load_pem_public_key(
                public_key_pem.encode('utf-8')
            )
        except Exception as e:
            raise LicenseError(f"Failed to load public key: {e}")

    def _validate_license_data(self, license_data: dict) -> Tuple[bool, str]:
        """Validate parsed license data against current hardware."""
        hwid = license_data.get('hwid')
        expiration = license_data.get('expiration')
        customer_name = license_data.get('customer_name', 'Unknown')
        signature = license_data.get('signature')
        tier = license_data.get('tier')
        max_flows = license_data.get('max_flows')

        if not all([hwid, signature]):
            return False, "Invalid license: missing required fields"

        if tier and tier not in VALID_TIERS:
            return False, f"Invalid license tier: {tier}"

        # Verify HWID matches current hardware
        current_hwid = generate_hwid()
        if hwid != current_hwid:
            return False, "License is locked to different hardware"

        # Check expiration
        if expiration:
            try:
                exp_date = datetime.fromisoformat(expiration)
                if datetime.now() > exp_date:
                    return False, f"License expired on {exp_date.strftime('%Y-%m-%d')}"
            except ValueError:
                return False, "Invalid expiration date format"

        # Verify RSA signature
        # Build message matching the format used during generation
        if tier:
            message = f"{hwid}|{expiration or 'perpetual'}|{customer_name}|{tier}"
            if max_flows is not None:
                message += f"|{max_flows}"
            message = message.encode('utf-8')
        else:
            message = f"{hwid}|{expiration or 'perpetual'}|{customer_name}".encode('utf-8')
        signature_bytes = bytes.fromhex(signature)

        try:
            self.public_key.verify(
                signature_bytes,
                message,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH
                ),
                hashes.SHA256()
            )
        except InvalidSignature:
            return False, "Invalid license key"

        return True, ""

    def validate_key_string(self, key_string: str) -> Tuple[bool, str]:
        """Validate a DRYX-... license key string."""
        key_string = key_string.strip()

        if not key_string.startswith(LICENSE_PREFIX):
            return False, "Invalid license key format"

        try:
            encoded = key_string[len(LICENSE_PREFIX):]
            json_bytes = base64.urlsafe_b64decode(encoded)
            license_data = json.loads(json_bytes)
        except Exception:
            return False, "Invalid license key"

        return self._validate_license_data(license_data)

    def validate_saved(self) -> Tuple[bool, str]:
        """Validate the saved license file."""
        license_path = self._find_license_file()
        if license_path is None:
            return False, "No license found"

        try:
            with open(license_path, 'r', encoding='utf-8') as f:
                key_string = f.read().strip()
            return self.validate_key_string(key_string)
        except Exception:
            return False, "Failed to read license file"

    def _find_license_file(self) -> Optional[str]:
        """Find the license file in known locations."""
        search_paths = [
            os.path.join(os.path.expanduser('~'), '.dataryx', LICENSE_FILE),
            LICENSE_FILE,
            os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', LICENSE_FILE),
        ]

        for path in search_paths:
            if os.path.exists(path):
                return path

        return None


def get_license_dir() -> str:
    """Get the persistent license directory (~/.dataryx/)."""
    license_dir = os.path.join(os.path.expanduser('~'), '.dataryx')
    os.makedirs(license_dir, exist_ok=True)
    return license_dir


def _get_trial_path() -> str:
    """Get the path to the trial file."""
    return os.path.join(get_license_dir(), TRIAL_FILE)


def start_trial() -> dict:
    """
    Start a new trial period. Creates trial.json with start date and HWID.
    Returns trial info dict.
    """
    trial_path = _get_trial_path()
    hwid = generate_hwid()
    trial_data = {
        "started_at": datetime.now().isoformat(),
        "hwid": hwid,
    }
    with open(trial_path, 'w', encoding='utf-8') as f:
        json.dump(trial_data, f)

    return {
        "is_trial": True,
        "customer_name": "Trial",
        "tier": "Trial",
        "expiration": (datetime.now() + timedelta(days=TRIAL_DAYS)).isoformat(),
        "days_left": TRIAL_DAYS,
        "max_flows": TIER_FLOW_LIMITS["Trial"],
    }


def get_trial_info() -> Optional[dict]:
    """
    Get trial info if a trial exists and is still valid.
    Returns None if no trial or trial expired.
    """
    trial_path = _get_trial_path()
    if not os.path.exists(trial_path):
        return None

    try:
        with open(trial_path, 'r', encoding='utf-8') as f:
            trial_data = json.load(f)

        # Verify HWID matches
        if trial_data.get('hwid') != generate_hwid():
            return None

        started_at = datetime.fromisoformat(trial_data['started_at'])
        exp_date = started_at + timedelta(days=TRIAL_DAYS)
        days_left = (exp_date - datetime.now()).days

        if days_left < 0:
            return None  # Trial expired

        return {
            "is_trial": True,
            "customer_name": "Trial",
            "tier": "Trial",
            "expiration": exp_date.isoformat(),
            "days_left": days_left,
            "max_flows": TIER_FLOW_LIMITS["Trial"],
        }
    except Exception:
        return None


def activate_license(key_string: str) -> Tuple[bool, str]:
    """
    Validate a license key string and save it persistently.

    Args:
        key_string: The DRYX-... license key string.

    Returns:
        Tuple of (success, message).
    """
    validator = LicenseValidator()
    is_valid, error = validator.validate_key_string(key_string)

    if not is_valid:
        return False, error

    # Save to ~/.dataryx/license.dat
    dest_path = os.path.join(get_license_dir(), LICENSE_FILE)
    try:
        with open(dest_path, 'w', encoding='utf-8') as f:
            f.write(key_string.strip())
        return True, "License activated successfully"
    except Exception as e:
        return False, f"Failed to save license: {e}"


def get_license_info() -> Optional[dict]:
    """
    Get info from the saved license or active trial.
    Returns None if no valid license or trial found.
    Paid licenses take priority over trial.
    """
    # Check for paid license first
    validator = LicenseValidator()
    license_path = validator._find_license_file()
    if license_path is not None:
        try:
            with open(license_path, 'r', encoding='utf-8') as f:
                key_string = f.read().strip()

            # Validate before returning info
            is_valid, _ = validator.validate_key_string(key_string)
            if is_valid:
                encoded = key_string[len(LICENSE_PREFIX):]
                json_bytes = base64.urlsafe_b64decode(encoded)
                data = json.loads(json_bytes)

                expiration = data.get('expiration')
                days_left = None
                if expiration:
                    exp_date = datetime.fromisoformat(expiration)
                    days_left = (exp_date - datetime.now()).days

                tier = data.get('tier', 'Basic')
                max_flows = data.get('max_flows', TIER_FLOW_LIMITS.get(tier))

                return {
                    "is_trial": False,
                    "customer_name": data.get('customer_name', 'Unknown'),
                    "tier": tier,
                    "expiration": expiration,
                    "days_left": days_left,
                    "max_flows": max_flows,
                }
        except Exception:
            pass

    # Fall back to trial
    return get_trial_info()


def check_license() -> Tuple[bool, str]:
    """Check if a valid saved license or active trial exists."""
    # Check paid license first
    validator = LicenseValidator()
    is_valid, error = validator.validate_saved()
    if is_valid:
        return True, ""

    # Check trial
    trial_info = get_trial_info()
    if trial_info:
        return True, ""

    # If no trial file exists, initialize it (on install/first run)
    if not os.path.exists(_get_trial_path()):
        start_trial()
        return True, ""

    return False, error
