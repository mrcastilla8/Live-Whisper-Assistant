"""
worker.py
Worker en hilo secundario (QThread) que conecta la captura de audio
con el motor de IA en streaming por micro-chunks.
"""

from typing import Optional
from datetime import datetime
import logging
import numpy as np
from scipy import signal as sp_signal
from PyQt6.QtCore import QThread, pyqtSignal

from process_capture import ProcessAudioCapture
from ai_engine import AIEngine, TranscriptionResult

logger = logging.getLogger(__name__)

# ── Parámetros del streaming acumulativo (Semi-Streaming) ──────────────

# Tamaño de cada micro-chunk de captura (en segundos).
CAPTURE_CHUNK_S = 0.16

# RMS mínimo para considerar que un chunk contiene voz.
# Ajustado más alto (0.015) tras diagnóstico: el audio de browser tiene
# música de fondo con RMS ~0.007-0.010, así evitamos alimentar ruido puro.
VOICE_RMS_THRESHOLD = 0.015

# Número de chunks silenciosos consecutivos para decretar pausa/fin de frase.
# 9 chunks × 0.16s = ~1.44 segundos de silencio.
SILENCE_CHUNKS_TO_FLUSH = 9

# Tamaño máximo del buffer acumulado antes de forzar una transcripción (en segundos).
MAX_BUFFER_S = 10.0

# Número mínimo de chunks con voz para validar una frase.
# 2 chunks × 0.16s = 0.32s (~320ms), lo que evita omitir frases muy cortas.
MIN_VOICE_CHUNKS = 2

# Número de chunks a copiar al inicio del siguiente buffer en caso de flush forzado (habla continua).
# 6 chunks × 0.16s = 0.96s (~1 segundo de solapamiento).
OVERLAP_CHUNKS = 6

# Tamaño máximo del pre-roll en chunks.
# 3 chunks × 0.16s = 0.48s (~480ms) de pre-roll.
PRE_ROLL_CHUNKS = 3

# ── Filtro de paso de banda de voz (300–3400 Hz) ─────────────────────
# Conserva solo la banda donde reside la inteligibilidad del habla humana (300-3400 Hz)
# y elimina los graves de música y el ruido de alta frecuencia.
_SAMPLE_RATE = 16_000
_BP_LOW  = 300   # Hz - corte inferior (elimina graves/bajo de música)
_BP_HIGH = 3400  # Hz - corte superior (elimina treble/ruido HF)
_BP_B, _BP_A = sp_signal.butter(
    N=4,
    Wn=[_BP_LOW / (_SAMPLE_RATE / 2), _BP_HIGH / (_SAMPLE_RATE / 2)],
    btype='band',
)

def _apply_voice_filter(chunk: np.ndarray) -> np.ndarray:
    """Aplica filtro paso de banda 300-3400 Hz para aislar la voz del ruido/música."""
    filtered = sp_signal.lfilter(_BP_B, _BP_A, chunk)
    return filtered.astype(np.float32)


