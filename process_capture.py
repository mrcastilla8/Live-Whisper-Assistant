"""
process_capture.py
Wrapper Python para la DLL nativa process_loopback.dll.

Provee una clase ProcessAudioCapture con la misma interfaz que AudioCapture
(método stream() generador) para que pueda reemplazarla directamente en worker.py.

Uso básico:
    from process_capture import ProcessAudioCapture, list_audio_processes

    # Ver procesos que están produciendo audio
    for proc in list_audio_processes():
        print(proc)

    # Capturar audio del proceso con PID 1234
    cap = ProcessAudioCapture(pid=1234)
    for chunk in cap.stream():
        # chunk: numpy float32 array, mono, 16kHz
        ...
"""

import ctypes
import logging
import os
import sys
import time
import queue
import threading
import psutil
from pathlib import Path
from typing import Iterator, Optional

import numpy as np

logger = logging.getLogger(__name__)

def list_audio_processes():
    """
    Retorna una lista de procesos que podrían estar emitiendo audio.
    Filtra estrictamente procesos que devuelven un HWND válido, son visibles 
    y tienen un título de ventana no vacío.
    """
    import ctypes
    
    # EnumWindows callback
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
    
    unique_procs = {}
    
    def foreach_window(hwnd, lParam):
        # 1. Debe ser visible
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            
            # 2. No debe tener dueño (evita sub-ventanas flotantes)
            GW_OWNER = 4
            if ctypes.windll.user32.GetWindow(hwnd, GW_OWNER) != 0:
                return True
                
            # 3. No debe ser una ventana "Cloaked" (aplicaciones UWP en segundo plano en Win10/11)
            DWMWA_CLOAKED = 14
            is_cloaked = ctypes.c_int(0)
            try:
                ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(is_cloaked), ctypes.sizeof(is_cloaked))
                if is_cloaked.value != 0:
                    return True
            except AttributeError:
                pass # Si dwmapi no está disponible en versiones viejas de Windows, lo ignoramos
                
            # 4. Debe tener un título de texto no vacío
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
                title = buff.value
                
                # Ignorar títulos típicos de ventanas invisibles o de sistema
                if title in ["Program Manager", "Settings", "Microsoft Text Input Application"]:
                    return True
                    
                pid = ctypes.c_ulong()
                ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                
                # Ignorar PID 0 (System Idle) y PID 4 (System)
                if pid.value > 4:
                    try:
                        proc = psutil.Process(pid.value)
                        name = proc.name()
                        
                        # Excluir procesos básicos de Windows UI
                        if name.lower() not in ['explorer.exe', 'searchapp.exe', 'textinputhost.exe', 'dwm.exe']:
                            if pid.value not in unique_procs:
                                unique_procs[pid.value] = {
                                    'pid': pid.value,
                                    'name': name,
                                    'title': title
                                }
                            else:
                                # Evitar concatenar títulos redundantes
                                if title not in unique_procs[pid.value]['title']:
                                    unique_procs[pid.value]['title'] += f" | {title}"
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
        return True
    
    ctypes.windll.user32.EnumWindows(EnumWindowsProc(foreach_window), 0)

    # Ordenar alfabéticamente por título de ventana
    return sorted(unique_procs.values(), key=lambda x: x['title'].lower())

# ── Resolución de la DLL ──────────────────────────────────────────────────────

_DLL_NAME = "process_loopback.dll"

# Buscar la DLL en: 1) directorio de este módulo, 2) native/ relativo a este módulo
_HERE = Path(__file__).parent
_DLL_CANDIDATES = [
    _HERE / _DLL_NAME,
    _HERE / "native" / _DLL_NAME,
    Path(_DLL_NAME),  # PATH del sistema
]


def _find_dll() -> Path:
    for candidate in _DLL_CANDIDATES:
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError(
        f"No se encontró {_DLL_NAME}. "
        f"Ejecuta 'python native/build_dll.py' para compilarla primero.\n"
        f"Rutas buscadas: {[str(c) for c in _DLL_CANDIDATES]}"
    )


# ── Prototipo del callback C → Python ─────────────────────────────────────────

# void (*AudioDataCallback)(const float* data, uint32_t frames,
#                           uint32_t channels, uint32_t sample_rate)
_CALLBACK_TYPE = ctypes.CFUNCTYPE(
    None,
    ctypes.POINTER(ctypes.c_float),  # data
    ctypes.c_uint32,                 # frames
    ctypes.c_uint32,                 # channels
    ctypes.c_uint32,                 # sample_rate
)

# Frecuencia de salida requerida por Whisper
_WHISPER_SAMPLE_RATE = 16_000


