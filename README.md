# Live Whisper Assistant — Real-Time Process Loopback & AI Transcription

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Platform Windows](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078d6.svg)](https://www.microsoft.com/windows)
[![C++17 WASAPI](https://img.shields.io/badge/C++-17%20WASAPI-00599C.svg)](https://learn.microsoft.com/en-us/windows/win32/coreaudio/wasapi)
[![CUDA Acceleration](https://img.shields.io/badge/NVIDIA-CUDA%20%7C%20CTranslate2-76b900.svg)](https://developer.nvidia.com/cuda-zone)
[![GUI PyQt6](https://img.shields.io/badge/GUI-PyQt6-41cd52.svg)](https://pypi.org/project/PyQt6/)
[![AI faster--whisper](https://img.shields.io/badge/ASR-faster--whisper-orange.svg)](https://github.com/SYSTRAN/faster-whisper)

**Live Whisper Assistant** es una solución de escritorio de alto rendimiento para **transcripción e interpretación simultánea en tiempo real (Español ↔ Inglés)** diseñada específicamente para Windows. 

A diferencia de las herramientas convencionales basadas en micrófonos o "Mezcla Estéreo" global, este software utiliza una **DLL nativa en C++** con la API **Windows WASAPI Application Loopback (`ActivateAudioInterfaceAsync`)** para interceptar de forma quirúrgica el flujo de audio de **una aplicación específica** (Zoom, Microsoft Teams, Google Meet, Chrome, etc.), eliminando la retroalimentación de micrófono y ruidos del entorno. El flujo de audio se procesa mediante filtros acústicos digitales (DSP) y se transcribe localmente en la GPU usando **Faster-Whisper (CTranslate2)** con cuantización `int8_float16`.

---

## Características Principales

- **Captura Selectiva por Proceso (Process Loopback):** Intercepta exclusivamente el audio de la aplicación seleccionada por PID o árbol de procesos (capturando incluso procesos hijos de navegadores multiproceso como Chrome o Edge).
- **Transcripción Local Ultrarrápida (CUDA):** Motor de inferencia basado en CTranslate2 y Faster-Whisper optimizado para ejecutarse en GPUs NVIDIA con 4 GB de VRAM (como la RTX 3050) en cuantización `int8_float16` con latencia inferior al segundo.
- **Pipeline DSP de Voz:**
  - **Filtro Pasa Banda Butterworth (300 Hz – 3400 Hz):** Remueve frecuencias graves de música de fondo y estática de alta frecuencia para maximizar la inteligibilidad vocal.
  - **Detección de Actividad de Voz (VAD) basada en RMS:** Buffer acumulativo con pre-roll dinámico y solapamiento contextual (*overlap*) para evitar que se corten inicios y finales de palabras.
  - **Filtro Anti-Alucinaciones:** Supresión de bucles de subtítulos y artefactos conocidos de Whisper ante segmentos de silencio prolongado.
- **Interfaz Gráfica Moderna (PyQt6):**
  - Tema oscuro profesional con visualizador de audio animado en vivo.
  - Transcripción sincronizada en tiempo real con código de color por idioma detectado.
  - Registro y guardado automático de sesiones completas en texto formateado.

---

## Arquitectura del Sistema

El proyecto integra un pipeline híbrido C++ / Python que garantiza máxima velocidad de captura con baja sobrecarga de CPU y sincronización de hilos segura:

```
┌──────────────────────────────────────────────┐
│  Aplicación Objetivo (Zoom / Teams / Chrome) │
└──────────────────────┬───────────────────────┘
                       │ Audio emitido (PCM 48 kHz Estéreo)
                       ▼
┌──────────────────────────────────────────────┐
│   C++ Native DLL (process_loopback.dll)      │  ◄── ActivateAudioInterfaceAsync
│   IAudioClient & IAudioCaptureClient         │      (AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM)
└──────────────────────┬───────────────────────┘
                       │ Callback C -> Python (ctypes)
                       ▼
┌──────────────────────────────────────────────┐
│   ProcessAudioCapture (process_capture.py)   │  ◄── Resample a 16 kHz & Mono
└──────────────────────┬───────────────────────┘
                       │ Micro-chunks de audio (np.float32)
                       ▼
┌──────────────────────────────────────────────┐
│   AudioWorker QThread (worker.py)            │  ◄── SciPy Butterworth (300-3400 Hz)
│   DSP & Buffer Acumulativo VAD               │      Pre-roll + Overlap context
└──────────────────────┬───────────────────────┘
                       │ Bloque de voz listo
                       ▼
┌──────────────────────────────────────────────┐
│   AI Engine (ai_engine.py)                   │  ◄── Faster-Whisper (CUDA int8_float16)
│   Whisper Inference + Soft Prompt            │      NVIDIA cuBLAS & cuDNN Injected
└──────────────────────┬───────────────────────┘
                       │ Texto transcrito e idioma detectado
                       ▼
┌──────────────────────────────────────────────┐
│   Interfaz Gráfica PyQt6 (main.py)           │  ◄── Visualizador RMS + Subtítulos
└──────────────────────────────────────────────┘
```

---

## Retos de Ingeniería y Soluciones

### 1. Captura de Audio por Proceso en Windows
WASAPI tradicional sólo captura dispositivos completos (micrófono o parlantes). Para aislar la voz de una videollamada sin capturar el propio micrófono del usuario ni sonidos de Windows, se implementó un módulo nativo en C++ (`native/process_loopback.cpp`) que interactúa con la interfaz COM asíncrona `ActivateAudioInterfaceAsync` y `AUDIOCLIENT_ACTIVATION_PARAMS` con `AUDCLNT_ACTIVATION_TYPE_PROCESS_LOOPBACK`.

### 2. Conflicto de Threading: COM Apartments vs CUDA Context
`PyQt6` inicializa hilos con el modelo Single-Threaded Apartment (STA) de Windows COM al momento de ser importado. Cuando CTranslate2 / PyTorch intentan inicializar el contexto CUDA posteriormente en el mismo hilo, Windows COM genera violaciones de acceso de bajo nivel.
- **Solución:** Se implementó [`bootstrap.py`](file:///c:/Users/marec/Desktop/Interprete/bootstrap.py), un punto de entrada que pre-inicializa y calienta el motor de IA en GPU antes de que los módulos de PyQt6 sean cargados en memoria.

### 3. Inyección Dinámica de DLLs de NVIDIA en Windows
Para evitar errores de carga de `cublas64_*.dll` o `cudnn_*.dll` cuando el usuario instala PyTorch/CUDA mediante pip o wheels independientes, [`ai_engine.py`](file:///c:/Users/marec/Desktop/Interprete/ai_engine.py) escanea dinámicamente las rutas de los paquetes `site-packages/nvidia` y las inyecta en el espacio de búsqueda del sistema operativo mediante `os.add_dll_directory` y `PATH`.

---

## Requisitos del Sistema

- **Sistema Operativo:** Windows 10 (Build 19041 / versión 20H1 o posterior) o Windows 11.
- **Procesador:** Intel Core i5 / AMD Ryzen 5 o superior.
- **Memoria RAM:** 8 GB o superior.
- **GPU (Recomendada):** NVIDIA GeForce GTX 1650 / RTX 3050 o superior con soporte CUDA. *(También es posible ejecutar en CPU configurando `device="cpu"`).*
- **Python:** 3.10, 3.11 o 3.12 (recomendado 3.11/3.12 64-bit).

---

## Instalación y Puesta en Marcha

### 1. Clonar el repositorio
```bash
git clone https://github.com/mrcastilla8/Live-Whisper-Assistant.git
cd Live-Whisper-Assistant
```

### 2. Crear y activar un entorno virtual (recomendado)
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 3. Instalar las dependencias
```powershell
pip install -r requirements.txt
```

> **Nota para aceleración CUDA en Windows:**
> Si utilizas GPU NVIDIA, asegúrate de tener instalados los drivers actualizados y bibliotecas de CUDA compatibles con PyTorch/CTranslate2.

### 4. Compilación del módulo nativo C++ (Opcional)
El repositorio ya incluye la biblioteca precompilada de 64 bits en `native/process_loopback.dll`. Si deseas recompilarla desde el código fuente C++:
```powershell
python native/build_dll.py
```
*(Si no cuentas con un compilador C++ instalado, ejecuta `python native/install_mingw.py` para descargarlo y configurarlo automáticamente).*

### 5. Iniciar la aplicación
Ejecuta la aplicación a través de su iniciador seguro:
```powershell
python bootstrap.py
```

---

## Herramienta de Diagnóstico CLI

Si deseas probar la captura de audio por proceso sin abrir la interfaz gráfica, puedes utilizar el script CLI de diagnóstico:

```powershell
# Listar todas las aplicaciones activas con su PID
python test_process_loopback.py --list

# Capturar 10 segundos de Chrome y exportar el resultado a un archivo WAV
python test_process_loopback.py --name chrome --secs 10 --wav

# Capturar por PID exacto
python test_process_loopback.py --pid 12345 --secs 5
```

---

## Estructura del Proyecto

```text
├── native/                         # Capa nativa de bajo nivel en C++
│   ├── process_loopback.cpp        # Interfaz WASAPI y ActivateAudioInterfaceAsync
│   ├── process_loopback.dll        # DLL precompilada de 64-bits
│   ├── build_dll.py                # Script de compilación automática (MSVC / MinGW)
│   ├── install_mingw.py            # Instalador asistido del toolchain MinGW-w64
│   └── README.md                   # Documentación técnica de la capa C++
├── bootstrap.py                    # Punto de entrada principal (resuelve COM/CUDA)
├── main.py                         # Interfaz gráfica moderna en PyQt6
├── worker.py                       # Hilo asíncrono secundario (QThread) y DSP vocal
├── ai_engine.py                    # Motor de inferencia faster-whisper con GPU
├── process_capture.py              # Wrapper de Python (ctypes) para la DLL nativa
├── audio_capture.py                # Captura alternativa global WASAPI
├── test_process_loopback.py        # Herramienta de pruebas CLI para WASAPI
├── requirements.txt                # Dependencias del proyecto
└── .gitignore                      # Reglas de exclusión de git
```

---

## Licencia

Distribuido bajo la Licencia MIT. Consulta el archivo `LICENSE` para más información.
