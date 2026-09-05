"""
test_process_loopback.py
Script de prueba para verificar la captura de audio por proceso (Application Loopback).

Uso:
    python test_process_loopback.py                    # Lista procesos con audio
    python test_process_loopback.py --pid 1234         # Captura PID específico
    python test_process_loopback.py --name chrome      # Captura por nombre de proceso
    python test_process_loopback.py --pid 1234 --wav   # Guarda resultado en WAV
    python test_process_loopback.py --pid 1234 --secs 10  # Captura N segundos

Requisitos:
    - Windows 10 Build 19041+ o Windows 11
    - La DLL compilada (ejecutar python native/build_dll.py primero)
    - psutil instalado: pip install psutil
"""

import argparse
import logging
import sys
import time
import wave
from pathlib import Path
from typing import Optional

import numpy as np

# Configurar logging antes de los imports locales
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("test_process_loopback")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Prueba de captura de audio por proceso (WASAPI Application Loopback)"
    )
    p.add_argument("--pid",  type=int,  default=None, help="PID del proceso a capturar")
    p.add_argument("--name", type=str,  default=None,
                   help="Nombre parcial del proceso (ej. 'chrome', 'spotify')")
    p.add_argument("--secs", type=float, default=5.0,
                   help="Segundos a capturar (default: 5)")
    p.add_argument("--wav",  action="store_true",
                   help="Guardar el audio capturado en test_process_loopback.wav")
    p.add_argument("--no-tree", action="store_true",
                   help="No incluir procesos hijos del PID objetivo")
    p.add_argument("--list", action="store_true",
                   help="Listar procesos activos con nombre y PID, luego salir")
    return p.parse_args()


def find_pid_by_name(name_fragment: str) -> Optional[int]:
    """
    Busca el PID del primer proceso cuyo nombre contenga name_fragment.
    """
    try:
        import psutil
    except ImportError:
        logger.error("psutil no instalado. Instálalo con: pip install psutil")
        return None

    name_lower = name_fragment.lower()
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if name_lower in proc.info["name"].lower():
                logger.info(
                    f"Proceso encontrado: {proc.info['name']} (PID={proc.info['pid']})"
                )
                return proc.info["pid"]
        except (Exception,):
            continue
    logger.error(f"No se encontró ningún proceso con nombre '{name_fragment}'")
    return None


def list_running_processes() -> None:
    """Imprime todos los procesos en ejecución ordenados por nombre."""
    try:
        import psutil
    except ImportError:
        print("psutil no instalado. Instálalo con: pip install psutil")
        return

    procs = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            procs.append((proc.info["name"], proc.info["pid"]))
        except Exception:
            continue

    procs.sort(key=lambda x: x[0].lower())
    print(f"\n{'NOMBRE':<40}{'PID':>8}")
    print("-" * 50)
    for name, pid in procs:
        print(f"{name:<40}{pid:>8}")
    print(f"\nTotal: {len(procs)} procesos")


def main() -> None:
    args = parse_args()

    # ── Listar procesos y salir ───────────────────────────────────────────────
    if args.list:
        list_running_processes()
        return

    # ── Resolver PID ──────────────────────────────────────────────────────────
    pid: Optional[int] = args.pid

    if pid is None and args.name:
        pid = find_pid_by_name(args.name)

    if pid is None:
        logger.error(
            "Debes indicar un PID (--pid) o un nombre de proceso (--name).\n"
            "Usa --list para ver los procesos disponibles."
        )
        sys.exit(1)

    logger.info(f"Objetivo: PID={pid}, duración={args.secs}s, "
                f"include_tree={not args.no_tree}")

    # ── Importar el wrapper ───────────────────────────────────────────────────
    # Añadir el directorio raíz al path
    sys.path.insert(0, str(Path(__file__).parent))

    try:
        from process_capture import ProcessAudioCapture
    except FileNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)

    # ── Capturar ──────────────────────────────────────────────────────────────
    cap = ProcessAudioCapture(
        pid=pid,
        include_tree=not args.no_tree,
        chunk_duration=0.5,
        sample_rate=16_000,
    )

    collected_chunks: list[np.ndarray] = []
    start_wall = time.time()
    total_rms_vals: list[float] = []

    logger.info(f"Iniciando captura de {args.secs}s del proceso PID={pid}...")
    print(f"\n>>> Capturando {args.secs}s del proceso PID={pid}...\n")

    try:
        for chunk in cap.stream():
            elapsed = time.time() - start_wall
            if elapsed >= args.secs:
                break

            rms = float(np.sqrt(np.mean(chunk ** 2)))
            total_rms_vals.append(rms)

            # Barra visual simple
            bar_len = int(rms * 200)
            bar = "█" * min(bar_len, 40)
            print(f"\r  [{elapsed:5.1f}s] RMS={rms:.5f}  {bar:<40}", end="", flush=True)

            collected_chunks.append(chunk)

    except KeyboardInterrupt:
        logger.info("Interrupción por usuario.")
    finally:
        cap.stop()

    print()  # nueva línea tras la barra

    # ── Resultados ────────────────────────────────────────────────────────────
    if not collected_chunks:
        logger.error("No se capturó ningún chunk. Verifica el PID y que el proceso emita audio.")
        sys.exit(1)

    total_audio = np.concatenate(collected_chunks)
    duration_real = len(total_audio) / 16_000
    avg_rms = float(np.mean(total_rms_vals)) if total_rms_vals else 0.0

    print("\n--- Resumen -----------------------------------------------")
    print(f"  Muestras capturadas : {len(total_audio):,}")
    print(f"  Duración real       : {duration_real:.2f}s")
    print(f"  RMS promedio        : {avg_rms:.5f}")
    print(f"  RMS máximo          : {max(total_rms_vals):.5f}" if total_rms_vals else "")
    print(f"  Chunks              : {len(collected_chunks)}")

    has_audio = avg_rms > 0.001
    print(f"\n  {'Audio detectado correctamente!' if has_audio else 'Audio muy bajo o silencio. ¿El proceso está reproduciendo audio?'}")

    # ── Guardar WAV si se solicitó ────────────────────────────────────────────
    if args.wav:
        wav_path = Path(__file__).parent / "test_process_loopback.wav"
        pcm_int16 = (total_audio * 32767).clip(-32768, 32767).astype(np.int16)
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(16_000)
            wf.writeframes(pcm_int16.tobytes())
        print(f"\n  WAV guardado en: {wav_path}")
        print("  Abre el archivo en Audacity o VLC para verificar el aislamiento.")

    print()


if __name__ == "__main__":
    main()
