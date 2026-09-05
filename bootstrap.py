"""
bootstrap.py
Punto de entrada de la aplicación.
Carga el modelo de IA (CUDA) ANTES de importar PyQt6 para evitar
que la inicialización COM de Qt corrompa el contexto CUDA.
"""

import sys
import faulthandler

# Capturar crashes de C++ (segfaults, aborts) en un archivo de log
_fault_file = open("crash_faulthandler.log", "w")
faulthandler.enable(file=_fault_file, all_threads=True)

import logging

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(threadName)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("app_debug.log", encoding='utf-8', mode='w'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Silenciar librerías de red muy verbosas
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("filelock").setLevel(logging.WARNING)


def main():
    logger.info("Iniciando aplicacion...")

    # ── 1. Cargar modelo ANTES de importar PyQt6 ─────────────────────────
    # PyQt6 inicializa COM al importarse, lo que corrompe el contexto CUDA.
    from ai_engine import AIEngine
    logger.info("Cargando modelo de IA antes de importar PyQt6...")
    try:
        engine = AIEngine(
            model_size="medium",
            device="cuda",
            compute_type="int8_float16",
        )
        logger.info("Modelo cargado exitosamente en CUDA.")
    except Exception as e:
        logger.error("Error fatal al cargar el modelo:", exc_info=True)
        print(f"\n❌ No se pudo cargar el modelo: {e}")
        print("Verifica que tu GPU tenga VRAM suficiente y los drivers estén actualizados.")
        sys.exit(1)

    # ── 2. Ahora sí importar PyQt6 y lanzar la UI ───────────────────────
    logger.info("Modelo listo. Importando PyQt6 y lanzando interfaz gráfica...")
    from main import run_app
    run_app(engine)


if __name__ == "__main__":
    main()
