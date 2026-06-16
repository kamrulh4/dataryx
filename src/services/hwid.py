"""
Hardware ID (HWID) generation module.
Generates a unique hardware fingerprint based on system characteristics.
"""
import hashlib
import platform
import uuid


def get_mac_address() -> str:
    """Get primary physical MAC address."""
    try:
        if platform.system() == "Linux":
            import os
            net_dir = "/sys/class/net"
            for iface in sorted(os.listdir(net_dir)):
                # Skip loopback and virtual interfaces
                if iface == "lo" or iface.startswith(("veth", "docker", "br-", "virbr")):
                    continue
                addr_path = os.path.join(net_dir, iface, "address")
                if os.path.exists(addr_path):
                    with open(addr_path, "r") as f:
                        mac = f.read().strip().upper()
                    if mac and mac != "00:00:00:00:00:00":
                        return mac
        # Fallback for Windows and other platforms
        mac = uuid.getnode()
        return ':'.join(("%012X" % mac)[i:i+2] for i in range(0, 12, 2))
    except Exception:
        return ""


def get_machine_id() -> str:
    """Get machine-id (Linux) or MachineGuid (Windows)."""
    try:
        if platform.system() == "Linux":
            with open("/etc/machine-id", "r") as f:
                return f.read().strip()
        elif platform.system() == "Windows":
            import subprocess
            result = subprocess.run(
                ["reg", "query",
                 "HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Cryptography",
                 "/v", "MachineGuid"],
                capture_output=True,
                text=True,
                timeout=5
            )
            for line in result.stdout.split("\n"):
                if "MachineGuid" in line:
                    parts = line.split()
                    if len(parts) >= 3:
                        return parts[-1].strip()
    except Exception:
        pass
    return ""


def generate_hwid() -> str:
    """
    Generate a unique hardware ID from MAC address + machine-id.
    These two identifiers are the most stable across all environments
    (dev, packaged app, different shells, etc).

    Returns:
        str: A SHA256 hash representing the unique hardware fingerprint.
    """
    hw_string = f"{get_mac_address()}|{get_machine_id()}|{platform.system()}"
    return hashlib.sha256(hw_string.encode('utf-8')).hexdigest()


def get_hwid_display() -> str:
    """
    Get a user-friendly display format of the HWID.

    Returns:
        str: HWID in format: XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX
    """
    hwid = generate_hwid()
    return '-'.join([hwid[i:i+4].upper() for i in range(0, len(hwid), 4)])


if __name__ == "__main__":
    print("Hardware ID Information")
    print("=" * 50)
    print(f"MAC Address: {get_mac_address()}")
    print(f"Machine ID:  {get_machine_id()}")
    print(f"Platform:    {platform.system()}")
    print("=" * 50)
    print(f"Generated HWID: {generate_hwid()}")
    print(f"Display Format: {get_hwid_display()}")
