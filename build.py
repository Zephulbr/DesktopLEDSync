import importlib.util
import os
import shutil
import subprocess
import sys
import customtkinter as ctk
from version import __version__


def find_iscc():
    """Locate Inno Setup compiler executable (ISCC.exe)."""
    iscc = shutil.which("iscc") or shutil.which("ISCC")
    if iscc:
        return iscc
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")
    candidates = [
        os.path.join(local_app_data, "Programs", "Inno Setup 6", "ISCC.exe"),
        os.path.join(local_app_data, "Programs", "Inno Setup 7", "ISCC.exe"),
        os.path.join(program_files, "Inno Setup 6", "ISCC.exe"),
        os.path.join(program_files_x86, "Inno Setup 6", "ISCC.exe"),
        os.path.join(program_files, "Inno Setup 7", "ISCC.exe"),
        os.path.join(program_files_x86, "Inno Setup 7", "ISCC.exe"),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


only_installer = "--only-installer" in sys.argv
skip_installer = "--no-installer" in sys.argv

if not only_installer:
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
        "--noconsole",  # Don't show the black DOS command prompt window anymore
        "--version-file", version_file,
        "--icon", os.path.join("assets", "app_icon.ico"),

        # config.json is deliberately NOT bundled: it holds your account details, and the app
        # reads/creates it in %APPDATA%\DesktopLEDSync on first run anyway.

        # CustomTkinter needs its theme files explicitly bundled in Windows
        "--add-data", f"{os.path.dirname(ctk.__file__)};customtkinter",
        "--add-data", "assets;assets",
    ]

    # The Windows Runtime projections are namespace packages that PyInstaller can't fully trace
    for winrt_package in ("winrt", "winsdk"):
        if importlib.util.find_spec(winrt_package):
            cmd += ["--collect-submodules", winrt_package]

    cmd.append("gui.py")

    subprocess.run(cmd, check=True)

    print("\n--- STANDALONE BUILD COMPLETE ---")
    print("Your executable is located in the 'dist' folder:")
    print("  dist/DesktopLEDSync.exe")

if not skip_installer:
    exe_path = os.path.join("dist", "DesktopLEDSync.exe")
    if not os.path.exists(exe_path):
        print(f"\n[Error] Cannot build installer: {exe_path} not found. Run full build first.")
        sys.exit(1)

    iscc_path = find_iscc()
    if iscc_path and os.path.exists("installer.iss"):
        print(f"\nBuilding Windows Installer using Inno Setup ({iscc_path})...")
        subprocess.run([iscc_path, f"/DAppVersion={__version__}", "installer.iss"], check=True)
        print("\n--- INSTALLER COMPLETE ---")
        print("Your installer is located in the 'dist' folder:")
        print("  dist/DesktopLEDSync-Setup.exe")
    elif not iscc_path:
        print("\n[Notice] Inno Setup (ISCC.exe) not found. Skipped creating installer.")
        print("To build the installer, install Inno Setup: winget install JRSoftware.InnoSetup")
