"""
main.py
Interfaz gráfica principal del Asistente de Interpretación (Español ↔ Inglés).
Ventana única con scroll unificado: orden cronológico entre ES e EN.
Diseñada para ocupar la mitad de la pantalla del usuario.
"""

import sys
import os
import logging
from datetime import datetime
from typing import Optional, Any

logger = logging.getLogger(__name__)

from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QStatusBar,
    QScrollArea,
    QFrame,
    QSizePolicy,
    QGraphicsDropShadowEffect,
)
from PyQt6.QtCore import Qt, QSize, QTimer, QPropertyAnimation, QEasingCurve, pyqtProperty, QPoint
from PyQt6.QtGui import QFont, QColor, QPainter, QBrush, QPen

from process_capture import list_audio_processes
from worker import AudioWorker

NARROW_LAYOUT_THRESHOLD = 650

# ── Paleta de colores ────────────────────────────────────────────────────────

COLORS = {
    "bg_main": "#0a0a12",
    "bg_panel": "#13131f",
    "bg_text": "#0d0d1a",
    "border": "#252535",
    "accent_es": "#3b82f6",      # Azul para español
    "accent_en": "#8b5cf6",      # Púrpura para inglés
    "accent_auto": "#14b8a6",    # Teal para automático
    "accent_es_dim": "#1e3a5f",
    "accent_en_dim": "#3b2070",
    "text_primary": "#e2e8f0",
    "text_secondary": "#94a3b8",
    "text_timestamp": "#64748b",
    "success": "#22c55e",
    "error": "#ef4444",
    "warning": "#f59e0b",
    "live_red": "#f43f5e",
}


# ── Estilos CSS globales ────────────────────────────────────────────────────

STYLESHEET = f"""
QMainWindow {{
    background-color: {COLORS["bg_main"]};
}}

QWidget#rootWidget {{
    background-color: {COLORS["bg_main"]};
}}

QLabel#headerLabel {{
    color: {COLORS["text_primary"]};
    font-family: 'Segoe UI', 'Inter', sans-serif;
    font-size: 20px;
    font-weight: bold;
    padding: 0px;
}}

QLabel#subtitleLabel {{
    color: {COLORS["text_secondary"]};
    font-family: 'Segoe UI', 'Inter', sans-serif;
    font-size: 12px;
    padding: 0px;
}}

QPushButton {{
    background-color: {COLORS["accent_es"]};
    color: white;
    border: none;
    border-radius: 8px;
    padding: 8px 14px;
    font-family: 'Segoe UI', 'Inter', sans-serif;
    font-size: 13px;
    font-weight: bold;
    min-width: 80px;
}}

QPushButton:hover {{
    background-color: #2563eb;
}}

QPushButton:pressed {{
    background-color: #1d4ed8;
}}

QPushButton:disabled {{
    background-color: #1e293b;
    color: #475569;
}}

QPushButton#stopBtn {{
    background-color: {COLORS["error"]};
}}

QPushButton#stopBtn:hover {{
    background-color: #dc2626;
}}

QPushButton#stopBtn:disabled {{
    background-color: #1e293b;
    color: #475569;
}}

QPushButton#clearBtn {{
    background-color: transparent;
    color: {COLORS["text_secondary"]};
    border: 1px solid {COLORS["border"]};
    min-width: 80px;
}}

QPushButton#clearBtn:hover {{
    background-color: {COLORS["border"]};
    color: {COLORS["text_primary"]};
}}

QPushButton#refreshBtn {{
    background-color: transparent;
    color: {COLORS["text_secondary"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
    padding: 9px 12px;
    min-width: 36px;
    font-size: 14px;
}}

QPushButton#refreshBtn:hover {{
    background-color: {COLORS["border"]};
    color: {COLORS["text_primary"]};
}}

QPushButton#newMsgBtn {{
    background-color: {COLORS["accent_es"]};
    color: white;
    border: none;
    border-radius: 16px;
    padding: 6px 16px;
    font-size: 12px;
    min-width: 0px;
}}

QComboBox {{
    background-color: {COLORS["bg_text"]};
    color: {COLORS["text_primary"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
    padding: 8px 12px;
    font-family: 'Segoe UI', 'Inter', sans-serif;
    font-size: 12px;
    min-width: 150px;
}}

QComboBox QLineEdit {{
    background-color: transparent;
    color: {COLORS["text_primary"]};
    border: none;
    font-family: 'Segoe UI', 'Inter', sans-serif;
    font-size: 12px;
}}

QComboBox::drop-down {{
    border: none;
    width: 24px;
}}

QComboBox QAbstractItemView {{
    background-color: {COLORS["bg_panel"]};
    color: {COLORS["text_primary"]};
    border: 1px solid {COLORS["border"]};
    selection-background-color: {COLORS["accent_es_dim"]};
}}

QComboBox:disabled {{
    color: #475569;
}}

QStatusBar {{
    background-color: {COLORS["bg_panel"]};
    color: {COLORS["text_secondary"]};
    border-top: 1px solid {COLORS["border"]};
    font-family: 'Segoe UI', 'Inter', sans-serif;
    font-size: 11px;
    padding: 4px;
}}

QScrollArea {{
    background-color: {COLORS["bg_text"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 10px;
}}

QScrollBar:vertical {{
    background: {COLORS["bg_panel"]};
    width: 8px;
    border-radius: 4px;
    margin: 0px;
}}

QScrollBar::handle:vertical {{
    background: {COLORS["border"]};
    border-radius: 4px;
    min-height: 20px;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
"""


