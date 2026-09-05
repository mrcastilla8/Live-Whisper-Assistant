"""
audio_capture.py
Módulo de captura de audio del sistema usando PyAudioWPatch para loopback WASAPI.
Captura el audio que se escucha en los altavoces/auriculares del usuario y lo
convierte a mono 16kHz (formato requerido por Whisper).
"""

import logging
import numpy as np
from typing import Optional, Any

logger = logging.getLogger(__name__)

# Frecuencia de muestreo requerida por Whisper
SAMPLE_RATE = 16000

# Duración de cada chunk de audio en segundos
CHUNK_DURATION = 3


def list_loopback_devices() -> list[dict]:
    """
    Lista todos los dispositivos de loopback (audio del sistema) disponibles.
    Usa PyAudioWPatch para encontrar dispositivos de loopback nativos de WASAPI.
    Devuelve una lista de diccionarios con 'id', 'name', 'index'.
    """
    import pyaudiowpatch as pyaudio
    devices = []
    with pyaudio.PyAudio() as p:
        try:
            wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        except OSError:
            logger.error("WASAPI no disponible en este sistema")
            return devices

        for loopback in p.get_loopback_device_info_generator():
            devices.append({
                "id": loopback["index"],
                "name": loopback["name"],
                "index": loopback["index"],
            })
    return devices


def get_default_loopback_device():
    """
    Obtiene el dispositivo de loopback asociado al speaker activo por defecto
    de Windows. Retorna el dict de device_info de PyAudio o None.
    """
    import pyaudiowpatch as pyaudio
    with pyaudio.PyAudio() as p:
        try:
            wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
            default_speakers = p.get_device_info_by_index(
                wasapi_info["defaultOutputDevice"]
            )

            if default_speakers.get("isLoopbackDevice"):
                return default_speakers

            # Buscar el dispositivo de loopback asociado
            for loopback in p.get_loopback_device_info_generator():
                if default_speakers["name"] in loopback["name"]:
                    return loopback

        except OSError:
            logger.error("WASAPI no disponible")
    return None


