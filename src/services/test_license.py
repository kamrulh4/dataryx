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
