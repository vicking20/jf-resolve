import os
import sys
import subprocess
import platform
import shutil
import datetime
import psutil

# ========= CONFIGURATION =========
EXCLUDE_FILES = [".env", "config.ini"]
BACKUP_DIR = "backup"
BRANCH = "main"
# =================================

def check_git():
    """Check if git is available."""
    try:
        subprocess.run(["git", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ Git is not installed or not found in PATH.")
        return False

def backup_files():
    """Back up environment and config files before updating."""
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_dir = os.path.join(BACKUP_DIR, timestamp)
    os.makedirs(dest_dir, exist_ok=True)

    for fname in EXCLUDE_FILES:
        if os.path.exists(fname):
            shutil.copy2(fname, dest_dir)
            print(f"📦 Backed up {fname} -> {dest_dir}/")
        else:
            print(f"⚠️  Skipping missing file: {fname}")

    print(f"\n✅ Backup complete. Files saved in: {dest_dir}\n")
    return dest_dir

def update_via_git():
    """Perform git update (fetch/reset)."""
    print("\n🔄 Updating jf_resolve from GitHub...\n")
    if not check_git():
        print("⚠️ Update aborted because Git is not available.")
        return False

    try:
        subprocess.run(["git", "fetch", "--all"], check=True)
        subprocess.run(["git", "reset", "--hard", f"origin/{BRANCH}"], check=True)
        print("\n✅ Update completed successfully.\n")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Git update failed: {e}")
        return False

def find_running_controller():
    """Find any running controller.py processes"""
    running_pids = []
    current_pid = os.getpid()
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info['cmdline']
            if cmdline and 'controller.py' in ' '.join(cmdline):
                if proc.info['pid'] != current_pid:
                    running_pids.append((proc.info['pid'], proc))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return running_pids

def stop_running_controllers():
    """Stop any running controller.py instances"""
    running = find_running_controller()
    if not running:
        print("✅ No running controller.py instances found.")
        return True

    print(f"\n⚙️ Found {len(running)} running controller.py instance(s):")
    for pid, proc in running:
        try:
            print(f"  PID {pid}: {' '.join(proc.cmdline())}")
        except:
            print(f"  PID {pid}")

    for pid, proc in running:
        try:
            print(f"🛑 Stopping PID {pid}...")
            proc.terminate()
            proc.wait(timeout=5)
            print(f"  ✓ Stopped PID {pid}")
        except psutil.TimeoutExpired:
            print(f"  Force killing PID {pid}...")
            proc.kill()
            print(f"  ✓ Killed PID {pid}")
        except Exception as e:
            print(f"  ✗ Failed to stop PID {pid}: {e}")
    return True

def install_requirements(requirements_file="requirements.txt", target_dir="libs"):
    """Reinstall dependencies"""
    if not os.path.exists(requirements_file):
        print(f"⚠️ {requirements_file} not found, skipping dependency installation.")
        return False
    print(f"\n📦 Installing requirements to ./{target_dir}...\n")
    os.makedirs(target_dir, exist_ok=True)

    commands = [
        [sys.executable, "-m", "pip", "install", "-r", requirements_file, "--target", target_dir],
        ["pip3", "install", "-r", requirements_file, "--target", target_dir],
    ]
    for cmd in commands:
        try:
            subprocess.check_call(cmd)
            print("\n✅ Dependencies installed successfully.\n")
            return True
        except subprocess.CalledProcessError:
            continue
    print("❌ Failed to install requirements.")
    return False

def run_controller():
    """Run the controller process interactively"""
    print("\n🚀 Launching Media Controller...\n")
    system_platform = platform.system()

    if system_platform != "Windows":
        mode = input("Run in foreground or background? [fg/bg]: ").strip().lower()
        if mode == "bg":
            with open("media_controller.log", "a") as out, open("media_controller.err", "a") as err:
                process = subprocess.Popen(
                    ["nohup", sys.executable, "controller.py", "--initiate"],
                    stdout=out,
                    stderr=err,
                    stdin=subprocess.DEVNULL,
                    preexec_fn=os.setpgrp,
                )
            print(f"✅ Running in background with PID {process.pid}. Log: media_controller.log")
        else:
            subprocess.run([sys.executable, "controller.py", "--initiate"])
    else:
        creationflags = subprocess.CREATE_NEW_CONSOLE
        subprocess.Popen(
            [sys.executable, "controller.py", "--initiate"],
            creationflags=creationflags,
        )
        print("✅ Running controller in new Windows console.")

def menu():
    while True:
        print("\n=== jf_resolve Maintenance Menu ===")
        print("1. 🔄 Update jf_resolve (backup, git pull, reinstall, restart)")
        print("2. 🛑 Stop jf_resolve (controller)")
        print("3. 🚀 Run jf_resolve")
        print("4. ❌ Exit")
        choice = input("\nSelect an option [1-4]: ").strip()

        if choice == "1":
            stop_running_controllers()
            backup_files()
            if update_via_git():
                install_requirements()
                run_controller()
        elif choice == "2":
            stop_running_controllers()
        elif choice == "3":
            run_controller()
        elif choice == "4":
            print("Goodbye!")
            sys.exit(0)
        else:
            print("Invalid choice. Try again.")

def main():
    menu()

if __name__ == "__main__":
    main()
