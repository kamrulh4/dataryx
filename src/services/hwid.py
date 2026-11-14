"""
Hardware ID (HWID) generation module.
Generates a unique hardware fingerprint based on system characteristics.
"""
import functools
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
            try:
                import winreg
                key = winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Microsoft\Cryptography",
                    0,
                    winreg.KEY_READ | winreg.KEY_WOW64_64KEY
                )
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
                winreg.CloseKey(key)
                if value:
                    return value.strip()
            except Exception:
                pass
            
            # Fallback to subprocess if winreg fails
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


def _parse_smbios_uuid(raw: bytes) -> str:
    """Find the System UUID (SMBIOS type 1) in a raw SMBIOS table dump.

    raw is the buffer returned by GetSystemFirmwareTable('RSMB'): an 8-byte
    header followed by the SMBIOS structures.
    """
    if len(raw) < 8:
        return ""
    table_len = int.from_bytes(raw[4:8], "little")
    data = raw[8:8 + table_len]
    i = 0
    while i + 4 <= len(data):
        s_type, s_len = data[i], data[i + 1]
        if s_len < 4:
            break
        if s_type == 1 and s_len >= 0x18 and i + 0x18 <= len(data):
            u = data[i + 8:i + 0x18]
            # All-zero / all-FF means "not set" by the vendor
            if u in (b"\x00" * 16, b"\xff" * 16):
                return ""
            # Same byte order Windows uses for Win32_ComputerSystemProduct.UUID
            return str(uuid.UUID(bytes_le=bytes(u))).upper()
        if s_type == 127:  # end-of-table
            break
        # Skip the formatted area, then the string area (ends with two NULs)
        j = i + s_len
        while j + 1 < len(data) and not (data[j] == 0 and data[j + 1] == 0):
            j += 1
        i = j + 2
    return ""


def get_bios_uuid() -> str:
    """Get the motherboard/BIOS system UUID.

    Unlike the MAC address, this does not change when Wi-Fi, LAN, VPN or a
    dock is connected/disconnected. Read without spawning any process so the
    result is the same on every call.
    """
    try:
        system = platform.system()
        if system == "Windows":
            import ctypes
            gsft = ctypes.windll.kernel32.GetSystemFirmwareTable
            gsft.restype = ctypes.c_uint
            gsft.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
            rsmb = int.from_bytes(b"RSMB", "big")
            size = gsft(rsmb, 0, None, 0)
            if size == 0:
                return ""
            buf = ctypes.create_string_buffer(size)
            if gsft(rsmb, 0, buf, size) == 0:
                return ""
            return _parse_smbios_uuid(buf.raw)
        if system == "Darwin":
            import re
            import subprocess
            out = subprocess.run(
                ["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            m = re.search(r'"IOPlatformUUID"\s*=\s*"([0-9A-Fa-f-]+)"', out)
            return m.group(1).upper() if m else ""
    except Exception:
        pass
    return ""


@functools.lru_cache(maxsize=1)
def generate_hwid() -> str:
    """
    Generate a unique hardware ID from the BIOS UUID + machine-id.

    The MAC address is no longer used: on Windows uuid.getnode() picks
    whichever network adapter it finds first, which changes when Wi-Fi, LAN or
    VPN come up in a different order, so the license stopped matching.
    The MAC is only used as a last resort if neither stable ID is available.

    Returns:
        str: A SHA256 hash representing the unique hardware fingerprint.
    """
    bios_uuid = get_bios_uuid()
    machine_id = get_machine_id()
    if not bios_uuid and not machine_id:
        machine_id = get_mac_address()
    hw_string = f"v2|{bios_uuid}|{machine_id}|{platform.system()}"
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
    print(f"BIOS UUID:   {get_bios_uuid()}")
    print(f"Machine ID:  {get_machine_id()}")
    print(f"Platform:    {platform.system()}")
    print("=" * 50)
    print(f"Generated HWID: {generate_hwid()}")
    print(f"Display Format: {get_hwid_display()}")