class ProcessAudioCapture:
    """
    Captura el audio producido por un proceso específico de Windows
    usando WASAPI Application Loopback (Windows 10 Build 19041+).

    Interfaz compatible con AudioCapture: produce chunks numpy float32
    mono 16kHz listos para ser consumidos por AudioWorker.

    Args:
        pid:           Process ID de la aplicación a capturar.
        include_tree:  Si True, también captura procesos hijos del PID.
        chunk_duration: Duración en segundos de cada chunk yieldeado.
        sample_rate:   Frecuencia de salida (default 16000 para Whisper).
    """

    def __init__(
        self,
        pid: int,
        include_tree: bool = True,
        chunk_duration: float = 0.5,
        sample_rate: int = _WHISPER_SAMPLE_RATE,
    ):
        self.pid            = pid
        self.include_tree   = include_tree
        self.chunk_duration = chunk_duration
        self.sample_rate    = sample_rate
        self._running       = False

        # Cargar DLL
        dll_path = _find_dll()
        logger.info(f"Cargando DLL nativa: {dll_path}")
        self._lib = ctypes.CDLL(str(dll_path))

        # Configurar prototipos de las funciones exportadas
        self._lib.set_audio_callback.restype  = None
        self._lib.set_audio_callback.argtypes = [_CALLBACK_TYPE]

        self._lib.start_capture.restype  = ctypes.c_int
        self._lib.start_capture.argtypes = [ctypes.c_uint32, ctypes.c_int]

        self._lib.stop_capture.restype  = None
        self._lib.stop_capture.argtypes = []

        self._lib.is_running.restype  = ctypes.c_int
        self._lib.is_running.argtypes = []

        # Cola interna de frames crudos (float32 interleaved)
        self._frame_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)

        # Referencia permanente al callback para evitar que el GC lo libere
        self._callback_ref: Optional[_CALLBACK_TYPE] = None

        logger.info(
            f"ProcessAudioCapture listo: PID={pid}, "
            f"include_tree={include_tree}, chunk={chunk_duration}s"
        )

    # ── Nombre del dispositivo (compatibilidad con AudioCapture) ─────────────

    @property
    def device_name(self) -> str:
        return f"Process Loopback (PID={self.pid})"

    # ── Callback que la DLL llama en su hilo de captura ──────────────────────

    def _on_audio_data(
        self,
        data_ptr: ctypes.POINTER(ctypes.c_float),
        frames: int,
        channels: int,
        sample_rate: int,
    ) -> None:
        """
        Recibe puntero a audio float32 interleaved desde la DLL C++.
        Convierte a numpy array y lo pone en la cola interna.
        """
        n_samples = frames * channels
        # Copiar datos antes de que la DLL libere el buffer
        raw = np.ctypeslib.as_array(data_ptr, shape=(n_samples,)).copy()

        # Reshape a (frames, channels) y pasar a mono
        if channels > 1:
            raw = raw.reshape(frames, channels).mean(axis=1)
        else:
            raw = raw.reshape(frames)

        try:
            self._frame_queue.put_nowait(raw)
        except queue.Full:
            # Descartar el frame más antiguo y añadir el nuevo
            try:
                self._frame_queue.get_nowait()
                self._frame_queue.put_nowait(raw)
            except queue.Empty:
                pass

    # ── Resampleo a 16kHz ─────────────────────────────────────────────────────

    @staticmethod
    def _resample(data: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
        if src_rate == dst_rate:
            return data
        ratio       = dst_rate / src_rate
        new_length  = int(len(data) * ratio)
        x_src       = np.arange(len(data))
        x_dst       = np.linspace(0, len(data) - 1, new_length)
        return np.interp(x_dst, x_src, data).astype(np.float32)

    # ── API pública ───────────────────────────────────────────────────────────

    def start(self) -> None:
        """Registra el callback y arranca la captura en la DLL."""
        self._callback_ref = _CALLBACK_TYPE(self._on_audio_data)
        self._lib.set_audio_callback(self._callback_ref)

        ret = self._lib.start_capture(
            ctypes.c_uint32(self.pid),
            ctypes.c_int(1 if self.include_tree else 0),
        )
        if ret != 0:
            raise OSError(
                f"start_capture falló con código 0x{ret & 0xFFFFFFFF:08X}. "
                f"Verifica que Windows 10 Build 19041+ y el PID {self.pid} exista."
            )

        self._running = True
        # Frecuencia nativa de la DLL (siempre 48000 según process_loopback.cpp)
        self._native_rate = 48_000
        logger.info(f"Captura iniciada para PID {self.pid}")

    def stop(self) -> None:
        """Detiene la captura y libera recursos de la DLL."""
        self._running = False
        self._lib.stop_capture()
        logger.info(f"Captura detenida para PID {self.pid}")

    def stream(self) -> Iterator[np.ndarray]:
        """
        Generador que produce chunks de audio mono 16kHz (float32).
        Cada chunk dura aproximadamente chunk_duration segundos.

        Interfaz idéntica a AudioCapture.stream() para poder intercambiarlos
        directamente en worker.py.

        Yields:
            np.ndarray: float32 array, shape (n_samples,), 16kHz mono.
        """
        self.start()

        # Cuántas muestras a 16kHz necesitamos por chunk
        target_samples = int(self.sample_rate * self.chunk_duration)
        accum: list[np.ndarray] = []
        accum_len = 0

        try:
            while self._running:
                try:
                    raw_mono = self._frame_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                # Resamplear de 48kHz → 16kHz
                resampled = self._resample(
                    raw_mono.astype(np.float32),
                    self._native_rate,
                    self.sample_rate,
                )

                accum.append(resampled)
                accum_len += len(resampled)

                # Emitir chunks cuando tengamos suficientes muestras
                while accum_len >= target_samples:
                    combined = np.concatenate(accum)
                    chunk    = combined[:target_samples]
                    rest     = combined[target_samples:]

                    yield chunk

                    # Guardar el remanente para el siguiente chunk
                    accum     = [rest] if len(rest) > 0 else []
                    accum_len = len(rest)

        finally:
            self.stop()

    def cleanup(self) -> None:
        """Alias de stop() para compatibilidad con AudioCapture."""
        self.stop()
