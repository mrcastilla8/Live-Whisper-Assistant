"""
ai_engine.py
Motor de transcripción basado en faster-whisper (CTranslate2).
Proporciona alta precisión en español/inglés con ruido de fondo y música.
"""

import logging
from dataclasses import dataclass
from typing import Optional
import sys
import os
import time
import numpy as np

logger = logging.getLogger(__name__)

@dataclass
class TranscriptionResult:
    """
    Resultado de una transcripción individual.
    """
    original_language: str = ""
    original_text: str = ""
    language_probability: float = 1.0
    translated_text: Optional[str] = None


SUPPORTED_LANGUAGES = {"es", "en"}
SOFT_PROMPT_WORDS = 5
KNOWN_HALLUCINATIONS = {
    "thank you so much",
    "thank you",
    "gracias",
    "conversación bilingüe",
    "conversacion bilingue",
    "bilingual conversation",
    "thank you for listening",
    "thank you for watching",
    "subtitles by",
    "subtitulos por",
    "amaraorg",
    "subscribe",
    "suscribete",
    "suscríbete"
}



class WhisperStream:
    """
    Simulación de stream para mantener el buffer de audio de la frase actual.
    """
    def __init__(self, lang: Optional[str]):
        self.lang = lang
        self.detected_lang = lang if lang else "es"  # Por defecto 'es' hasta que se detecte
        self.buffer: list[np.ndarray] = []
        self.text = ""
        self.last_transcribe_time = 0.0
        self.dirty = False