# ── Widget: Indicador "En Vivo" animado ──────────────────────────────────────

class LiveIndicator(QWidget):
    """Punto circular parpadeante que indica estado de escucha activa."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(12, 12)
        self._opacity = 1.0
        self._color = QColor(COLORS["live_red"])
        self._visible_dot = False

        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._blink)
        self._blink_state = True

    def set_active(self, active: bool):
        self._visible_dot = active
        if active:
            self._anim_timer.start(600)
        else:
            self._anim_timer.stop()
        self.update()

    def _blink(self):
        self._blink_state = not self._blink_state
        self.update()

    def paintEvent(self, event):
        if not self._visible_dot:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(COLORS["live_red"])
        if not self._blink_state:
            color.setAlpha(80)
        painter.setBrush(QBrush(color))
        painter.setPen(QPen(Qt.PenStyle.NoPen))
        painter.drawEllipse(1, 1, 10, 10)


# ── Widget: Toast de notificación ───────────────────────────────────────────

class ToastNotification(QFrame):
    """Banner temporal que aparece y desaparece con animación para errores o avisos."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("toast")
        self.setStyleSheet("""
            QFrame#toast {
                background-color: #450a0a;
                border: 1px solid #f43f5e;
                border-radius: 8px;
                padding: 4px;
            }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)

        self._icon_lbl = QLabel("❌")
        self._icon_lbl.setStyleSheet("font-size: 14px; background: transparent; border: none;")
        layout.addWidget(self._icon_lbl)

        self._msg_lbl = QLabel("")
        self._msg_lbl.setStyleSheet(
            f"color: #fca5a5; font-size: 12px; font-family: 'Segoe UI', sans-serif;"
            f"background: transparent; border: none;"
        )
        self._msg_lbl.setWordWrap(True)
        layout.addWidget(self._msg_lbl, stretch=1)

        close_btn = QPushButton("×")
        close_btn.setFixedSize(20, 20)
        close_btn.setStyleSheet(
            "background: transparent; color: #fca5a5; font-size: 16px;"
            "border: none; padding: 0; min-width: 0;"
        )
        close_btn.clicked.connect(self.hide_toast)
        layout.addWidget(close_btn)

        self.hide()
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide_toast)

    def show_toast(self, message: str, icon: str = "❌", duration_ms: int = 6000):
        self._icon_lbl.setText(icon)
        self._msg_lbl.setText(message)
        self.show()
        self._hide_timer.start(duration_ms)

    def hide_toast(self):
        self._hide_timer.stop()
        self.hide()


# ── Widget: Botón flotante "Nuevos mensajes" ─────────────────────────────────

class NewMessagesButton(QPushButton):
    """Botón flotante que aparece cuando hay nuevos mensajes y el usuario ha scrolleado arriba."""

    def __init__(self, parent=None):
        super().__init__("⬇  Nuevos mensajes", parent)
        self.setObjectName("newMsgBtn")
        self.setFixedHeight(32)
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(12)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(0, 0, 0, 120))
        self.setGraphicsEffect(shadow)
        self.hide()


# ── Ventana principal ────────────────────────────────────────────────────────

class InterpreterApp(QMainWindow):
    """Ventana principal del asistente de interpretación."""

    def __init__(self, engine):
        super().__init__()
        self._engine = engine
        self._worker = None
        self._es_count = 0
        self._en_count = 0
        self._is_running = False
        self._current_lang = "auto"
        self._user_scrolled_up = False  # Para el scroll inteligente
        self._is_animating_scroll = False
        self._rows = []
        self._log_file_raw = None
        self._log_file_ui = None
        self._active_labels = {"es": None, "en": None}
        self._active_time_labels = {"es": None, "en": None}
        self._active_widgets = {"es": None, "en": None}
        self._init_ui()

    # ── Construcción de la UI ────────────────────────────────────────────

    def _init_ui(self):
        self.setWindowTitle("Intérprete en Tiempo Real — ES ↔ EN")
        self.setMinimumSize(QSize(500, 500))
        self.resize(960, 720)
        self.setStyleSheet(STYLESHEET)

        central = QWidget()
        central.setObjectName("rootWidget")
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(18, 14, 18, 8)
        root_layout.setSpacing(10)

        # ── Encabezado ───────────────────────────────────────────────────
        header_layout = QHBoxLayout()
        header_layout.setSpacing(10)

        title_block = QVBoxLayout()
        title_block.setSpacing(2)

        header_lbl = QLabel("🎧  Intérprete en Tiempo Real")
        header_lbl.setObjectName("headerLabel")
        title_block.addWidget(header_lbl)

        subtitle = QLabel("Transcripción automática · Español ↔ Inglés")
        subtitle.setObjectName("subtitleLabel")
        title_block.addWidget(subtitle)

        header_layout.addLayout(title_block)
        header_layout.addStretch()

        # Indicador de estado "EN VIVO"
        self._live_indicator = LiveIndicator()
        self._live_label = QLabel("EN VIVO")
        self._live_label.setStyleSheet(
            f"color: {COLORS['live_red']}; font-family: 'Segoe UI'; font-size: 11px;"
            f"font-weight: bold; letter-spacing: 1px;"
        )
        self._live_label.hide()
        self._live_indicator.hide()

        live_layout = QHBoxLayout()
        live_layout.setSpacing(5)
        live_layout.addWidget(self._live_indicator)
        live_layout.addWidget(self._live_label)
        header_layout.addLayout(live_layout)

        root_layout.addLayout(header_layout)

        # ── Toast de error (debajo del header, arriba de controles) ─────
        self._toast = ToastNotification()
        root_layout.addWidget(self._toast)

        # ── Separador ────────────────────────────────────────────────────
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet(f"color: {COLORS['border']}; background-color: {COLORS['border']}; border: none; max-height: 1px;")
        root_layout.addWidget(separator)

        # ── Barra de controles (Responsiva) ──────────────────────────────
        self._controls_container = QWidget()
        self._controls_container.setStyleSheet("background: transparent;")
        controls_main_layout = QVBoxLayout(self._controls_container)
        controls_main_layout.setContentsMargins(0, 0, 0, 0)
        controls_main_layout.setSpacing(6)

        row1_widget = QWidget()
        row1_widget.setStyleSheet("background: transparent;")
        self._controls_row1_layout = QHBoxLayout(row1_widget)
        self._controls_row1_layout.setContentsMargins(0, 0, 0, 0)
        self._controls_row1_layout.setSpacing(8)

        self._row2_container = QWidget()
        self._row2_container.setStyleSheet("background: transparent;")
        self._controls_row2_layout = QHBoxLayout(self._row2_container)
        self._controls_row2_layout.setContentsMargins(0, 0, 0, 0)
        self._controls_row2_layout.setSpacing(8)

        # Selector de dispositivo
        self._device_combo = QComboBox()
        self._device_combo.setEditable(True)
        self._device_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._device_combo.lineEdit().setPlaceholderText("Escribe para buscar un proceso...")
        # QCompleter is created automatically when setEditable(True)
        completer = self._device_combo.completer()
        if completer:
            completer.setCompletionMode(completer.CompletionMode.PopupCompletion)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._populate_devices()

        # Botón refrescar dispositivos
        self._refresh_btn = QPushButton("🔄")
        self._refresh_btn.setObjectName("refreshBtn")
        self._refresh_btn.setToolTip("Actualizar lista de dispositivos")
        self._refresh_btn.clicked.connect(self._on_refresh_devices)

        # Botón Cambiar Idioma (Toggle)
        self._lang_toggle_btn = QPushButton("Idioma: ✨ AUTO")
        self._lang_toggle_btn.setObjectName("langToggleBtn")
        self._lang_toggle_btn.setToolTip("Cambiar idioma de transcripción (AUTO/ES/EN)")
        self._lang_toggle_btn.setStyleSheet(f"""
            QPushButton#langToggleBtn {{
                background-color: {COLORS["accent_auto"]};
                color: white;
            }}
            QPushButton#langToggleBtn:hover {{
                background-color: #0d9488;
            }}
        """)
        self._lang_toggle_btn.clicked.connect(self._on_toggle_language)

        # Botón Iniciar
        self._start_btn = QPushButton("▶  Iniciar")
        self._start_btn.clicked.connect(self._on_start)

        # Botón Detener
        self._stop_btn = QPushButton("■  Detener")
        self._stop_btn.setObjectName("stopBtn")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)

        # Botón Limpiar
        self._clear_btn = QPushButton("🗑  Limpiar")
        self._clear_btn.setObjectName("clearBtn")
        self._clear_btn.clicked.connect(self._on_clear)

        # Insertamos en el layout inicial (se reordenará en _update_responsive_layout)
        self._controls_row1_layout.addWidget(self._device_combo, stretch=1)
        self._controls_row1_layout.addWidget(self._refresh_btn)
        self._controls_row1_layout.addWidget(self._lang_toggle_btn)
        self._controls_row1_layout.addWidget(self._start_btn)
        self._controls_row1_layout.addWidget(self._stop_btn)
        self._controls_row1_layout.addWidget(self._clear_btn)

        controls_main_layout.addWidget(row1_widget)
        controls_main_layout.addWidget(self._row2_container)
        self._row2_container.hide()

        root_layout.addWidget(self._controls_container)

        # ── Cabeceras de columna ─────────────────────────────────────────
        headers_layout = QHBoxLayout()
        headers_layout.setContentsMargins(0, 2, 0, 2)
        headers_layout.setSpacing(10)

        self._es_header = QLabel("  🇪🇸  ESPAÑOL")
        self._es_header.setStyleSheet(
            f"color: {COLORS['accent_es']}; font-weight: bold; font-size: 13px;"
            f"background-color: {COLORS['accent_es_dim']}; border-radius: 6px;"
            f"padding: 5px 14px; border: 1px solid {COLORS['border']};"
        )
        self._es_header.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        headers_layout.addWidget(self._es_header, stretch=1)

        self._en_header = QLabel("  🇺🇸  ENGLISH")
        self._en_header.setStyleSheet(
            f"color: {COLORS['accent_en']}; font-weight: bold; font-size: 13px;"
            f"background-color: {COLORS['accent_en_dim']}; border-radius: 6px;"
            f"padding: 5px 14px; border: 1px solid {COLORS['border']};"
        )
        self._en_header.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        headers_layout.addWidget(self._en_header, stretch=1)
        root_layout.addLayout(headers_layout)

        # ── Área de scroll unificada ─────────────────────────────────────
        scroll_container = QWidget()
        scroll_container.setStyleSheet("background: transparent;")
        scroll_container_layout = QVBoxLayout(scroll_container)
        scroll_container_layout.setContentsMargins(0, 0, 0, 0)
        scroll_container_layout.setSpacing(0)

        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # Detectar scroll manual del usuario
        self._scroll_area.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)

        self._scroll_widget = QWidget()
        self._scroll_widget.setStyleSheet(f"background-color: {COLORS['bg_text']};")
        self._scroll_layout = QVBoxLayout(self._scroll_widget)
        self._scroll_layout.setContentsMargins(10, 10, 10, 10)
        self._scroll_layout.setSpacing(0)

        # ── Estado vacío ─────────────────────────────────────────────────
        self._empty_state = QWidget()
        self._empty_state.setStyleSheet("background: transparent;")
        empty_layout = QVBoxLayout(self._empty_state)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.setSpacing(10)

        empty_icon = QLabel("🎙️")
        empty_icon.setStyleSheet("font-size: 48px; background: transparent; border: none;")
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_icon)

        empty_title = QLabel("Sin transcripciones aún")
        empty_title.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 16px; font-weight: bold;"
            f"font-family: 'Segoe UI', sans-serif; background: transparent; border: none;"
        )
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)

        empty_hint = QLabel(
            "Selecciona un dispositivo de audio y presiona\n▶ Iniciar para comenzar a transcribir."
        )
        empty_hint.setStyleSheet(
            f"color: {COLORS['text_timestamp']}; font-size: 12px;"
            f"font-family: 'Segoe UI', sans-serif; background: transparent; border: none;"
        )
        empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_hint)

        self._scroll_layout.addStretch()
        self._scroll_layout.addWidget(self._empty_state)
        self._scroll_layout.addStretch()

        self._scroll_area.setWidget(self._scroll_widget)
        scroll_container_layout.addWidget(self._scroll_area)

        # Botón flotante "Nuevos mensajes"
        self._new_msg_btn = NewMessagesButton(self._scroll_area)
        self._new_msg_btn.clicked.connect(self._scroll_to_bottom)
        self._reposition_new_msg_btn()

        root_layout.addWidget(scroll_container, stretch=1)

        # ── Pie: contadores ──────────────────────────────────────────────
        counters_layout = QHBoxLayout()
        self._es_counter = QLabel("0 segmentos")
        self._es_counter.setStyleSheet(
            f"color: {COLORS['text_timestamp']}; font-size: 11px;"
            f"font-family: 'Segoe UI', sans-serif;"
        )
        self._es_counter.setAlignment(Qt.AlignmentFlag.AlignLeft)
        counters_layout.addWidget(self._es_counter, stretch=1)

        self._en_counter = QLabel("0 segments")
        self._en_counter.setStyleSheet(
            f"color: {COLORS['text_timestamp']}; font-size: 11px;"
            f"font-family: 'Segoe UI', sans-serif;"
        )
        self._en_counter.setAlignment(Qt.AlignmentFlag.AlignRight)
        counters_layout.addWidget(self._en_counter, stretch=1)
        root_layout.addLayout(counters_layout)

        # ── Barra de estado ──────────────────────────────────────────────
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._set_status("Listo. Selecciona un dispositivo e inicia.", "neutral")
        self._update_responsive_layout(self.width())

    # ── Helpers de UI ────────────────────────────────────────────────────

    def _populate_devices(self):
        self._device_combo.clear()
        self._device_combo.addItem("Selecciona un proceso...", None)
        procs = list_audio_processes()
        logger.info(f"[_populate_devices] Procesos con ventana encontrados: {len(procs)}")
        for proc in procs:
            logger.info(f"  -> {proc['name']} (PID:{proc['pid']}) | {proc.get('title','')[:60]}")
            label = f"💻 {proc['title']} ({proc['name']} — PID: {proc['pid']})"
            self._device_combo.addItem(label, proc['pid'])

    def _set_status(self, message: str, kind: str = "neutral"):
        """Actualiza la barra de estado con color según el tipo de mensaje."""
        colors = {
            "neutral": COLORS["text_secondary"],
            "success": COLORS["success"],
            "error": COLORS["error"],
            "warning": COLORS["warning"],
            "live": COLORS["live_red"],
        }
        color = colors.get(kind, COLORS["text_secondary"])
        self._status_bar.setStyleSheet(
            f"QStatusBar {{ background-color: {COLORS['bg_panel']}; color: {color};"
            f"border-top: 1px solid {COLORS['border']};"
            f"font-family: 'Segoe UI', 'Inter', sans-serif; font-size: 11px; padding: 4px; }}"
        )
        self._status_bar.showMessage(message)

    def _set_live_state(self, active: bool):
        """Muestra u oculta el indicador 'EN VIVO' y cambia el borde del scroll."""
        self._live_indicator.set_active(active)
        if active:
            self._live_indicator.show()
            self._live_label.show()
            self._scroll_area.setStyleSheet(
                f"QScrollArea {{ background-color: {COLORS['bg_text']};"
                f"border: 1px solid {COLORS['accent_es']}44;"
                f"border-radius: 10px; }}"
                + self._scroll_bar_style()
            )
        else:
            self._live_indicator.hide()
            self._live_label.hide()
            self._scroll_area.setStyleSheet(
                f"QScrollArea {{ background-color: {COLORS['bg_text']};"
                f"border: 1px solid {COLORS['border']};"
                f"border-radius: 10px; }}"
                + self._scroll_bar_style()
            )

    def _scroll_bar_style(self):
        return (
            f"QScrollBar:vertical {{ background: {COLORS['bg_panel']}; width: 8px;"
            f"border-radius: 4px; margin: 0px; }}"
            f"QScrollBar::handle:vertical {{ background: {COLORS['border']}; border-radius: 4px; min-height: 20px; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}"
        )

    def _reposition_new_msg_btn(self):
        """Posiciona el botón flotante en el centro-inferior del scroll."""
        sa = self._scroll_area
        btn = self._new_msg_btn
        bw = 180
        btn.setFixedWidth(bw)
        btn.move(
            (sa.width() - bw) // 2,
            sa.height() - 52,
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_responsive_layout(self.width())
        self._reposition_new_msg_btn()

    def _update_responsive_layout(self, width: int):
        is_narrow = width < NARROW_LAYOUT_THRESHOLD

        # 1. Mostrar/ocultar cabeceras de columna
        self._es_header.setVisible(not is_narrow)
        self._en_header.setVisible(not is_narrow)

        # 2. Reordenar barra de controles
        if is_narrow:
            # Fila 1: Combo de dispositivo + refrescar
            self._controls_row1_layout.addWidget(self._device_combo, stretch=1)
            self._controls_row1_layout.addWidget(self._refresh_btn)
            # Fila 2: Acciones
            self._controls_row2_layout.addWidget(self._lang_toggle_btn)
            self._controls_row2_layout.addWidget(self._start_btn)
            self._controls_row2_layout.addWidget(self._stop_btn)
            self._controls_row2_layout.addWidget(self._clear_btn)
            self._row2_container.show()
        else:
            # Fila 1 completa
            self._controls_row1_layout.addWidget(self._device_combo, stretch=1)
            self._controls_row1_layout.addWidget(self._refresh_btn)
            self._controls_row1_layout.addWidget(self._lang_toggle_btn)
            self._controls_row1_layout.addWidget(self._start_btn)
            self._controls_row1_layout.addWidget(self._stop_btn)
            self._controls_row1_layout.addWidget(self._clear_btn)
            self._row2_container.hide()

        # 3. Adaptar filas de transcripción existentes
        for row_widget, es_cell, en_cell, is_es in self._rows:
            if is_narrow:
                if is_es:
                    en_cell.setVisible(False)
                else:
                    es_cell.setVisible(False)
            else:
                es_cell.setVisible(True)
                en_cell.setVisible(True)

    # ── Scroll inteligente ───────────────────────────────────────────────

    def _on_scroll_changed(self, value: int):
        if getattr(self, "_is_animating_scroll", False):
            return
        sb = self._scroll_area.verticalScrollBar()
        at_bottom = value >= sb.maximum() - 10
        self._user_scrolled_up = not at_bottom
        if at_bottom:
            self._new_msg_btn.hide()

    def _scroll_to_bottom(self):
        self._user_scrolled_up = False
        sb = self._scroll_area.verticalScrollBar()
        
        if not hasattr(self, "_scroll_anim"):
            self._scroll_anim = QPropertyAnimation(sb, b"value")
            self._scroll_anim.setDuration(250)
            self._scroll_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._scroll_anim.finished.connect(self._on_scroll_anim_finished)
            
        self._is_animating_scroll = True
        self._scroll_anim.stop()
        self._scroll_anim.setStartValue(sb.value())
        self._scroll_anim.setEndValue(sb.maximum())
        self._scroll_anim.start()
        self._new_msg_btn.hide()

    def _on_scroll_anim_finished(self):
        self._is_animating_scroll = False

    def _auto_scroll(self):
        """Hace scroll al final solo si el usuario no subió manualmente."""
        if self._user_scrolled_up:
            self._new_msg_btn.show()
            self._reposition_new_msg_btn()
        else:
            self._scroll_to_bottom()

    # ── Slots ────────────────────────────────────────────────────────────

    def _on_refresh_devices(self):
        """Refresca la lista de dispositivos de audio."""
        current = self._device_combo.currentData()
        self._populate_devices()
        # Intentar re-seleccionar el mismo dispositivo
        for i in range(self._device_combo.count()):
            if self._device_combo.itemData(i) == current:
                self._device_combo.setCurrentIndex(i)
                break
        self._set_status("Lista de procesos actualizada.", "neutral")

    def _on_start(self):
        """Inicia la captura y transcripción en un hilo secundario."""
        logger.info("Botón Iniciar presionado. Iniciando worker...")
        
        target_pid = self._device_combo.currentData()
        current_text = self._device_combo.currentText()
        
        if not target_pid and current_text:
            # Si el usuario escribió algo pero no seleccionó explícitamente,
            # intentamos buscar el item que coincida con el texto
            index = self._device_combo.findText(current_text, Qt.MatchFlag.MatchContains)
            if index >= 0:
                target_pid = self._device_combo.itemData(index)

        if not target_pid:
            self._toast.show_toast("Selecciona o escribe un proceso válido primero.", duration_ms=4000)
            return
        
        try:
            os.makedirs("logs", exist_ok=True)
            session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            raw_path = os.path.join("logs", f"sesion_{session_id}_texto_completo.txt")
            ui_path = os.path.join("logs", f"sesion_{session_id}_interfaz_ui.txt")
            
            self._log_file_raw = open(raw_path, "a", encoding="utf-8")
            self._log_file_ui = open(ui_path, "a", encoding="utf-8")
        except Exception as e:
            logger.error(f"Error al crear archivos de log: {e}")
            self._log_file_raw = None
            self._log_file_ui = None

        self._toast.hide_toast()

        self._worker = AudioWorker(
            engine=self._engine,
            target_pid=target_pid,
            initial_lang=self._current_lang,
        )

        self._worker.transcribed.connect(self._on_transcribed)
        self._worker.status.connect(self._on_worker_status)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._on_worker_finished)

        logger.debug("Iniciando hilo del worker...")
        self._worker.start()
        logger.debug("Hilo del worker iniciado.")

        self._is_running = True
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._device_combo.setEnabled(False)
        self._refresh_btn.setEnabled(False)

        self._set_status("⟳  Iniciando motor de IA...", "neutral")

    def _on_stop(self):
        """Detiene la captura de forma segura."""
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._set_status("Deteniendo...", "warning")

    def _on_clear(self):
        """Limpia el área de transcripciones."""
        # Eliminar todos los widgets de filas de contenido
        # Mantenemos los 3 elementos originales: stretch, empty_state, stretch
        while self._scroll_layout.count() > 3:
            item = self._scroll_layout.takeAt(3)
            if item.widget():
                item.widget().deleteLater()

        self._rows.clear()
        self._es_count = 0
        self._en_count = 0
        self._es_counter.setText("0 segmentos")
        self._en_counter.setText("0 segments")
        self._active_labels = {"es": None, "en": None}
        self._active_time_labels = {"es": None, "en": None}
        self._active_widgets = {"es": None, "en": None}
        self._empty_state.show()

    def _on_toggle_language(self):
        """Alterna el idioma de transcripción entre AUTO, ES y EN."""
        if self._current_lang == "auto":
            self._current_lang = "es"
            self._lang_toggle_btn.setText("Idioma: 🇪🇸 ES")
            self._lang_toggle_btn.setStyleSheet(f"""
                QPushButton#langToggleBtn {{
                    background-color: {COLORS["accent_es"]};
                    color: white;
                }}
                QPushButton#langToggleBtn:hover {{
                    background-color: #2563eb;
                }}
            """)
            self._toast.show_toast("Idioma cambiado a ESPAÑOL 🇪🇸", icon="ℹ️", duration_ms=3000)
        elif self._current_lang == "es":
            self._current_lang = "en"
            self._lang_toggle_btn.setText("Idioma: 🇺🇸 EN")
            self._lang_toggle_btn.setStyleSheet(f"""
                QPushButton#langToggleBtn {{
                    background-color: {COLORS["accent_en"]};
                    color: white;
                }}
                QPushButton#langToggleBtn:hover {{
                    background-color: #7c3aed;
                }}
            """)
            self._toast.show_toast("Idioma cambiado a INGLÉS 🇺🇸", icon="ℹ️", duration_ms=3000)
        else:
            self._current_lang = "auto"
            self._lang_toggle_btn.setText("Idioma: ✨ AUTO")
            self._lang_toggle_btn.setStyleSheet(f"""
                QPushButton#langToggleBtn {{
                    background-color: {COLORS["accent_auto"]};
                    color: white;
                }}
                QPushButton#langToggleBtn:hover {{
                    background-color: #0d9488;
                }}
            """)
            self._toast.show_toast("Idioma cambiado a AUTO ✨", icon="ℹ️", duration_ms=3000)

        # Notificar al worker si está activo
        if self._worker and self._worker.isRunning():
            self._worker.set_language(self._current_lang)

    def _on_transcribed(self, language: str, text: str, translated, start_time: float, end_time: float, is_final: bool):
        """Recibe una transcripción parcial o final y la agrega o actualiza en el scroll."""
        ts_start = datetime.fromtimestamp(start_time).strftime("%H:%M:%S")
        ts_end   = datetime.fromtimestamp(end_time).strftime("%H:%M:%S")
        duration_s = end_time - start_time
        time_label = f"{ts_start} → {ts_end}  ◔ {duration_s:.1f}s"

        active_lbl = self._active_labels.get(language)
        active_time_lbl = self._active_time_labels.get(language)

        if active_lbl is not None:
            # Actualizar burbuja activa existente
            active_lbl.setText(text)
            if active_time_lbl is not None:
                active_time_lbl.setText(time_label)
        else:
            # Crear nueva burbuja
            row_widget, txt_lbl, ts_lbl = self._append_row(language, time_label, text)
            self._active_labels[language] = txt_lbl
            self._active_time_labels[language] = ts_lbl
            self._active_widgets[language] = row_widget

            if language == "es":
                self._es_count += 1
                self._es_counter.setText(f"{self._es_count} segmento{'s' if self._es_count != 1 else ''}")
            elif language == "en":
                self._en_count += 1
                self._en_counter.setText(f"{self._en_count} segment{'s' if self._en_count != 1 else ''}")

        if is_final:
            # Sellar burbuja
            self._active_labels[language] = None
            self._active_time_labels[language] = None
            self._active_widgets[language] = None

            try:
                if self._log_file_raw:
                    self._log_file_raw.write(text + " ")
                    self._log_file_raw.flush()
                if self._log_file_ui:
                    lang_tag = "[ES]" if language == "es" else "[EN]"
                    self._log_file_ui.write(f"[{time_label}] {lang_tag} {text}\n")
                    self._log_file_ui.flush()
            except Exception as e:
                logger.error(f"Error al escribir en logs de sesión: {e}")

        # Activar indicador en vivo en cuanto llegue la primera transcripción
        if not self._live_indicator._visible_dot and self._is_running:
            self._set_live_state(True)
            self._set_status("● Escuchando y transcribiendo en tiempo real...", "live")

    def _on_worker_status(self, message: str):
        """Muestra un mensaje de estado del worker."""
        # Activar indicador live cuando el worker confirma que está escuchando
        if "Escuchando" in message or "Dispositivo:" in message:
            self._set_live_state(True)
            self._set_status(f"● {message}", "live")
        elif "Inicializando" in message:
            self._set_status(f"⟳  {message}", "neutral")
        else:
            self._set_status(message, "neutral")

    def _on_error(self, message: str):
        """Muestra un error como toast prominente y en la barra de estado."""
        self._toast.show_toast(message, icon="❌", duration_ms=8000)
        self._set_status(f"Error: {message}", "error")
        self._on_worker_finished()

    def _on_worker_finished(self):
        """Restaura los controles cuando el worker termina."""
        if self._log_file_raw:
            try:
                self._log_file_raw.close()
            except Exception:
                pass
            self._log_file_raw = None
            
        if self._log_file_ui:
            try:
                self._log_file_ui.close()
            except Exception:
                pass
            self._log_file_ui = None

        self._is_running = False
        self._set_live_state(False)
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._device_combo.setEnabled(True)
        self._refresh_btn.setEnabled(True)
        if "Deteniendo" in self._status_bar.currentMessage():
            self._set_status("✓ Detenido. Listo para iniciar de nuevo.", "success")

    # ── Helpers ──────────────────────────────────────────────────────────

    def _append_row(self, language: str, time_label: str, text: str) -> tuple[QWidget, QLabel, QLabel]:
        """
        Inserta una fila cronológica en el scroll unificado.
        Cada fila tiene dos celdas: izquierda (ES) y derecha (EN).
        Solo una celda tiene contenido; la otra queda vacía para preservar
        el orden cronológico visual entre los dos idiomas.
        """
        # Ocultar estado vacío al agregar la primera fila
        if self._empty_state.isVisible():
            self._empty_state.hide()

        is_es      = (language == "es")
        accent     = COLORS["accent_es"]    if is_es else COLORS["accent_en"]
        accent_dim = COLORS["accent_es_dim"] if is_es else COLORS["accent_en_dim"]
        flag_prefix = "🇪🇸 ES  •  " if is_es else "🇺🇸 EN  •  "
        display_tag = f"{flag_prefix}{time_label}"

        # Contenedor de fila
        row_widget = QWidget()
        row_widget.setStyleSheet("background: transparent;")
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 3, 0, 3)
        row_layout.setSpacing(10)

        def make_cell(filled: bool) -> tuple[QFrame, Optional[QLabel], Optional[QLabel]]:
            frame = QFrame()
            frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            cell_layout = QVBoxLayout(frame)
            cell_layout.setContentsMargins(12, 10, 12, 10)
            cell_layout.setSpacing(6)

            txt_lbl = None
            ts_lbl = None

            if filled:
                frame.setStyleSheet(
                    f"QFrame {{"
                    f"  border: 1px solid {COLORS['border']};"
                    f"  border-left: 4px solid {accent};"
                    f"  background-color: {accent_dim}1a;"
                    f"  border-radius: 12px;"
                    f"}}"
                )

                # Header container for metadata & copy button
                header_widget = QWidget()
                header_widget.setStyleSheet("background: transparent; border: none;")
                header_layout = QHBoxLayout(header_widget)
                header_layout.setContentsMargins(0, 0, 0, 0)
                header_layout.setSpacing(6)

                # Etiqueta de tiempo e idioma
                ts_lbl = QLabel(display_tag)
                ts_lbl.setStyleSheet(
                    f"color: {COLORS['text_timestamp']}; font-size: 10px;"
                    f"font-family: 'Segoe UI', 'Inter', sans-serif;"
                    f"border: none; background: transparent; letter-spacing: 0.3px;"
                )
                header_layout.addWidget(ts_lbl)
                header_layout.addStretch()

                # Botón de copiar
                copy_btn = QPushButton("📋")
                copy_btn.setFixedSize(20, 20)
                copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                copy_btn.setToolTip("Copiar texto")
                copy_btn.setStyleSheet("""
                    QPushButton {
                        background: transparent;
                        border: none;
                        color: #64748b;
                        font-size: 11px;
                        min-width: 0px;
                        padding: 0px;
                    }
                    QPushButton:hover {
                        color: #e2e8f0;
                    }
                """)
                header_layout.addWidget(copy_btn)
                cell_layout.addWidget(header_widget)

                # Texto de la transcripción
                txt_lbl = QLabel(text)
                txt_lbl.setWordWrap(True)
                txt_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                txt_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                txt_lbl.setStyleSheet(
                    f"color: {COLORS['text_primary']}; font-size: 14px; line-height: 1.5;"
                    f"font-family: 'Segoe UI', 'Inter', sans-serif;"
                    f"border: none; background: transparent;"
                )
                cell_layout.addWidget(txt_lbl)

                # Conectar el botón de copiado con el label del texto actual
                copy_btn.clicked.connect(lambda: self._copy_to_clipboard(txt_lbl.text(), copy_btn))
            else:
                # Celda vacía: placeholder invisible
                frame.setStyleSheet("border: none; background: transparent;")
                spacer_lbl = QLabel("")
                spacer_lbl.setMinimumHeight(1)
                cell_layout.addWidget(spacer_lbl)

            return frame, txt_lbl, ts_lbl

        es_cell, es_txt, es_ts = make_cell(filled=is_es)
        en_cell, en_txt, en_ts = make_cell(filled=not is_es)
        row_layout.addWidget(es_cell, stretch=1)
        row_layout.addWidget(en_cell, stretch=1)

        # Aplicar el modo de visualización responsivo actual
        is_narrow = (self.width() < NARROW_LAYOUT_THRESHOLD)
        if is_narrow:
            if is_es:
                en_cell.setVisible(False)
            else:
                es_cell.setVisible(False)

        # Guardar referencia para redimensionamientos dinámicos
        self._rows.append((row_widget, es_cell, en_cell, is_es))

        # Insertar ANTES del stretch final (índice count-1 corresponde al último stretch)
        self._scroll_layout.insertWidget(self._scroll_layout.count(), row_widget)

        # Scroll inteligente: solo si el usuario no subió
        QTimer.singleShot(50, self._auto_scroll)

        txt_lbl = es_txt if is_es else en_txt
        ts_lbl = es_ts if is_es else en_ts
        return row_widget, txt_lbl, ts_lbl

    def _copy_to_clipboard(self, text: str, button: QPushButton):
        """Copia el texto dado al portapapeles y provee una microinteracción de confirmación."""
        QApplication.clipboard().setText(text)
        button.setText("✓")
        button.setToolTip("¡Copiado!")
        # Restaurar el botón original después de 1.5s
        QTimer.singleShot(1500, lambda: (button.setText("📋"), button.setToolTip("Copiar texto")))
        self._set_status("✓ Texto copiado al portapapeles.", "success")

    # ── Cierre ───────────────────────────────────────────────────────────

    def closeEvent(self, event):
        """Al cerrar la ventana, detener el worker si está corriendo."""
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)
        event.accept()


# ── Entry Point ──────────────────────────────────────────────────────────────

def global_exception_handler(exc_type, exc_value, exc_traceback):
    """Manejador global para excepciones no capturadas."""
    logger.error("Excepción no capturada:", exc_info=(exc_type, exc_value, exc_traceback))
    sys.__excepthook__(exc_type, exc_value, exc_traceback)


def run_app(engine):
    """Punto de entrada llamado desde bootstrap.py con el engine ya cargado."""
    app = QApplication(sys.argv)
    logger.debug("QApplication instanciada")
    app.setStyle("Fusion")

    window = InterpreterApp(engine)
    window.show()
    sys.exit(app.exec())


# Mantener compatibilidad si se ejecuta directamente (sin CUDA)
if __name__ == "__main__":
    print("⚠️  Para usar CUDA, ejecuta: py bootstrap.py")
    print("    Ejecutando sin CUDA (CPU) como fallback...")
    from ai_engine import AIEngine
    engine = AIEngine(model_size="small", device="cpu", compute_type="int8")
    run_app(engine)
