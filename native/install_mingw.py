# -*- coding: utf-8 -*-
"""
install_mingw.py
Descarga e instala MinGW-w64 (compilador C/C++ para Windows) de forma silenciosa,
lo añade al PATH del usuario y compila la DLL automáticamente.

Uso:
    python native/install_mingw.py

Nota: No requiere privilegios de administrador (instalación solo para el usuario).
"""

import os
import sys
import zipfile
import urllib.request
import subprocess
import shutil
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO,
                    format="[%(levelname)s] %(message)s")
logger = logging.getLogger("install_mingw")

# ── Configuración ─────────────────────────────────────────────────────────────

# WinLibs MinGW-w64 (GCC 14, UCRT, POSIX, x64) — sin instalador, solo ZIP
MINGW_URL = (
    "https://github.com/brechtsanders/winlibs_mingw/releases/download/"
    "14.2.0posix-18.1.8-12.0.0-ucrt-r1/"
    "winlibs-x86_64-posix-seh-gcc-14.2.0-mingw-w64ucrt-12.0.0-r1.zip"
)

# Directorio de instalación (dentro del home del usuario, sin admin)
INSTALL_DIR = Path.home() / ".mingw64"
MINGW_BIN   = INSTALL_DIR / "mingw64" / "bin"
ZIP_PATH    = INSTALL_DIR / "mingw.zip"

HERE = Path(__file__).parent
BUILD_SCRIPT = HERE / "build_dll.py"


def download_mingw() -> None:
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"Descargando MinGW-w64 desde:\n  {MINGW_URL}")
    logger.info("Esto puede tardar 2-5 minutos dependiendo de tu conexión...")

    def progress(count: int, block_size: int, total: int) -> None:
        downloaded = count * block_size
        pct = min(downloaded / total * 100, 100) if total > 0 else 0
        mb = downloaded / 1_048_576
        print(f"\r  {pct:5.1f}%  {mb:.1f} MB descargados", end="", flush=True)

    urllib.request.urlretrieve(MINGW_URL, ZIP_PATH, reporthook=progress)
    print()  # nueva línea
    logger.info(f"Descarga completa: {ZIP_PATH}")


def extract_mingw() -> None:
    logger.info(f"Extrayendo en {INSTALL_DIR}...")
    with zipfile.ZipFile(ZIP_PATH, "r") as zf:
        zf.extractall(INSTALL_DIR)
    ZIP_PATH.unlink()  # Borrar el zip para ahorrar espacio
    logger.info("Extracción completa.")


def add_to_user_path() -> None:
    """
    Añade MINGW_BIN al PATH permanente del usuario (sin admin)
    usando el registro de Windows vía PowerShell.
    """
    bin_str = str(MINGW_BIN)
    logger.info(f"Añadiendo al PATH del usuario: {bin_str}")

    ps_cmd = (
        f'$current = [System.Environment]::GetEnvironmentVariable("PATH", "User"); '
        f'if ($current -notlike "*{bin_str}*") {{'
        f'  [System.Environment]::SetEnvironmentVariable("PATH", "$current;{bin_str}", "User")'
        f'  Write-Host "PATH actualizado"'
        f'}} else {{'
        f'  Write-Host "Ya estaba en el PATH"'
        f'}}'
    )

    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_cmd],
        capture_output=True, text=True
    )
    print(result.stdout.strip())
    if result.returncode != 0:
        logger.warning(f"No se pudo actualizar el PATH automáticamente: {result.stderr}")
        logger.warning(f"Añade manualmente al PATH: {bin_str}")

    # También actualizar el PATH de esta sesión
    os.environ["PATH"] = bin_str + os.pathsep + os.environ.get("PATH", "")


def verify_compiler() -> bool:
    """True solo si hay un g++ de 64-bit disponible."""
    gpp = shutil.which("g++")
    if not gpp:
        return False
    try:
        r = subprocess.run(["g++", "-dumpmachine"],
                           capture_output=True, text=True, timeout=5)
        machine = r.stdout.strip()
        return "x86_64" in machine
    except Exception:
        return False


def build_dll() -> None:
    logger.info("Compilando process_loopback.dll...")
    result = subprocess.run(
        [sys.executable, str(BUILD_SCRIPT)],
        capture_output=False,  # Mostrar salida al usuario
        env={**os.environ, "PATH": str(MINGW_BIN) + os.pathsep + os.environ.get("PATH", "")},
    )
    if result.returncode != 0:
        logger.error("La compilación falló. Revisa los errores arriba.")
        sys.exit(1)


def main() -> None:
    print("=== Instalador de MinGW-w64 para process_loopback ===")
    print()

    # Verificar si ya hay un g++ de 64-bit
    if verify_compiler():
        gpp_path = shutil.which("g++")
        logger.info(f"g++ 64-bit ya disponible en: {gpp_path}")
        logger.info("Procediendo directamente a compilar la DLL...")
        print()
        build_dll()
        return

    # Verificar si el g++ existente es de 32-bit (MinGW clasico)
    gpp_32 = shutil.which("g++")
    if gpp_32:
        logger.info(f"Se detecto g++ de 32-bit en {gpp_32} -- se necesita la version 64-bit.")

    # Verificar si MinGW-w64 ya fue descargado pero no esta en PATH
    if MINGW_BIN.exists() and (MINGW_BIN / "g++.exe").exists():
        logger.info("MinGW-w64 ya descargado, actualizando PATH...")
        add_to_user_path()
        build_dll()
        return

    # Descargar + extraer MinGW-w64
    try:
        download_mingw()
        extract_mingw()
    except Exception as e:
        logger.error(f"Error durante la descarga/extraccion: {e}")
        logger.error(
            "\nDescarga manual desde:\n"
            "  https://winlibs.com/\n"
            "Extrae el ZIP en cualquier carpeta y agrega la subcarpeta 'bin' al PATH."
        )
        sys.exit(1)

    add_to_user_path()

    if not verify_compiler():
        logger.warning(
            "\ng++ 64-bit no detectado en esta sesion de terminal.\n"
            "Cierra y vuelve a abrir la terminal, luego ejecuta:\n"
            "    python native/build_dll.py"
        )
        return

    build_dll()
    logger.info("\nInstalacion completa. La DLL esta lista.")


if __name__ == "__main__":
    main()