class AudioWorker(QThread):
    """
    Hilo de trabajo que:
    1. Captura audio de un proceso específico (PID).
    2. Acumula chunks en un buffer circular/acumulativo.
    3. Cuando detecta silencio después de hablar, envía todo el buffer al motor.
    4. Emite señales hacia la UI principal.
    """

    transcribed = pyqtSignal(str, str, object, float, float, bool)  # lang, text, translated, start_time, end_time, is_final
    error = pyqtSignal(str)
    status = pyqtSignal(str)

    def __init__(
        self,
        engine: AIEngine,
        target_pid: int,
        initial_lang: str = "es",
        parent=None,
    ):
        super().__init__(parent)
        self._engine = engine
        self._target_pid = target_pid
        self._running = False
        self._target_lang = initial_lang
        self._lang_changed = False

    def set_language(self, lang: str):
        """Establece el idioma de transcripción de forma dinámica."""
        self._target_lang = lang
        self._lang_changed = True
        logger.info(f"Petición de cambio de idioma en el worker a: {lang}")

    def run(self):
        """Punto de entrada del hilo."""
        logger.info(f"Worker thread started. Inicializando captura acumulativa para PID: {self._target_pid}...")
        try:
            self.status.emit("Inicializando captura de audio...")
            capture = ProcessAudioCapture(
                pid=self._target_pid,
                include_tree=True,
                chunk_duration=CAPTURE_CHUNK_S,
            )
            self.status.emit(f"PID objetivo: {self._target_pid}")
            self.status.emit("✓ Escuchando audio... (habla y el programa te detectará)")

            # ── Buffer acumulativo ───────────────────────────────────────────
            audio_buffer = []       # Lista de chunks numpy (originales, sin filtrar)
            silent_chunks = 0       # Contador de chunks silenciosos consecutivos
            voice_chunks = 0        # Contador de chunks con voz en el buffer actual
            segment_start: Optional[float] = None  # Timestamp del inicio del segmento actual
            sample_rate = capture.sample_rate
            max_buffer_frames = int(MAX_BUFFER_S * sample_rate)

            # Pre-roll: buffer circular de chunks silenciosos recientes
            from collections import deque
            pre_roll = deque(maxlen=PRE_ROLL_CHUNKS)

            # Contexto del segmento previo para soft prompting
            last_transcribed_text: Optional[str] = None

            in_speech = False
            self._running = True
            logger.debug("Entrando al bucle de captura de audio con buffer acumulativo (Semi-Streaming)")

            for audio_chunk in capture.stream():
                if not self._running:
                    break

                # Comprobar cambio de idioma
                if self._lang_changed:
                    if audio_buffer and voice_chunks >= 1:
                        logger.info("Flushing buffer before changing language.")
                        result_text = self._flush_buffer(
                            audio_buffer, voice_chunks, segment_start, last_transcribed_text
                        )
                        if result_text:
                            last_transcribed_text = result_text
                    
                    audio_buffer = []
                    voice_chunks = 0
                    silent_chunks = 0
                    segment_start = None
                    pre_roll.clear()
                    in_speech = False
                    self._lang_changed = False
                    logger.info(f"Worker cambió el idioma de transcripción a: {self._target_lang}")

                # Filtrar el chunk para la detección de RMS de voz
                audio_float = audio_chunk.astype(np.float32)
                filtered_chunk = _apply_voice_filter(audio_float)
                rms = float(np.sqrt(np.mean(filtered_chunk ** 2)))
                now = datetime.now().timestamp()

                if not in_speech:
                    if rms >= VOICE_RMS_THRESHOLD:
                        in_speech = True
                        silent_chunks = 0
                        segment_start = now - (len(pre_roll) * CAPTURE_CHUNK_S)
                        
                        # Agregar pre-roll al buffer
                        for pr_chunk in pre_roll:
                            audio_buffer.append(pr_chunk)
                        pre_roll.clear()
                        
                        # Agregar chunk actual
                        audio_buffer.append(audio_float)
                        voice_chunks += 1
                        logger.debug(f"Voz detectada! Buffer iniciado con pre-roll. RMS={rms:.4f}")
                    else:
                        pre_roll.append(audio_float)
                else:
                    audio_buffer.append(audio_float)
                    total_frames = sum(len(c) for c in audio_buffer)
                    
                    if rms >= VOICE_RMS_THRESHOLD:
                        voice_chunks += 1
                        silent_chunks = 0
                    else:
                        silent_chunks += 1

                    # Determinar si hay que forzar flush por buffer lleno o por silencio
                    should_flush_silence = (silent_chunks >= SILENCE_CHUNKS_TO_FLUSH)
                    should_flush_max = (total_frames >= max_buffer_frames)

                    if should_flush_silence:
                        if voice_chunks >= MIN_VOICE_CHUNKS:
                            logger.info(f"Fin de frase detectado (silencio de {silent_chunks} chunks). Flushing...")
                            result_text = self._flush_buffer(
                                audio_buffer, voice_chunks, segment_start, last_transcribed_text
                            )
                            if result_text:
                                last_transcribed_text = result_text
                        else:
                            logger.debug(f"Silencio detectado pero no hay suficiente voz ({voice_chunks} chunks). Descartando.")

                        audio_buffer = []
                        voice_chunks = 0
                        silent_chunks = 0
                        segment_start = None
                        in_speech = False
                        pre_roll.clear()

                    elif should_flush_max:
                        if voice_chunks >= MIN_VOICE_CHUNKS:
                            logger.info("Forzando flush por límite de buffer (habla continua).")
                            result_text = self._flush_buffer(
                                audio_buffer, voice_chunks, segment_start, last_transcribed_text
                            )
                            if result_text:
                                last_transcribed_text = result_text
                        
                        # Overlap: copiar los últimos N chunks al siguiente buffer
                        overlap = audio_buffer[-OVERLAP_CHUNKS:] if len(audio_buffer) >= OVERLAP_CHUNKS else audio_buffer[:]
                        audio_buffer = list(overlap)
                        voice_chunks = len(overlap)
                        silent_chunks = 0
                        overlap_duration = len(overlap) * CAPTURE_CHUNK_S
                        segment_start = now - overlap_duration
                        logger.debug(f"Overlap: {len(overlap)} chunks ({overlap_duration:.2f}s) copiados al siguiente segmento.")

            # Flush final garantizado al detener el worker
            if audio_buffer and voice_chunks >= 1:
                logger.info("Forzando flush final por detención del worker.")
                self._flush_buffer(audio_buffer, voice_chunks, segment_start, last_transcribed_text)

        except Exception as e:
            logger.error("Error no capturado en el worker:", exc_info=True)
            self.error.emit(f"Error en el worker: {e}")

    def _flush_buffer(
        self,
        audio_buffer: list,
        voice_chunks: int,
        segment_start: Optional[float],
        previous_text: Optional[str] = None,
    ) -> Optional[str]:
        """
        Concatena el buffer y lo envía al motor de IA para transcribir.
        """
        combined = np.concatenate(audio_buffer, axis=0).astype(np.float32)
        duration_s = len(combined) / 16000
        end_time = datetime.now().timestamp()
        start_time = segment_start if segment_start is not None else (end_time - duration_s)
        
        logger.info(
            f"Enviando buffer al motor: {duration_s:.1f}s total, {voice_chunks} chunks con voz"
            + (f" | ctx: '...{ ' '.join((previous_text or '').split()[-3:]) }'" if previous_text else "")
        )
        try:
            result = self._engine.transcribe(
                combined, previous_text=previous_text, lang=self._target_lang
            )
            if result is not None:
                self.transcribed.emit(
                    result.original_language,
                    result.original_text,
                    result.translated_text,
                    start_time,
                    end_time,
                    True,  # is_final = True
                )
                return result.original_text
        except Exception as e:
            logger.error("Error en transcripción del buffer:", exc_info=True)
            self.error.emit(f"Error en transcripción: {e}")
        return None

    def stop(self):
        """Solicita al hilo que se detenga de forma segura."""
        self._running = False
