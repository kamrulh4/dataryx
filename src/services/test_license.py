import os
import sys
from pathlib import Path

# Add src to python path to run standalone
current_dir = Path(__file__).parent.resolve()
sys.path.insert(0, str(current_dir.parent))

from services.hwid import generate_hwid, get_hwid_display
from services.license_validator import check_license, get_trial_info, _get_trial_path, start_trial

def test_licensing():
    print("Running Licensing Tests...")
    
    # 1. HWID tests
    hwid = generate_hwid()
    display_hwid = get_hwid_display()
    print(f"Generated HWID: {hwid}")
    print(f"Display HWID: {display_hwid}")
    assert len(hwid) == 64, "HWID should be a SHA256 hex string"
    assert len(display_hwid.split("-")) == 16, "Display HWID format should have 16 groups"

    # 2. Trial file setup and checks
    trial_path = _get_trial_path()
    print(f"Trial path: {trial_path}")
    
    # Backup existing trial if it exists
    backup_path = trial_path + ".backup"
    has_backup = False
    if os.path.exists(trial_path):
        os.rename(trial_path, backup_path)
        has_backup = True
        print("Backed up existing trial file.")
        
    try:
        # Start a new trial
        info = start_trial()
        print("Trial started successfully.")
        print(f"Trial Info: {info}")
        assert info["is_trial"] is True
        assert info["days_left"] == 90
        
        # Verify check_license returns True
        is_licensed, err = check_license()
        print(f"License check status: {is_licensed}, Error message: {err}")
        assert is_licensed is True, "Newly initialized trial should be valid"
        
        # Verify active trial info is found
        retrieved_info = get_trial_info()
        print(f"Retrieved Trial Info: {retrieved_info}")
        assert retrieved_info is not None
        assert retrieved_info["days_left"] in (89, 90)
        
        # 3. Paid License test
        # Add root src path directly to avoid executing src/__init__.py
        root_dir = Path(__file__).parent.resolve().parent.parent.parent
        sys.path.insert(0, str(root_dir / "src"))
        
        try:
            from keygen import PRIVATE_KEY_PATH, generate_license_key
        except Exception:
            print("Skipping paid license tests (root keygen.py not accessible).")
            PRIVATE_KEY_PATH = None
            
        from cryptography.hazmat.primitives import serialization
        from services.license_validator import activate_license, get_license_info, LICENSE_FILE, get_license_dir
        
        if PRIVATE_KEY_PATH and os.path.exists(PRIVATE_KEY_PATH):
            # Backup existing license file if it exists
            license_dat = os.path.join(get_license_dir(), LICENSE_FILE)
            has_license_backup = False
            license_backup_path = license_dat + ".backup"
            if os.path.exists(license_dat):
                os.rename(license_dat, license_backup_path)
                has_license_backup = True
                print("Backed up existing license file.")
                
            try:
                with open(PRIVATE_KEY_PATH, 'rb') as f:
                    priv_key = serialization.load_pem_private_key(f.read(), password=None)
                    
                # Generate key for current HWID
                paid_key = generate_license_key(priv_key, hwid, "Test Paid User", "Pro", 365)
                
                # Activate license
                success, msg = activate_license(paid_key)
                assert success is True, f"Activation failed: {msg}"
                
                # Verify license checks pass
                is_licensed, err = check_license()
                assert is_licensed is True, f"Paid license validation failed: {err}"
                
                info = get_license_info()
                assert info is not None
                assert info["is_trial"] is False
                assert info["customer_name"] == "Test Paid User"
                assert info["tier"] == "Pro"
                print("Paid License verification passed.")
            finally:
                # Cleanup test license
                if os.path.exists(license_dat):
                    os.remove(license_dat)
                # Restore backup
                if has_license_backup:
                    os.rename(license_backup_path, license_dat)
                    print("Restored original license file.")
        
    finally:
        # Cleanup test trial file
        if os.path.exists(trial_path):
            os.remove(trial_path)
        # Restore backup
        if has_backup:
            os.rename(backup_path, trial_path)
            print("Restored original trial file.")
            
    print("All tests passed successfully!")

if __name__ == "__main__":
    test_licensing()
