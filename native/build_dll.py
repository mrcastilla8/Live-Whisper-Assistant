# -*- coding: utf-8 -*-
"""
build_dll.py -- Compila process_loopback.cpp -> process_loopback.dll

Detecta automáticamente MSVC (cl.exe) o MinGW-w64 (64-bit g++).
El MinGW clásico de 32-bit (mingw32) NO es compatible — lo rechaza.

Uso:
    python build_dll.py
"""

import subprocess
import shutil
import sys
import os
from pathlib import Path

HERE     = Path(__file__).parent
CPP_FILE = HERE / "process_loopback.cpp"
DLL_OUT  = HERE / "process_loopback.dll"


def get_compiler_info(compiler: str):
    """Retorna info del compilador o None si no existe."""
    path = shutil.which(compiler)
    if not path:
        return None
    try:
        r = subprocess.run([compiler, "-dumpmachine"],
                           capture_output=True, text=True, timeout=5)
        machine = r.stdout.strip()
    except Exception:
        machine = ""
    return {"path": path, "machine": machine}


def find_compiler() -> tuple[str, list[str]]:
    """
    Retorna (binario, flags) para un compilador compatible.
    Lanza RuntimeError si ninguno sirve.
    """
    # ── MSVC ──────────────────────────────────────────────────────────────────
    for name in ("cl", "cl.exe"):
        if shutil.which(name):
            flags = [
                "/LD", "/O2", "/EHsc", "/std:c++17",
                str(CPP_FILE),
                "/link", "Ole32.lib", "Avrt.lib",
                f"/OUT:{DLL_OUT}",
            ]
            return name, flags

    # ── MinGW-w64 64-bit ──────────────────────────────────────────────────────
    # Buscar primero en la ruta instalada por install_mingw.py
    user_mingw_bin = Path.home() / ".mingw64" / "mingw64" / "bin"
    user_gpp = user_mingw_bin / "g++.exe"
    candidates = []
    if user_gpp.exists():
        candidates.append(str(user_gpp))
    candidates += ["x86_64-w64-mingw32-g++", "g++"]

    for candidate in candidates:
        cpath = Path(candidate)
        if cpath.is_absolute():
            if not cpath.exists():
                continue
            name = candidate
            # Inyectar el bin de MinGW-w64 en PATH para el linker
            os.environ["PATH"] = str(user_mingw_bin) + os.pathsep + os.environ.get("PATH", "")
        else:
            info = get_compiler_info(candidate)
            if not info:
                continue
            machine = info["machine"]
            if "mingw32" in machine and "x86_64" not in machine:
                print(f"[build_dll] AVISO: '{candidate}' es 32-bit ({machine}), se necesita 64-bit.")
                print("            Ejecuta:  python native/install_mingw.py")
                continue
            name = candidate

        flags = [
            "-shared", "-O2", "-std=c++11",
            "-Wno-attributes",
            "-o", str(DLL_OUT),
            str(CPP_FILE),
            "-lole32",
            "-static",
            "-static-libgcc", "-static-libstdc++",
        ]
        return name, flags

    raise RuntimeError(
        "No se encontró compilador C++ compatible (64-bit).\n\n"
        "Solución rápida (sin admin):\n"
        "    python native/install_mingw.py\n\n"
        "Alternativa manual — MinGW-w64:\n"
        "    https://winlibs.com/  → descarga la versión UCRT x86_64 con Pthreads\n\n"
        "Alternativa manual — Visual Studio Build Tools:\n"
        "    https://visualstudio.microsoft.com/visual-cpp-build-tools/"
    )


def build() -> None:
    compiler, flags = find_compiler()
    print(f"[build_dll] Usando compilador : {compiler}")
    print(f"[build_dll] Compilando         : {CPP_FILE.name}")
    print(f"[build_dll] Salida             : {DLL_OUT}\n")

    cmd = [compiler, *flags]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(HERE))

    if result.stdout.strip():
        print(result.stdout)
    if result.stderr.strip():
        print(result.stderr, file=sys.stderr)

    if result.returncode != 0:
        print(f"\n[build_dll] ✗ Compilación fallida (código {result.returncode})",
              file=sys.stderr)
        sys.exit(result.returncode)

    if DLL_OUT.exists():
        size_kb = DLL_OUT.stat().st_size // 1024
        print(f"[build_dll] OK DLL generada: {DLL_OUT}  ({size_kb} KB)")
    else:
        print("[build_dll] ✗ No se encontró el archivo de salida.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    build()
