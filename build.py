import importlib.util
import os
import shutil
import subprocess
import sys
import customtkinter as ctk

print("--- Standalone LED Sync Builder ---")
print("Installing build dependencies...")
subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)

# Delete old build folders if they exist
print("\nCleaning up old builds...")
for folder in ("build", "dist"):
    shutil.rmtree(folder, ignore_errors=True)
if os.path.exists("DesktopLEDSync.spec"):
    os.remove("DesktopLEDSync.spec")

print("\nPackaging the application...")
cmd = [
    sys.executable, "-m", "PyInstaller",
    "--name", "DesktopLEDSync",
    "--onefile",
    "--noconsole", # Don't show the black DOS command prompt window anymore

    # config.json is deliberately NOT bundled: it holds your account details, and the app
    # reads/creates it next to the .exe on first run anyway.

    # CustomTkinter needs its theme files explicitly bundled in Windows
    "--add-data", f"{os.path.dirname(ctk.__file__)};customtkinter",
]

# The Windows Runtime projections are namespace packages that PyInstaller can't fully trace
for winrt_package in ("winrt", "winsdk"):
    if importlib.util.find_spec(winrt_package):
        cmd += ["--collect-submodules", winrt_package]

cmd.append("gui.py")

subprocess.run(cmd, check=True)

print("\n--- BUILD COMPLETE ---")
print("Your executable is located in the 'dist' folder!")
print("You can double-click 'DesktopLEDSync.exe' to run the app.")
