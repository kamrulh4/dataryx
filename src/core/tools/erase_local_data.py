import os
import shutil
import sys
from pathlib import Path

def erase_all_data():
    """Wipes all local Dataryx data for a fresh start."""
    project_root = Path(__file__).parent.parent
    
    # List of files and directories to remove
    to_remove = [
        project_root / "dataryx.db",
        project_root / "dataryx_data",
        project_root / "dataryx_internal_storage",
        project_root / "saved_flows",
        project_root / "core" / "saved_flows",
    ]
    
    print("Starting Dataryx local data erasure...")
    
    for item in to_remove:
        if not item.exists():
            print(f"Skipping {item.name} (not found)")
            continue
            
        try:
            if item.is_file():
                item.unlink()
                print(f"REMOVED file: {item.name}")
            elif item.is_dir():
                shutil.rmtree(item)
                # Re-create the empty directory if it's expected by the app
                item.mkdir(parents=True, exist_ok=True)
                print(f"CLEARED directory: {item.name}")
        except Exception as e:
            print(f"ERROR removing {item}: {e}")

    print("\nSUCCESS: All local data has been erased. You can now restart your containers.")

if __name__ == "__main__":
    confirm = input("This will PERMANENTLY delete all local flows and user accounts. Are you sure? (y/N): ")
    if confirm.lower() == 'y':
        erase_all_data()
    else:
        print("Aborted.")