class AudioCapture:
    """
    Clase que captura audio del loopback del sistema usando PyAudioWPatch + WASAPI.
    Captura en la configuración nativa del dispositivo (ej. 48000Hz stereo)
    y convierte internamente a mono 16kHz para Whisper.
    """

    def __init__(
        self,
        device_name: Optional[str] = None,
        sample_rate: int = SAMPLE_RATE,
        chunk_duration: float = CHUNK_DURATION,
    ):
        """
        Inicializa el capturador de audio.

        Args:
            device_name: Fragmento del nombre del dispositivo loopback a usar.
                         Si es None, usa el dispositivo de salida predeterminado.
            sample_rate: Frecuencia de muestreo de SALIDA (default 16000 para Whisper).
            chunk_duration: Duración en segundos de cada bloque de audio capturado.
        """
        self.sample_rate = sample_rate
        self.chunk_duration = chunk_duration
        self._running = False

        # Encontrar dispositivo de loopback
        import pyaudiowpatch as pyaudio
        self._pyaudio = pyaudio

        self._p = pyaudio.PyAudio()

        if device_name:
            self._device_info = self._find_loopback_by_name(device_name)
            if self._device_info is None:
                raise ValueError(
                    f"No se encontró un dispositivo loopback cuyo nombre contenga: "
                    f"'{device_name}'. "
                    f"Dispositivos disponibles: {list_loopback_devices()}"
                )
        else:
            self._device_info = self._get_default_loopback()
            if self._device_info is None:
                raise ValueError(
                    "No se encontró el dispositivo de loopback por defecto. "
                    f"Dispositivos disponibles: {list_loopback_devices()}"
                )

        # Guardar los parámetros nativos del dispositivo
        self._native_rate = int(self._device_info["defaultSampleRate"])
        self._native_channels = self._device_info["maxInputChannels"]
        self._device_index = self._device_info["index"]

        logger.info(
            f"AudioCapture configurado: {self._device_info['name']} "
            f"(nativo: {self._native_rate}Hz, {self._native_channels}ch → "
            f"salida: {self.sample_rate}Hz, 1ch)"
        )

    def _find_loopback_by_name(self, name_fragment: str):
        """Busca un dispositivo de loopback cuyo nombre contenga name_fragment."""
        for loopback in self._p.get_loopback_device_info_generator():
            if name_fragment.lower() in loopback["name"].lower():
                return loopback
        return None

    def _get_default_loopback(self):
        """Obtiene el loopback del speaker activo por defecto."""
        try:
            wasapi_info = self._p.get_host_api_info_by_type(self._pyaudio.paWASAPI)
            default_speakers = self._p.get_device_info_by_index(
                wasapi_info["defaultOutputDevice"]
            )

            if default_speakers.get("isLoopbackDevice"):
                return default_speakers

            for loopback in self._p.get_loopback_device_info_generator():
                if default_speakers["name"] in loopback["name"]:
                    return loopback
        except OSError:
            logger.error("WASAPI no disponible")
        return None

    @property
    def device_name(self) -> str:
        return self._device_info["name"]

    def start(self):
        """Marca el capturador como activo."""
        self._running = True

    def stop(self):
        """Marca el capturador para detenerse."""
        self._running = False

    def _resample_to_16k_mono(self, data: np.ndarray) -> np.ndarray:
        """
        Convierte audio estéreo a mono y resamplea de la frecuencia nativa
        del dispositivo (ej. 48000Hz) a 16000Hz para Whisper.
        """
        # Convertir a float32
        if data.dtype == np.int16:
            data = data.astype(np.float32) / 32768.0
        elif data.dtype != np.float32:
            data = data.astype(np.float32)

        # Stereo a mono (promediar canales)
        if data.ndim > 1 and data.shape[1] > 1:
            data = np.mean(data, axis=1)
        elif data.ndim > 1:
            data = data[:, 0]

        # Resamplear si es necesario
        if self._native_rate != self.sample_rate:
            # Resampleo simple por interpolación lineal
            ratio = self.sample_rate / self._native_rate
            original_length = len(data)
            new_length = int(original_length * ratio)
            x_original = np.arange(original_length)
            x_new = np.linspace(0, original_length - 1, new_length)
            data = np.interp(x_new, x_original, data)

        return data.astype(np.float32)

    def stream(self):
        """
        Generador que produce chunks de audio como numpy arrays (float32, mono, 16kHz).
        Cada chunk tiene una duración aproximada de self.chunk_duration segundos.

        Yields:
            np.ndarray: Array de audio normalizado float32, forma (n_samples,).
        """
        # Calcular cuántos frames nativos necesitamos para chunk_duration segundos
        native_frames_per_chunk = int(self._native_rate * self.chunk_duration)
        bytes_per_sample = 2  # paInt16 = 2 bytes
        chunk_size = 512  # Frames por lectura del stream

        self._running = True

        logger.debug(
            f"Iniciando loopback recording: {self._device_info['name']} "
            f"(index={self._device_index})"
        )

        stream = self._p.open(
            format=self._pyaudio.paInt16,
            channels=self._native_channels,
            rate=self._native_rate,
            input=True,
            input_device_index=self._device_index,
            frames_per_buffer=chunk_size,
        )

        logger.debug("Stream WASAPI inicializado, entrando en bucle.")

        try:
            while self._running:
                # Leer suficientes frames para llenar chunk_duration
                frames = []
                frames_read = 0

                while frames_read < native_frames_per_chunk and self._running:
                    to_read = min(chunk_size, native_frames_per_chunk - frames_read)
                    try:
                        raw_data = stream.read(to_read, exception_on_overflow=False)
                        frames.append(raw_data)
                        frames_read += to_read
                    except IOError as e:
                        logger.warning(f"IOError en stream.read: {e}")
                        continue

                if not self._running:
                    break

                # Convertir bytes a numpy array
                raw_bytes = b"".join(frames)
                data = np.frombuffer(raw_bytes, dtype=np.int16)

                # Reshape a (frames, canales) si es multichannel
                if self._native_channels > 1:
                    data = data.reshape(-1, self._native_channels)

                # Convertir a mono 16kHz float32
                audio_chunk = self._resample_to_16k_mono(data)

                yield audio_chunk

        finally:
            stream.stop_stream()
            stream.close()
            logger.debug("Stream WASAPI cerrado.")

    def cleanup(self):
        """Libera recursos de PyAudio."""
        try:
            self._p.terminate()
        except Exception:
            pass