class AIEngine:
    """
    Motor de transcripción que envuelve faster-whisper.
    Mantiene la misma interfaz que el reconocedor Nemotron.
    """

    def __init__(
        self,
        model_size: str = "medium",
        device: str = "cuda",
        compute_type: str = "int8_float16",
        model_dir: str = "model",
    ):
        # Ajustar compute_type si corremos en CPU para evitar incompatibilidades
        if device == "cpu" and compute_type == "int8_float16":
            compute_type = "int8"

        logger.info(f"Inicializando motor faster-whisper '{model_size}' en {device} ({compute_type})...")

        # ── Inyección dinámica de DLLs de Nvidia ────────────────────────────
        if sys.platform == "win32" and device == "cuda":
            import site
            logger.debug("Intentando inyectar directorios NVIDIA DLL...")
            try:
                site_packages = site.getsitepackages()
                for sp in site_packages:
                    for nvidia_pkg in ["cublas", "cudnn"]:
                        target_dir = os.path.join(sp, "nvidia", nvidia_pkg, "bin")
                        if os.path.exists(target_dir):
                            logger.info(f"Inyectando DLL dir: {target_dir}")
                            os.add_dll_directory(target_dir)
                            os.environ["PATH"] = target_dir + os.pathsep + os.environ.get("PATH", "")
            except Exception as e:
                logger.warning(f"Error inyectando DLLs de NVIDIA: {e}")

        # Cargar faster-whisper después de la inyección de DLLs
        try:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(
                model_size,
                device=device,
                compute_type=compute_type,
            )
            logger.info("Modelo Whisper cargado exitosamente.")
        except Exception as e:
            logger.error("Error fatal al instanciar WhisperModel:", exc_info=True)
            raise e

        self._last_lang = "es"

    def transcribe(
        self,
        audio_chunk: np.ndarray,
        previous_text: Optional[str] = None,
        lang: Optional[str] = None,
    ) -> Optional[TranscriptionResult]:
        """
        Transcribe un bloque de audio (idealmente acumulado) usando Whisper.
        """
        rms = np.sqrt(np.mean(audio_chunk ** 2))
        logger.debug(f"Audio chunk recibido. RMS: {rms:.5f}")

        # Mapear códigos de idioma al formato corto de Whisper
        lang_code = None
        if lang in ["es-ES", "es-US", "es"]:
            lang_code = "es"
        elif lang in ["en-US", "en-GB", "en"]:
            lang_code = "en"
        elif lang == "auto":
            lang_code = None
        else:
            lang_code = lang

        # ── Soft Prompt: contexto del segmento anterior ──────────────────────
        base_prompt = "Español latino. English."
        if previous_text:
            # Tomar las últimas N palabras del texto previo para orientar a
            # Whisper sin habilitar condition_on_previous_text (que causa bucles).
            last_words = " ".join(previous_text.split()[-SOFT_PROMPT_WORDS:])
            initial_prompt = f"{base_prompt} {last_words}"
            logger.debug(f"Soft prompt activo: ...'{last_words}'")
        else:
            initial_prompt = base_prompt

        try:
            segments, info = self._model.transcribe(
                audio_chunk,
                beam_size=5,
                language=lang_code,
                task="transcribe",
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=1000, speech_pad_ms=400),
                compression_ratio_threshold=2.4,
                log_prob_threshold=-2.0,
                no_speech_threshold=0.7,
                condition_on_previous_text=False,
                initial_prompt=initial_prompt,
            )
        except Exception as e:
            logger.error("Error durante la transcripción:", exc_info=True)
            raise e

        try:
            detected_lang = info.language
            lang_probability = info.language_probability

            logger.debug(
                f"Idioma detectado: {detected_lang} (prob={lang_probability:.2f}), "
                f"dur_after_vad={info.duration_after_vad:.2f}s"
            )

            # Solo procesar idiomas soportados
            if detected_lang not in SUPPORTED_LANGUAGES:
                logger.debug(f"Idioma '{detected_lang}' no soportado, descartando.")
                return None

            segment_texts = []
            for seg in segments:
                text = seg.text.strip()
                if text:
                    logger.debug(f"  Segmento [{seg.start:.1f}s-{seg.end:.1f}s]: {text!r}")
                    segment_texts.append(text)

            full_text = " ".join(segment_texts).strip()

        except Exception as e:
            logger.error("Error al procesar los segmentos transcritos:", exc_info=True)
            raise e

        if not full_text:
            logger.debug("Texto vacío tras procesar segmentos, descartando.")
            return None

        # ── Filtro Anti-Alucinaciones ─────────────────────────────────────────
        import re
        cleaned_text = re.sub(r'[^\w\s]', '', full_text).strip().lower()
        if cleaned_text in KNOWN_HALLUCINATIONS:
            logger.info(f"Alucinación detectada y descartada: {full_text!r}")
            return None

        logger.info(f"Transcripción [{detected_lang}]: {full_text!r}")

        return TranscriptionResult(
            original_language=detected_lang,
            original_text=full_text,
            language_probability=lang_probability,
            translated_text=None,
        )

    def create_stream(self, lang: str = "auto"):
        """Crea y retorna un WhisperStream."""
        # Mapear códigos de idioma al formato corto que Whisper espera
        if lang in ["es-ES", "es-US", "es"]:
            lang_code = "es"
        elif lang in ["en-US", "en-GB", "en"]:
            lang_code = "en"
        elif not lang or lang == "auto":
            lang_code = None
        else:
            lang_code = lang
            
        logger.info(f"Stream de Whisper creado con idioma: {lang_code}")
        return WhisperStream(lang=lang_code)

    def accept_waveform(self, stream: WhisperStream, audio_chunk: np.ndarray):
        """Acumula un chunk de audio en el buffer del stream."""
        stream.buffer.append(audio_chunk)
        stream.dirty = True

    def decode_stream(self, stream: WhisperStream, force: bool = False):
        """
        Ejecuta la transcripción sobre el audio acumulado.
        Throttled para no saturar la GPU durante streaming.
        """
        if not stream.dirty or not stream.buffer:
            return

        try:
            # Concatenar todos los chunks en un único array
            audio = np.concatenate(stream.buffer)
            
            # Evitar transcribir parciales si el audio es muy corto (menos de 0.8 segundos)
            # a menos que sea el resultado final (force=True). 16000 Hz * 0.8s = 12800 samples.
            if not force and len(audio) < 12800:
                return
                
            now = time.time()
            # Limitar ejecuciones a una cada 1.2s (para reducir parpadeo), a menos que se fuerce
            if not force and (now - stream.last_transcribe_time < 1.2):
                return

            # Transcribir usando el modelo Whisper
            # beam_size=3 es un gran balance entre velocidad y precisión.
            # temperature=0.0 asegura transcripción determinista.
            segments, info = self._model.transcribe(
                audio,
                language=stream.lang,
                beam_size=5,
                temperature=0.0,
                vad_filter=True,  # Activado para eliminar silencios internos y evitar cortes/omisiones
                vad_parameters=dict(min_silence_duration_ms=1000, speech_pad_ms=400),
                condition_on_previous_text=True, # Activado para mantener contexto en frases largas
            )
            
            text_parts = [segment.text for segment in segments]
            stream.text = "".join(text_parts).strip()
            
            # Guardar el idioma detectado por Whisper si estábamos en AUTO
            if stream.lang is None and hasattr(info, 'language'):
                stream.detected_lang = info.language
            
            stream.last_transcribe_time = now
            stream.dirty = False
        except Exception as e:
            logger.error(f"Error en la decodificación de Whisper: {e}")

    def get_result(self, stream: WhisperStream, force: bool = False) -> str:
        """Obtiene la transcripción parcial/final acumulada del stream."""
        if force and stream.dirty:
            self.decode_stream(stream, force=True)
        return stream.text

    def reset_states(self, stream: WhisperStream):
        """Limpia el buffer y estados del stream."""
        stream.buffer.clear()
        stream.text = ""
        stream.dirty = False
        stream.last_transcribe_time = 0.0
        logger.debug("Estados del stream de Whisper reseteados.")

    def is_endpoint(self, stream: WhisperStream) -> bool:
        """
        Whisper es de tipo batch, por lo que confiamos en la segmentación
        RMS externa del worker. Retornamos siempre False.
        """
        return False

    def detect_language(self, text: str) -> str:
        """Compatibilidad para detección de idioma rápida."""
        from langdetect import detect
        cleaned = text.strip()
        if not cleaned:
            return self._last_lang
        try:
            lang = detect(cleaned)
            if lang in {"es", "en"}:
                self._last_lang = lang
                return lang
        except Exception:
            pass
        return self._last_lang

    def score_text(self, text: str, lang: str) -> float:
        """Compatibilidad: puntuación heurística de idioma."""
        return 0.0

    def select_best_text(self, text_es: str, text_en: str) -> tuple[str, str]:
        """Compatibilidad: selección del mejor texto."""
        return "es", text_es
