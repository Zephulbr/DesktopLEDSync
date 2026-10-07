import importlib.util
import os
import shutil
import subprocess
import sys
import customtkinter as ctk
from version import __version__

print("--- Standalone LED Sync Builder ---")
print("Installing build dependencies...")
subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)

# Delete old build folders if they exist
print("\nCleaning up old builds...")
for folder in ("build", "dist"):
    shutil.rmtree(folder, ignore_errors=True)
if os.path.exists("DesktopLEDSync.spec"):
    os.remove("DesktopLEDSync.spec")

# Stamp the version into the .exe so it shows under Properties -> Details
version_numbers = tuple(int(part) for part in __version__.split(".")) + (0,) * (4 - len(__version__.split(".")))
os.makedirs("build", exist_ok=True)
version_file = os.path.join("build", "version_info.txt")
with open(version_file, "w", encoding="utf-8") as f:
    f.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={version_numbers}, prodvers={version_numbers}),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('ProductName', 'Desktop LED Sync'),
      StringStruct('FileDescription', 'Desktop LED Sync'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('ProductVersion', '{__version__}'),
      StringStruct('OriginalFilename', 'DesktopLEDSync.exe'),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""")

print(f"\nPackaging the application (version {__version__})...")
cmd = [
    sys.executable, "-m", "PyInstaller",
    "--name", "DesktopLEDSync",
    "--onefile",
    "--noconsole", # Don't show the black DOS command prompt window anymore
    "--version-file", version_file,

    # config.json is deliberately NOT bundled: it holds your account details, and the app
    # reads/creates it in %APPDATA%\DesktopLEDSync on first run anyway.

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
