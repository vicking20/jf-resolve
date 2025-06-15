import sys
import os
import subprocess

sys.path.insert(0, os.path.abspath("libs"))

def print_header():
    print("=" * 50)
    print("Welcome to the Jf-resolve installer")
    print("=" * 50)
    input("Press enter to continue...")

def prompt_user_for_install():
    while True:
        answer = input("Have you installed the dependencies before? (y/n): ").strip().lower()
        if answer in ("y", "yes"):
            return False  # Don't install
        elif answer in ("n", "no"):
            return True  # Run install
        else:
            print("Please enter 'y' or 'n'.")

def install_requirements(requirements_file="requirements.txt", target_dir="libs"):
    if not os.path.exists(requirements_file):
        print(f"Error: {requirements_file} not found.")
        return False
    print(f"\nInstalling requirements to ./{target_dir}...\n")
    os.makedirs(target_dir, exist_ok=True)
    pip_commands = [
        [sys.executable, "-m", "pip", "install", "-r", requirements_file, "--target", target_dir],
        ["pip3", "install", "-r", requirements_file, "--target", target_dir],
    ]
    for cmd in pip_commands:
        try:
            print(f"Trying: {' '.join(cmd)}")
            subprocess.check_call(cmd)
            print("\nDependencies installed successfully.")
            return True
        except subprocess.CalledProcessError as e:
            print(f"Failed: {' '.join(cmd)}")
    print("\nAll installation methods failed.")
    print("Try running manually:\n  pip install --target=libs -r requirements.txt")
    return False

def try_import_setup():
    try:
        import setup
        return setup
    except ImportError as e:
        print("\nFailed to import 'setup'. Make sure it exists and all dependencies are available.")
        print(f"Details: {e}")
        return None

def main():
    print_header()
    should_install = prompt_user_for_install()
    if should_install:
        success = install_requirements()
        if not success:
            sys.exit(1)
    setup = try_import_setup()
    if not setup:
        sys.exit(1)
    try:
        setup.main()
    except Exception as e:
        print("\nAn error occurred while running setup.main():")
        print(e)
        sys.exit(1)

if __name__ == "__main__":
    main()
