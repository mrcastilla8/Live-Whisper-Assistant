# Application Loopback — Captura por Proceso

Captura el audio producido por una sola aplicación (Zoom, Chrome, Spotify, etc.) usando **WASAPI Application Loopback** de Windows.

## Archivos creados

| Archivo | Rol |
|---|---|
| `native/process_loopback.cpp` | Núcleo C++ — llama a `ActivateAudioInterfaceAsync` |
| `native/build_dll.py` | Compila el `.cpp` → `process_loopback.dll` |
| `native/install_mingw.py` | Descarga MinGW-w64 si no hay compilador |
| `process_capture.py` | Wrapper Python (`ctypes`) — interfaz idéntica a `AudioCapture` |
| `test_process_loopback.py` | Script de prueba y verificación |

## Requisitos del sistema

- **Windows 10 Build 19041** (versión 20H1) o superior / Windows 11
- **Python 3.10+** con `psutil` instalado

## Instalación rápida

### Paso 1 — Compilar la DLL

**Opción A** (recomendada — instala MinGW automáticamente):
```powershell
python native/install_mingw.py
```

**Opción B** (si ya tienes Visual Studio Build Tools):
```powershell
# Abrir "Developer Command Prompt for VS"
python native/build_dll.py
```

**Opción C** (MinGW ya instalado):
```powershell
python native/build_dll.py
```

### Paso 2 — Instalar psutil
```powershell
pip install psutil
```

## Uso del script de prueba

```powershell
# Listar todos los procesos en ejecución
python test_process_loopback.py --list

# Capturar 10 segundos de Chrome y guardar WAV
python test_process_loopback.py --name chrome --secs 10 --wav

# Capturar por PID exacto
python test_process_loopback.py --pid 5432 --secs 5 --wav
```

El archivo `test_process_loopback.wav` generado puede abrirse en **Audacity** o **VLC** para verificar que solo se capturó el audio del proceso objetivo.

## Integración en el motor principal

`ProcessAudioCapture` tiene la **misma interfaz que `AudioCapture`** (mismo método `stream()`), por lo que en `worker.py` solo hay que cambiar la inicialización:

```python
# Antes (audio del sistema completo):
from audio_capture import AudioCapture
capture = AudioCapture(device_name=self._device_name)

# Ahora (audio de una sola aplicación por PID):
from process_capture import ProcessAudioCapture
capture = ProcessAudioCapture(pid=target_pid, include_tree=True)
```

## Arquitectura técnica

```
Python worker.py
      │
      │ yield np.ndarray (float32, mono, 16kHz)
      ▼
process_capture.py (ctypes wrapper)
      │
      │ ctypes.CDLL + callback C → Python
      ▼
process_loopback.dll (C++ nativo)
      │
      │ ActivateAudioInterfaceAsync(PID)
      │ AUDCLNT_ACTIVATION_TYPE_PROCESS_LOOPBACK
      ▼
Windows WASAPI — IAudioClient → IAudioCaptureClient
      │
      │ float32, 48kHz, stereo
      ▼
DLL resamplea implícitamente con AUTOCONVERTPCM
process_capture.py resamplea 48kHz→16kHz + estéreo→mono
      │
      ▼
AIEngine (Whisper) recibe audio idéntico al anterior
```

## Notas importantes

- La DLL siempre captura en **float32, 48kHz, estéreo** y el wrapper Python lo convierte a **float32, 16kHz, mono** antes de pasarlo a Whisper.
- El flag `AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM` delega la conversión de formato a Windows si el proceso objetivo usa un formato diferente.
- Si el proceso objetivo no produce audio en el momento de `start_capture`, la DLL devuelve **silencio** (flags `AUDCLNT_BUFFERFLAGS_SILENT`), que el wrapper representa como zeros. El VAD de Whisper los ignorará.
- `include_tree=True` captura también los **procesos hijos** (útil para Chrome/Edge, que usan procesos separados por pestaña).
