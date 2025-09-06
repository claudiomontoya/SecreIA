# app/main.py - Versión mejorada
from collections import deque
import os
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("CHROMADB_TELEMETRY_IMPLEMENTATION", "noop")
os.environ.setdefault("CHROMADB_DISABLE_TELEMETRY", "1")

import re
import sys
import json
import time
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from enum import Enum
import speech_recognition as sr
import queue
import pytz
from concurrent.futures import ThreadPoolExecutor, Future
from PySide6.QtCore import (
        Qt, QSize, QTimer, QPropertyAnimation, QEasingCurve, 
        QRect, QThread, Signal, QCoreApplication, QPoint
    )
from PySide6.QtGui import (
        QAction, QIcon, QKeySequence, QPalette, QFont, QPixmap, 
        QPainter, QBrush, QColor, QPen, QTextCursor
    )
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QTextEdit, QComboBox, QListWidget,
    QListWidgetItem, QFrame, QStackedWidget, QFormLayout, QSpinBox,
    QMessageBox, QFileDialog, QStyle, QStyledItemDelegate, QMenu, QCheckBox,
    QSplitter, QToolBar, QGroupBox, QProgressBar, QInputDialog, QSlider,
    QStatusBar, QGridLayout, QDialog, QScrollArea, QSizePolicy, QSpacerItem
)
import pygame
import tempfile
import subprocess


from app.settings import Settings
from app.db import NotesDB, Note
from app.ai import AIService
from app.vectorstore import VectorIndex

import pyperclip

APP_NAME = "SecreIA"

def format_date_chile(date_str: str) -> str:
    """Formatea fecha para Chile con información consistente"""
    try:

        chile_tz = pytz.timezone('America/Santiago')
        
        # Parsear la fecha
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        
        # Si no tiene zona horaria, asumir que es UTC y convertir a Chile
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=pytz.UTC)
        
        # Convertir a hora de Chile
        dt_chile = dt.astimezone(chile_tz)
        now_chile = datetime.now(chile_tz)
        diff = now_chile - dt_chile
        
        # Mapeo de meses en español
        months_es = [
            'ene', 'feb', 'mar', 'abr', 'may', 'jun',
            'jul', 'ago', 'sep', 'oct', 'nov', 'dic'
        ]
        
        if diff.days == 0:
            # Hoy - mostrar "Hoy HH:MM"
            return f"Hoy {dt_chile.strftime('%H:%M')}"
        elif diff.days == 1:
            # Ayer - mostrar "Ayer HH:MM"
            return f"Ayer {dt_chile.strftime('%H:%M')}"
        elif diff.days <= 7:
            # Esta semana - mostrar "DD/MM HH:MM"
            return f"{dt_chile.day:02d}/{dt_chile.month:02d} {dt_chile.strftime('%H:%M')}"
        elif dt_chile.year == now_chile.year:
            # Este año - mostrar "DD MMM HH:MM"
            month_short = months_es[dt_chile.month - 1]
            return f"{dt_chile.day} {month_short} {dt_chile.strftime('%H:%M')}"
        else:
            # Otro año - mostrar "DD/MM/YYYY"
            return f"{dt_chile.day:02d}/{dt_chile.month:02d}/{dt_chile.year}"
            
    except:
        # Fallback a formato original
        return date_str[:10] if date_str else ""
class AppState(Enum):
    FIRST_RUN = "first_run"
    SETUP = "setup" 
    READY = "ready"
    ERROR = "error"

# Colores Apple refinados
class AppleColors:
    # Backgrounds
    SIDEBAR = QColor(42, 42, 47)           
    NOTES_LIST = QColor(30, 30, 32)       
    CONTENT = QColor(28, 28, 30)          
    CARD = QColor(44, 44, 46)             
    ELEVATED = QColor(58, 58, 60)         
    
    # Text
    PRIMARY = QColor(255, 255, 255)       
    SECONDARY = QColor(152, 152, 157)     
    TERTIARY = QColor(99, 99, 102)        
    
    # Accents
    BLUE = QColor(10, 132, 255)          
    GREEN = QColor(48, 209, 88)          
    RED = QColor(255, 69, 58)            
    ORANGE = QColor(255, 159, 10)        
    PURPLE = QColor(191, 90, 242)        
    
    # Separators
    SEPARATOR = QColor(84, 84, 88)        
    SEPARATOR_LIGHT = QColor(58, 58, 60) 

class SafeTimer(QTimer):
    """Timer con manejo seguro de errores"""
    def __init__(self, callback, interval=1000, single_shot=True):
        super().__init__()
        self.callback = callback
        self.setSingleShot(single_shot)
        self.setInterval(interval)
        self.timeout.connect(self._safe_execute)
        
    def _safe_execute(self):
        try:
            self.callback()
        except Exception as e:
            print(f"Error en timer: {e}")
            
class LoadingSpinner(QWidget):
    """Spinner de carga estilo Apple"""
    def __init__(self, size=16):
        super().__init__()
        self.size = size
        self.angle = 0
        self.setFixedSize(size, size)
        
        self.timer = QTimer()
        self.timer.timeout.connect(self.rotate)
        
    def start(self):
        self.timer.start(50)
        self.show()
        
    def stop(self):
        self.timer.stop()
        self.hide()
        
    def rotate(self):
        self.angle = (self.angle + 30) % 360
        self.update()
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        painter.translate(self.size/2, self.size/2)
        painter.rotate(self.angle)
        
        for i in range(8):
            alpha = 255 - (i * 30)
            painter.setPen(QPen(QColor(AppleColors.SECONDARY.red(), 
                                     AppleColors.SECONDARY.green(), 
                                     AppleColors.SECONDARY.blue(), alpha), 2))
            painter.drawLine(0, -self.size/3, 0, -self.size/2.5)
            painter.rotate(45)

class StatusBadge(QLabel):
    """Badge de estado estilo Apple"""
    def __init__(self):
        super().__init__()
        self.setFixedSize(8, 8)
        self.set_status("saved")
    
    def set_status(self, status: str):
        colors = {
            "saved": "transparent",
            "saving": AppleColors.ORANGE.name(),
            "unsaved": AppleColors.RED.name(),
            "syncing": AppleColors.BLUE.name()
        }
        
        self.setStyleSheet(f"""
            QLabel {{
                background-color: {colors.get(status, 'transparent')};
                border-radius: 4px;
            }}
        """)


class SummaryTab(QWidget):
    """Tab de Resumen IA con síntesis de voz - NUEVO"""
    
    def __init__(self, settings: Settings, db: NotesDB, ai: AIService):
        super().__init__()
        self.settings = settings
        self.db = db
        self.ai = ai
        self.audio_file = None
        self.audio_playing = False
        self.audio_thread = None
        self._setup_ui()
        self._init_audio()
    
    def _init_audio(self):
        """Inicializa pygame para audio"""
        try:
            pygame.mixer.init()
        except Exception as e:
            print(f"Error inicializando audio: {e}")
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(32)
        layout.setContentsMargins(60, 60, 60, 60)
        
        # Header
        header = QLabel("Resumen IA")
        header.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.PRIMARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 32px;
                font-weight: 300;
            }}
        """)
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)
        
        # Descripción
        description = QLabel("Genera un resumen inteligente de tus notas de los últimos 3 días con síntesis de voz")
        description.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.SECONDARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 16px;
            }}
        """)
        description.setWordWrap(True)
        description.setAlignment(Qt.AlignCenter)
        layout.addWidget(description)
        
        layout.addStretch()
        
        # Botón principal centrado
        self.btn_generate = AppleButton("🤖 Generar Resumen de últimos 3 días", "primary")
        self.btn_generate.setFixedHeight(60)
        self.btn_generate.setFixedWidth(400)
        self.btn_generate.clicked.connect(self._generate_summary)
        
        if not self.ai.settings.openai_api_key:
            self.btn_generate.setEnabled(False)
            self.btn_generate.setToolTip("Requiere configuración de OpenAI API")
        
        layout.addWidget(self.btn_generate, alignment=Qt.AlignCenter)
        
        # Panel de progreso (inicialmente oculto)
        self.progress_widget = QWidget()
        progress_layout = QVBoxLayout(self.progress_widget)
        progress_layout.setContentsMargins(0, 20, 0, 20)
        
        self.progress_label = QLabel("Analizando notas...")
        self.progress_label.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.SECONDARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 14px;
            }}
        """)
        self.progress_label.setAlignment(Qt.AlignCenter)
        
        self.progress_spinner = LoadingSpinner(24)
        
        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.progress_spinner, alignment=Qt.AlignCenter)
        
        self.progress_widget.hide()
        layout.addWidget(self.progress_widget)
        
        layout.addStretch()
        
        # Área de resumen
        summary_header = QLabel("Resumen generado")
        summary_header.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.PRIMARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 18px;
                font-weight: 600;
            }}
        """)
        layout.addWidget(summary_header)
        
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setPlaceholderText("El resumen aparecerá aquí...")
        self.summary_text.setStyleSheet(f"""
            QTextEdit {{
                background-color: {AppleColors.NOTES_LIST.name()};
                color: {AppleColors.PRIMARY.name()};
                border: none;
                border-radius: 12px;
                padding: 20px;
                font-family: '.AppleSystemUIFont';
                font-size: 15px;
                line-height: 1.6;
            }}
        """)
        layout.addWidget(self.summary_text, 2)
        
        # Controles de audio
        audio_controls = QWidget()
        audio_layout = QHBoxLayout(audio_controls)
        audio_layout.setSpacing(12)
        
        self.btn_play_audio = AppleButton("🔊 Reproducir Audio", "secondary")
        self.btn_play_audio.clicked.connect(self._toggle_audio)
        self.btn_play_audio.setEnabled(False)
        
        self.btn_copy_summary = AppleButton("📋 Copiar Resumen", "ghost")
        self.btn_copy_summary.clicked.connect(self._copy_summary)
        self.btn_copy_summary.setEnabled(False)
        
        audio_layout.addWidget(self.btn_play_audio)
        audio_layout.addWidget(self.btn_copy_summary)
        audio_layout.addStretch()
        
        layout.addWidget(audio_controls)
    
    def _generate_summary(self):
        """Genera resumen de los últimos 3 días"""
        if not self.ai.settings.openai_api_key:
            QMessageBox.information(self, APP_NAME, 
                                  "El resumen IA está deshabilitado.\n"
                                  "Configura tu OpenAI API key en Ajustes.")
            return
        
        # Mostrar progreso
        self._show_progress("Consultando notas de los últimos 3 días...")
        
        # Usar QTimer para no bloquear UI
        QTimer.singleShot(100, self._do_generate_summary)
    
    def _do_generate_summary(self):
        """Ejecuta la generación real del resumen"""
        try:
            # Calcular fecha límite (últimos 3 días)
            chile_tz = pytz.timezone('America/Santiago')
            now = datetime.now(chile_tz)
            three_days_ago = now - timedelta(days=3)
            
            self._update_progress("Obteniendo notas recientes...")
            
            # Obtener todas las notas y filtrar por fecha
            all_notes = self.db.list_notes(limit=10000)
            recent_notes = []
            
            for note in all_notes:
                try:
                    # Parsear fecha de actualización
                    updated_at = note.updated_at.replace('Z', '+00:00')
                    note_date = datetime.fromisoformat(updated_at)
                    
                    # Si no tiene timezone, asumir UTC y convertir a Chile
                    if note_date.tzinfo is None:
                        note_date = note_date.replace(tzinfo=pytz.UTC)
                    
                    note_date_chile = note_date.astimezone(chile_tz)
                    
                    if note_date_chile >= three_days_ago:
                        recent_notes.append(note)
                except Exception:
                    # En caso de error parseando fecha, incluir la nota
                    recent_notes.append(note)
            
            if not recent_notes:
                self._hide_progress()
                self.summary_text.setPlainText("No se encontraron notas en los últimos 3 días.")
                return
            
            self._update_progress(f"Analizando {len(recent_notes)} notas...")
            
            # Preparar contenido para el resumen
            content_parts = []
            for i, note in enumerate(recent_notes[:20]):  # Limitar a 20 notas más recientes
                date_str = format_date_chile(note.updated_at)
                content_parts.append(f"=== {note.title} ({date_str}) ===\n{note.content}\n")
            
            combined_content = "\n".join(content_parts)
            
            # Truncar si es muy largo (límite de tokens)
            if len(combined_content) > 15000:
                combined_content = combined_content[:15000] + "\n[...contenido truncado]"
            
            self._update_progress("Generando resumen con IA...")
            
            # Crear prompt para el resumen
            system_prompt = """Eres el Asistente de Claudio Montoya jefe del departamento de desarrollo de software, estas especializado en crear resúmenes ejecutivos claros y útiles. 
            Analiza las notas proporcionadas y crea un resumen estructurado que incluya:
            
            1. **Resumen Ejecutivo**: Los puntos más importantes en 2-3 oraciones enfocado a los proyectos, urgencia y hallazgos claves
            2. **Actividades Principales**: Las actividades y eventos más relevantes
            3. **Ideas y Decisiones Clave**: Ideas importantes, decisiones tomadas
            4. **Pendientes y Acciones**: Tareas pendientes o acciones identificadas
            5. **Temas Recurrentes**: Patrones o temas que aparecen múltiples veces
            6. **Descarta Notas Irrelevantes**: Omite notas que no estan en funcion de proyecto eventos problemas o funciones principales
            
            Mantén un tono profesional util para ul tts de macos nativo evita caracteres o simbolos que provoquen problemas de transcripcion. El resumen debe ser útil para revisar rápidamente los últimos días."""
            
            user_prompt = f"Analiza y resume las siguientes notas de los últimos 3 días:\n\n{combined_content}"
            
            # Generar resumen con OpenAI
            response = self.ai.client.chat.completions.create(
                model=self.ai.settings.chat_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,
                max_tokens=1500
            )
            
            summary = response.choices[0].message.content.strip()
            
            # Mostrar resumen
            self._hide_progress()
            self.summary_text.setPlainText(summary)
            self.btn_copy_summary.setEnabled(True)
            
            # Generar audio
            self._generate_audio(summary)
            
        except Exception as e:
            self._hide_progress()
            error_msg = f"Error generando resumen: {str(e)}"
            self.summary_text.setPlainText(error_msg)
            QMessageBox.critical(self, "Error", error_msg)
    
    def _generate_audio(self, text: str):
        """Genera audio del resumen usando OpenAI TTS"""
        try:
            self._update_progress("Generando audio...")
            self.progress_widget.show()
            
            # Usar QTimer para generar audio sin bloquear UI
            QTimer.singleShot(100, lambda: self._do_generate_audio(text))
            
        except Exception as e:
            self._hide_progress()
            print(f"Error preparando audio: {e}")
    
    def _do_generate_audio(self, text: str):
        """Genera audio usando TTS nativo de macOS con Francisca"""
        import subprocess
        import sys
        
        try:
            if not hasattr(self, '_audio_generation_active'):
                self._audio_generation_active = True

            if not self._audio_generation_active:
                return

            if sys.platform != "darwin":
                self._show_simple_error("TTS solo disponible en macOS")
                return

            # Limpiar archivo temporal anterior
            if hasattr(self, 'audio_file') and self.audio_file:
                try:
                    os.remove(self.audio_file)
                except:
                    pass

            # CAMBIO: Usar .wav en lugar de .aiff
            with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as temp_file:
                temp_path = temp_file.name

            # Limitar longitud del texto
            clean_text = text[:4000] + "..." if len(text) > 4000 else text

            # CAMBIO: Agregar --data-format para generar WAV compatible
            cmd = [
                "say",
                "-v", "Francisca",
                "-r", "160",
                "--data-format=LEI16@22050",  # WAV 16-bit a 22kHz
                "-o", temp_path,
                clean_text
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                if self._audio_generation_active:
                    self.audio_file = temp_path
                    self._hide_progress()
                    self.btn_play_audio.setEnabled(True)
                    self.btn_play_audio.setText("🔊 Reproducir Audio")
                    self._show_success_message()
                    QTimer.singleShot(500, self._auto_play_audio)
            else:
                raise Exception(f"Error en comando say: {result.stderr}")

        except subprocess.TimeoutExpired:
            self._show_simple_error("Timeout generando audio")
        except FileNotFoundError:
            self._show_simple_error("Comando 'say' no disponible")
        except Exception as e:
            self._show_simple_error(f"Error de TTS: {str(e)}")
            if 'temp_path' in locals():
                try:
                    os.remove(temp_path)
                except:
                    pass
    def _auto_play_audio(self):
        """Reproduce audio automáticamente después de generarlo"""
        if self.audio_file and os.path.exists(self.audio_file) and not self.audio_playing:
            self._play_audio()            
    def _show_simple_error(self, message: str):
        """Muestra error simple"""
        self._hide_progress()
        QMessageBox.warning(self, "Error de audio", message)
    def _get_chilean_voice_instructions(self) -> str:
        """Devuelve instrucciones específicas para tono chileno"""
        return (
            "Habla con un tono conversacional y cercano, característico del español chileno. "
            "Mantén una velocidad moderada, pronunciación clara y natural. "
            "Usa un registro informal pero profesional, como si fueras un asistente amigable. "
            "Haz pausas naturales entre oraciones y enfatiza suavemente los puntos importantes."
        )

    def _show_error_message(self, error: str):
        """Muestra mensaje de error específico y útil"""
        self._hide_progress()
        
        # Detectar tipos de error y dar mensajes claros
        error_str = str(error).lower()
        
        if "rate limit" in error_str or "429" in error_str:
            message = "Límite de API alcanzado. Espera unos minutos antes de intentar nuevamente."
        elif "api key" in error_str or "401" in error_str:
            message = "Problema con la clave de API. Verifica tu configuración en Ajustes."
        elif "quota" in error_str or "billing" in error_str:
            message = "Cuota de API agotada. Revisa tu plan de OpenAI."
        elif "network" in error_str or "connection" in error_str:
            message = "Error de conexión. Verifica tu conexión a internet."
        elif "model" in error_str:
            message = "Modelo no disponible. Puede que el servicio esté temporalmente inactivo."
        else:
            message = f"Error generando audio: {error}"
        
        # Mostrar mensaje al usuario
        QMessageBox.warning(self, "Error de síntesis de voz", message)
        
        # Log detallado para debugging
        print(f"Detalles del error de TTS: {error}")
    def _cancel_audio_generation(self):
        """Cancela la generación de audio en progreso"""
        self._audio_generation_active = False
        self._hide_progress()        
    def _toggle_audio(self):
        """Alterna reproducción de audio"""
        if not self.audio_file or not os.path.exists(self.audio_file):
            QMessageBox.warning(self, "Audio no disponible", 
                              "No hay audio generado para reproducir.")
            return
        
        if self.audio_playing:
            self._stop_audio()
        else:
            self._play_audio()
    
    def _play_audio(self):
        """Reproduce el audio"""
        try:
            pygame.mixer.music.load(self.audio_file)
            pygame.mixer.music.play()
            
            self.audio_playing = True
            self.btn_play_audio.setText("⏹️ Detener Audio")
            self.btn_play_audio.setStyleSheet(f"""
                QPushButton {{
                    background-color: {AppleColors.RED.name()};
                    color: white;
                    border: none;
                    border-radius: 8px;
                    padding: 10px 20px;
                    font-family: '.AppleSystemUIFont';
                    font-size: 14px;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    background-color: {AppleColors.RED.darker(110).name()};
                }}
            """)
            
            # Iniciar hilo para monitorear finalización
            self.audio_thread = threading.Thread(target=self._monitor_audio, daemon=True)
            self.audio_thread.start()
            
        except Exception as e:
            QMessageBox.warning(self, "Error de audio", f"No se pudo reproducir: {e}")
    
    def _stop_audio(self):
        """Detiene la reproducción"""
        try:
            pygame.mixer.music.stop()
            self._reset_audio_button()
        except Exception as e:
            print(f"Error deteniendo audio: {e}")
    
    def _monitor_audio(self):
        """Monitorea cuando termina el audio"""
        try:
            while pygame.mixer.music.get_busy() and self.audio_playing:
                threading.Event().wait(0.1)
            
            if self.audio_playing:  # Si terminó naturalmente
                QTimer.singleShot(0, self._reset_audio_button)
                
        except Exception as e:
            print(f"Error monitoreando audio: {e}")
    
    def _reset_audio_button(self):
        """Resetea el botón de audio"""
        self.audio_playing = False
        self.btn_play_audio.setText("🔊 Reproducir Audio")
        self.btn_play_audio.setStyleSheet(f"""
            QPushButton {{
                background-color: {AppleColors.CARD.name()};
                color: {AppleColors.PRIMARY.name()};
                border: 1px solid {AppleColors.SEPARATOR_LIGHT.name()};
                border-radius: 8px;
                padding: 10px 20px;
                font-family: '.AppleSystemUIFont';
                font-size: 14px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {AppleColors.ELEVATED.name()};
            }}
        """)
    
    def _copy_summary(self):
        """Copia el resumen al portapapeles"""
        text = self.summary_text.toPlainText().strip()
        if text:
            try:
                pyperclip.copy(text)
                QMessageBox.information(self, "Copiado", "Resumen copiado al portapapeles")
            except:
                clipboard = QApplication.clipboard()
                clipboard.setText(text)
                QMessageBox.information(self, "Copiado", "Resumen copiado al portapapeles")
    
    def _show_progress(self, message: str):
        """Muestra indicador de progreso"""
        self.btn_generate.setEnabled(False)
        self.progress_label.setText(message)
        self.progress_spinner.start()
        self.progress_widget.show()
    
    def _update_progress(self, message: str):
        """Actualiza mensaje de progreso"""
        self.progress_label.setText(message)
    
    def _hide_progress(self):
        """Oculta indicador de progreso"""
        self.progress_spinner.stop()
        self.progress_widget.hide()
        self.btn_generate.setEnabled(True)
    
    def _show_success_message(self):
        """Muestra mensaje de éxito temporal"""
        self.progress_label.setText("✅ Resumen y audio generados exitosamente")
        self.progress_widget.show()
        QTimer.singleShot(3000, lambda: self.progress_widget.hide())
    
    def closeEvent(self, event):
        """Limpia recursos al cerrar"""
        try:
            if hasattr(self, 'audio_playing') and self.audio_playing:
                self._stop_audio()
            
            if hasattr(self, 'audio_file') and self.audio_file and os.path.exists(self.audio_file):
                try:
                    os.remove(self.audio_file)
                except:
                    pass
        except Exception as e:
            print(f"Error limpiando SummaryTab: {e}")
        finally:
            event.accept()


class AdvancedSearchBar(QWidget):
    """Barra de búsqueda simple"""
    search_triggered = Signal(str)
    
    def __init__(self, db: NotesDB):
        super().__init__()
        self.db = db
        self._setup_ui()
        
    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 Buscar en notas...")
        self.search_edit.setStyleSheet(f"""
            QLineEdit {{
                background-color: {AppleColors.CARD.name()};
                border: 1px solid {AppleColors.SEPARATOR_LIGHT.name()};
                border-radius: 10px;
                padding: 12px 16px;
                color: {AppleColors.PRIMARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 14px;
                selection-background-color: {AppleColors.BLUE.name()};
            }}
            QLineEdit:focus {{
                border: 2px solid {AppleColors.BLUE.name()};
                padding: 11px 15px;
            }}
        """)
        
        self.clear_btn = QPushButton("✕")
        self.clear_btn.setFixedSize(30, 30)
        self.clear_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {AppleColors.CARD.name()};
                border: 1px solid {AppleColors.SEPARATOR_LIGHT.name()};
                border-radius: 15px;
                color: {AppleColors.SECONDARY.name()};
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {AppleColors.ELEVATED.name()};
            }}
        """)
        self.clear_btn.clicked.connect(self._clear_search)
        
        layout.addWidget(self.search_edit, 1)
        layout.addWidget(self.clear_btn)
        
        # Conectar eventos
        self.search_edit.textChanged.connect(self._emit_search)
        
        # Timer para búsqueda en tiempo real
        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self._do_search)
        
    def _emit_search(self):
        """Inicia búsqueda con delay"""
        self.search_timer.start(300)
    
    def _do_search(self):
        """Ejecuta la búsqueda"""
        query = self.search_edit.text().strip()
        self.search_triggered.emit(query)
    
    def _clear_search(self):
        """Limpia búsqueda"""
        self.search_edit.clear()
        self._emit_search()
class AudioQualityWidget(QWidget):
    """Widget para mostrar calidad de audio en tiempo real"""
    
    def __init__(self):
        super().__init__()
        self._setup_ui()
        
    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Indicador de volumen
        self.volume_bar = QProgressBar()
        self.volume_bar.setRange(0, 100)
        self.volume_bar.setFixedHeight(20)
        self.volume_bar.setStyleSheet(f"""
            QProgressBar {{
                border: 1px solid {AppleColors.SEPARATOR_LIGHT.name()};
                border-radius: 10px;
                background-color: {AppleColors.CARD.name()};
            }}
            QProgressBar::chunk {{
                background-color: {AppleColors.GREEN.name()};
                border-radius: 8px;
            }}
        """)
        
        # Etiqueta de calidad
        self.quality_label = QLabel("Sin audio")
        self.quality_label.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.SECONDARY.name()};
                font-size: 12px;
            }}
        """)
        
        layout.addWidget(QLabel("Volumen:"))
        layout.addWidget(self.volume_bar, 1)
        layout.addWidget(self.quality_label)
    
    def update_quality(self, volume: float, quality: str):
        """Actualiza indicadores de calidad"""
        self.volume_bar.setValue(int(volume * 100))
        
        color_map = {
            "good": AppleColors.GREEN.name(),
            "low": AppleColors.ORANGE.name(),
            "silent": AppleColors.TERTIARY.name(),
            "clipping": AppleColors.RED.name()
        }
        
        color = color_map.get(quality, AppleColors.SECONDARY.name())
        self.quality_label.setText(quality.capitalize())
        self.quality_label.setStyleSheet(f"QLabel {{ color: {color}; font-size: 12px; }}")

class NotesExportManager:
    """Maneja exportación de notas"""
    
    def __init__(self, db: NotesDB):
        self.db = db
    
    def export_note_markdown(self, note: Note) -> str:
        """Exporta nota a Markdown"""
        content = f"# {note.title}\n\n"
        content += f"**Categoría:** {note.category}\n"
        content += f"**Fecha:** {note.updated_at[:19]}\n"
        content += f"**Fuente:** {note.source}\n"
        
        if note.tags:
            content += f"**Tags:** {', '.join(note.tags)}\n"
        
        content += "\n---\n\n"
        content += note.content
        
        return content
    
    def export_notes_json(self, notes: List[Note]) -> str:
        """Exporta notas a JSON"""
        notes_data = []
        for note in notes:
            notes_data.append({
                "id": note.id,
                "title": note.title,
                "content": note.content,
                "category": note.category,
                "tags": note.tags,
                "source": note.source,
                "audio_path": note.audio_path,
                "created_at": note.created_at,
                "updated_at": note.updated_at
            })
        
        return json.dumps(notes_data, indent=2, ensure_ascii=False)

class AppleButton(QPushButton):
    """Botón estilo Apple"""
    def __init__(self, text: str, style_type: str = "primary"):
        super().__init__(text)
        self.style_type = style_type
        self._setup_style()
    
    def _setup_style(self):
        styles = {
            "primary": f"""
                QPushButton {{
                    background-color: {AppleColors.BLUE.name()};
                    color: white;
                    border: none;
                    border-radius: 8px;
                    padding: 10px 20px;
                    font-family: '.AppleSystemUIFont';
                    font-size: 14px;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    background-color: {AppleColors.BLUE.lighter(110).name()};
                }}
                QPushButton:disabled {{
                    background-color: {AppleColors.TERTIARY.name()};
                }}
            """,
            "secondary": f"""
                QPushButton {{
                    background-color: {AppleColors.CARD.name()};
                    color: {AppleColors.PRIMARY.name()};
                    border: 1px solid {AppleColors.SEPARATOR_LIGHT.name()};
                    border-radius: 8px;
                    padding: 10px 20px;
                    font-family: '.AppleSystemUIFont';
                    font-size: 14px;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    background-color: {AppleColors.ELEVATED.name()};
                }}
            """,
            "ghost": f"""
                QPushButton {{
                    background-color: transparent;
                    color: {AppleColors.BLUE.name()};
                    border: none;
                    padding: 8px 16px;
                    font-family: '.AppleSystemUIFont';
                    font-size: 14px;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    background-color: {AppleColors.CARD.name()};
                    border-radius: 6px;
                }}
            """,
            "success": f"""
                QPushButton {{
                    background-color: {AppleColors.GREEN.name()};
                    color: white;
                    border: none;
                    border-radius: 8px;
                    padding: 10px 20px;
                    font-family: '.AppleSystemUIFont';
                    font-size: 14px;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    background-color: {AppleColors.GREEN.darker(110).name()};
                }}
                QPushButton:disabled {{
                    background-color: {AppleColors.TERTIARY.name()};
                }}
            """,
            "danger": f"""
                QPushButton {{
                    background-color: transparent;
                    color: {AppleColors.RED.name()};
                    border: 1px solid {AppleColors.RED.name()};
                    border-radius: 8px;
                    padding: 10px 20px;
                    font-family: '.AppleSystemUIFont';
                    font-size: 14px;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    background-color: {AppleColors.RED.name()};
                    color: white;
                }}
            """
        }
        
        self.setStyleSheet(styles.get(self.style_type, styles["primary"]))

class AppleLineEdit(QLineEdit):
    """Campo de texto estilo Apple"""
    def __init__(self, placeholder: str = ""):
        super().__init__()
        self.setPlaceholderText(placeholder)
        self.setStyleSheet(f"""
            QLineEdit {{
                background-color: {AppleColors.CARD.name()};
                color: {AppleColors.PRIMARY.name()};
                border: 1px solid {AppleColors.SEPARATOR_LIGHT.name()};
                border-radius: 8px;
                padding: 12px 16px;
                font-family: '.AppleSystemUIFont';
                font-size: 14px;
                selection-background-color: {AppleColors.BLUE.name()};
            }}
            QLineEdit:focus {{
                border: 2px solid {AppleColors.BLUE.name()};
                padding: 11px 15px;
            }}
        """)

class AppleCard(QFrame):
    """Card estilo Apple"""
    def __init__(self, title: str = "", description: str = ""):
        super().__init__()
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {AppleColors.CARD.name()};
                border: 1px solid {AppleColors.SEPARATOR_LIGHT.name()};
                border-radius: 12px;
            }}
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)
        
        if title:
            title_label = QLabel(title)
            title_label.setStyleSheet(f"""
                QLabel {{
                    color: {AppleColors.PRIMARY.name()};
                    font-family: '.AppleSystemUIFont';
                    font-size: 16px;
                    font-weight: 600;
                    border: none;
                }}
            """)
            layout.addWidget(title_label)
            
        if description:
            desc_label = QLabel(description)
            desc_label.setStyleSheet(f"""
                QLabel {{
                    color: {AppleColors.SECONDARY.name()};
                    font-family: '.AppleSystemUIFont';
                    font-size: 13px;
                    border: none;
                }}
            """)
            desc_label.setWordWrap(True)
            layout.addWidget(desc_label)

# ... [Aquí irían las clases WelcomeScreen y SetupScreen - iguales que el original pero con caracteres corregidos]

class WelcomeScreen(QWidget):
    """Pantalla de bienvenida estilo Apple"""
    
    def __init__(self, on_continue):
        super().__init__()
        self.on_continue = on_continue
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(40)
        layout.setContentsMargins(80, 80, 80, 80)
        
        # Logo/Título
        title = QLabel("SecreIA")
        title.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.PRIMARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 48px;
                font-weight: 300;
                margin-bottom: 16px;
            }}
        """)
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        
        # Subtítulo
        subtitle = QLabel("Tu asistente inteligente para notas y transcripciones")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.SECONDARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 18px;
                margin-bottom: 32px;
            }}
        """)
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)
        
        # Características
        features_card = AppleCard()
        features_layout = features_card.layout()
        
        features = [
            ("📄", "Notas inteligentes", "Crea y organiza tus notas con clasificación automática"),
            ("🎤", "Transcripción automática", "Convierte audio a texto usando IA"),
            ("🔍", "Búsqueda semántica", "Encuentra información usando lenguaje natural"),
            ("🧠", "Análisis con IA", "Haz preguntas sobre tu contenido"),
        ]
        
        for icon, ft_title, desc in features:
            feature_widget = QWidget()
            feature_layout = QHBoxLayout(feature_widget)
            feature_layout.setContentsMargins(0, 12, 0, 12)
            feature_layout.setSpacing(16)
            
            icon_label = QLabel(icon)
            icon_label.setStyleSheet(f"""
                QLabel {{
                    color: {AppleColors.BLUE.name()};
                    font-size: 24px;
                }}
            """)
            icon_label.setFixedSize(40, 40)
            icon_label.setAlignment(Qt.AlignCenter)
            
            text_widget = QWidget()
            text_layout = QVBoxLayout(text_widget)
            text_layout.setContentsMargins(0, 0, 0, 0)
            text_layout.setSpacing(4)
            
            title_label = QLabel(ft_title)
            title_label.setStyleSheet(f"""
                QLabel {{
                    color: {AppleColors.PRIMARY.name()};
                    font-family: '.AppleSystemUIFont';
                    font-size: 15px;
                    font-weight: 600;
                }}
            """)
            
            desc_label = QLabel(desc)
            desc_label.setWordWrap(True)
            desc_label.setStyleSheet(f"""
                QLabel {{
                    color: {AppleColors.SECONDARY.name()};
                    font-family: '.AppleSystemUIFont';
                    font-size: 13px;
                }}
            """)
            
            text_layout.addWidget(title_label)
            text_layout.addWidget(desc_label)
            
            feature_layout.addWidget(icon_label)
            feature_layout.addWidget(text_widget, 1)
            
            features_layout.addWidget(feature_widget)
        
        layout.addWidget(features_card)
        
        # Botón continuar
        continue_btn = AppleButton("Comenzar configuración", "primary")
        continue_btn.clicked.connect(self.on_continue)
        continue_btn.setMinimumWidth(200)
        layout.addWidget(continue_btn, alignment=Qt.AlignHCenter)

class SetupScreen(QWidget):
    """Pantalla de configuración estilo Apple"""
    
    def __init__(self, settings: Settings, on_complete):
        super().__init__()
        self.settings = settings
        self.on_complete = on_complete
        self.test_timer = QTimer()
        self.test_timer.setSingleShot(True)
        self.test_timer.timeout.connect(self._test_connection)
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(32)
        layout.setContentsMargins(80, 60, 80, 60)
        
        # Título
        title = QLabel("Configuración inicial")
        title.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.PRIMARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 32px;
                font-weight: 300;
                margin-bottom: 8px;
            }}
        """)
        layout.addWidget(title)
        
        subtitle = QLabel("Configura tu clave de API de OpenAI para habilitar todas las funciones")
        subtitle.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.SECONDARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 16px;
                margin-bottom: 24px;
            }}
        """)
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        
        # Configuración API
        api_card = AppleCard("Clave API de OpenAI", "Necesaria para transcripción, clasificación y búsqueda semántica")
        api_layout = api_card.layout()
        
        # Campo API Key con indicador
        api_input_widget = QWidget()
        api_input_layout = QHBoxLayout(api_input_widget)
        api_input_layout.setContentsMargins(0, 16, 0, 0)
        api_input_layout.setSpacing(12)
        
        self.api_key_edit = AppleLineEdit("sk-...")
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.textChanged.connect(self._on_api_key_changed)
        
        self.status_badge = StatusBadge()
        self.status_label = QLabel("No configurada")
        self.status_label.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.SECONDARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 13px;
            }}
        """)
        
        api_input_layout.addWidget(self.api_key_edit, 1)
        api_input_layout.addWidget(self.status_badge)
        api_input_layout.addWidget(self.status_label)
        
        api_layout.addWidget(api_input_widget)
        
        # Botón test
        self.test_btn = AppleButton("Probar conexión", "ghost")
        self.test_btn.clicked.connect(self._start_test)
        self.test_btn.setEnabled(False)
        api_layout.addWidget(self.test_btn)
        
        layout.addWidget(api_card)
        
        # Configuraciones opcionales
        optional_card = AppleCard("Configuraciones opcionales", "Puedes modificar estos valores más tarde")
        optional_layout = optional_card.layout()
        
        form_layout = QFormLayout()
        form_layout.setSpacing(16)
        
        self.data_dir_edit = AppleLineEdit(self.settings.data_dir)
        self.chat_model_edit = AppleLineEdit(self.settings.chat_model)
        
        form_layout.addRow("Carpeta de datos:", self.data_dir_edit)
        form_layout.addRow("Modelo de chat:", self.chat_model_edit)
        
        optional_layout.addLayout(form_layout)
        layout.addWidget(optional_card)
        
        layout.addStretch()
        
        # Botones finales
        final_buttons = QWidget()
        final_layout = QHBoxLayout(final_buttons)
        final_layout.setSpacing(12)
        
        self.skip_btn = AppleButton("Omitir (funciones limitadas)", "secondary")
        self.skip_btn.clicked.connect(self._skip_setup)
        
        self.continue_btn = AppleButton("Completar configuración", "primary")
        self.continue_btn.clicked.connect(self._complete_setup)
        self.continue_btn.setEnabled(False)
        
        final_layout.addWidget(self.skip_btn)
        final_layout.addWidget(self.continue_btn)
        
        layout.addWidget(final_buttons)
        
        # Cargar valores existentes
        if self.settings.openai_api_key:
            self.api_key_edit.setText(self.settings.openai_api_key)
    
    def _on_api_key_changed(self):
        api_key = self.api_key_edit.text().strip()
        has_key = bool(api_key and api_key.startswith('sk-'))
        
        self.test_btn.setEnabled(has_key)
        self.continue_btn.setEnabled(has_key)
        
        if has_key:
            self.status_badge.set_status("unsaved")
            self.status_label.setText("No probada")
            self.test_timer.start(2000)
        else:
            self.status_badge.set_status("saved")
            self.status_label.setText("No configurada")
    
    def _start_test(self):
        self.test_btn.setText("Probando...")
        self.test_btn.setEnabled(False)
        self.status_badge.set_status("syncing")
        self.status_label.setText("Conectando...")
        self._test_connection()
    
    def _test_connection(self):
        api_key = self.api_key_edit.text().strip()
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            client.embeddings.create(input=["test"], model="text-embedding-3-small")
            
            self.status_badge.set_status("saved")
            self.status_label.setText("Conexión exitosa")
            self.test_btn.setText("✓ Conexión exitosa")
            
        except Exception as e:
            self.status_badge.set_status("unsaved")
            self.status_label.setText("Error de conexión")
            self.test_btn.setText("Probar conexión")
            self.test_btn.setEnabled(True)
            
            QMessageBox.warning(self, "Error de conexión", f"No se pudo conectar con OpenAI:\n{str(e)}")
    
    def _skip_setup(self):
        QMessageBox.information(self, "Configuración omitida", 
                               "Puedes configurar tu API key más tarde en Ajustes.\nAlgunas funciones estarán limitadas.")
        self.on_complete()
    
    def _complete_setup(self):
        self.settings.openai_api_key = self.api_key_edit.text().strip()
        self.settings.data_dir = self.data_dir_edit.text().strip()
        self.settings.chat_model = self.chat_model_edit.text().strip()
        
        QMessageBox.information(self, "Configuración completa", "¡Perfecto! Ya puedes empezar a usar SecreIA.")
        self.on_complete()

class NotesListDelegate(QStyledItemDelegate):
    """Delegate para la lista de notas estilo Apple Notes"""
    
    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        
        rect = option.rect
        is_selected = option.state & QStyle.State_Selected
        
        # Fondo
        if is_selected:
            painter.fillRect(rect, AppleColors.BLUE)
            text_color = QColor(255, 255, 255)
            meta_color = QColor(255, 255, 255, 180)
        else:
            text_color = AppleColors.PRIMARY
            meta_color = AppleColors.SECONDARY
            
        # Datos de la nota
        data = index.data(Qt.UserRole)
        if not data:
            painter.restore()
            return
            
        title = data.get('title', 'Sin título')
        preview = data.get('preview', '')
        date = data.get('date', '')
        has_audio = data.get('has_audio', False)
        is_transcript = data.get('is_transcript', False)
        
        # Márgenes
        margin = 16
        content_rect = rect.adjusted(margin, 8, -margin, -8)
        
        # Indicadores de estado
        if has_audio or is_transcript:
            icon_x = content_rect.x() + content_rect.width() - 30
            icon_y = content_rect.y() + 2
            
            if has_audio:
                painter.setPen(QPen(AppleColors.BLUE))
                painter.setFont(QFont(".AppleSystemUIFont", 12))
                painter.drawText(icon_x, icon_y + 15, "🎵")
                
            if is_transcript:
                painter.setPen(QPen(AppleColors.GREEN))
                painter.setFont(QFont(".AppleSystemUIFont", 12))
                painter.drawText(icon_x - 20, icon_y + 15, "🎤")
        
        # Título - CAMBIO AQUÍ: Mayor, negrita y mayúsculas
        title_font = QFont(".AppleSystemUIFont", 16, QFont.Bold)  # Aumentado de 15 a 16 y Bold
        painter.setFont(title_font)
        painter.setPen(text_color)
        
        # Reducir ancho del título si hay iconos
        title_width = content_rect.width()
        if has_audio or is_transcript:
            title_width -= 50
        
        title_rect = QRect(content_rect.x(), content_rect.y(), title_width, 22)  # Aumentado altura
        painter.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter, title.upper())  # .upper() para mayúsculas
        
        # Fecha
        date_font = QFont(".AppleSystemUIFont", 13)
        painter.setFont(date_font)
        painter.setPen(meta_color)
        
        date_rect = QRect(content_rect.x(), content_rect.y() + 26, content_rect.width(), 16)  # Ajustado posición
        painter.drawText(date_rect, Qt.AlignLeft | Qt.AlignVCenter, date)
        
        # Preview del contenido
        if preview:
            preview_font = QFont(".AppleSystemUIFont", 13)
            painter.setFont(preview_font)
            painter.setPen(meta_color)
            
            preview_rect = QRect(content_rect.x(), content_rect.y() + 46, 
                            content_rect.width(), content_rect.height() - 50)  # Ajustado posición
            painter.drawText(preview_rect, Qt.TextWordWrap, preview)
        
        painter.restore()
    def sizeHint(self, option, index):
        return QSize(320, 80)

class EnhancedNoteEditor(QWidget):
    """Editor de notas simplificado con nueva nota y loading"""
    
    note_saved = Signal()  # Señal para notificar cuando se guarda una nota
    
    def __init__(self, db: NotesDB, vector: Optional[VectorIndex], ai: AIService):
        super().__init__()
        self.db = db
        self.vector = vector
        self.ai = ai
        self.current_note_id = None
        self.is_dirty = False
        self.last_save_content = ""
        self._setup_ui()
        
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(20)
        
        # Toolbar con botones de acción
        toolbar_layout = QHBoxLayout()
        
        # Botón nueva nota
        self.btn_new = AppleButton("+ Nueva nota", "success")
        self.btn_new.clicked.connect(self._new_note)
        
        # Botón guardar con loading
        self.btn_save = AppleButton("💾 Guardar", "primary")
        self.btn_save.clicked.connect(self._save_note)
        self.btn_save.hide()  # Ocultar inicialmente
        
        # NUEVO: Botón eliminar
        self.btn_delete = AppleButton("🗑️ Eliminar", "danger")
        self.btn_delete.clicked.connect(self._delete_current_note)
        self.btn_delete.hide()  # Ocultar inicialmente
        
        # Spinner de loading (inicialmente oculto)
        self.loading_spinner = LoadingSpinner(20)
        self.loading_spinner.hide()
        
        # Status de guardado
        self.save_status = QLabel("")
        self.save_status.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.SECONDARY.name()};
                font-size: 12px;
                padding: 4px 8px;
            }}
        """)
        
        toolbar_layout.addWidget(self.btn_new)
        toolbar_layout.addWidget(self.btn_save)
        toolbar_layout.addWidget(self.btn_delete)  # AGREGAR AQUÍ
        toolbar_layout.addWidget(self.loading_spinner)
        toolbar_layout.addWidget(self.save_status)
        toolbar_layout.addStretch()
        layout.addLayout(toolbar_layout)

        
        # Título auto-generado con fondo diferente
        # Título editable con auto-generación inteligente
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Título de la nota...")
        self.title_edit.setStyleSheet(f"""
            QLineEdit {{
                background-color: {AppleColors.CARD.name()};
                border: 1px solid {AppleColors.SEPARATOR_LIGHT.name()};
                border-radius: 8px;
                color: {AppleColors.PRIMARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 18px;
                font-weight: 500;
                padding: 12px 16px;
                selection-background-color: {AppleColors.BLUE.name()};
            }}
            QLineEdit:focus {{
                border: 2px solid {AppleColors.BLUE.name()};
                padding: 11px 15px;
            }}
        """)

        # Conectar eventos para título
        self.title_edit.textChanged.connect(self._on_title_changed)
        self.title_edit.editingFinished.connect(self._on_title_finished)

        # Variable para controlar si el título fue editado manualmente
        self.title_manually_edited = False

        layout.addWidget(self.title_edit)
        
        # Solo categoría
        category_layout = QHBoxLayout()
        category_layout.addWidget(QLabel("Categoría:"))
        
        self.category_combo = QComboBox()
        self.category_combo.setStyleSheet(f"""
            QComboBox {{
                background: transparent;
                border: 1px solid {AppleColors.SEPARATOR_LIGHT.name()};
                border-radius: 6px;
                padding: 8px 12px;
                color: {AppleColors.PRIMARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 14px;
                min-width: 150px;
            }}
        """)
        self.category_combo.currentTextChanged.connect(self._mark_dirty)
        
        category_layout.addWidget(self.category_combo)
        category_layout.addStretch()
        layout.addLayout(category_layout)
        
        # Editor principal con fondo diferente
        self.content_edit = QTextEdit()
        self.content_edit.setPlaceholderText("Comenzar a escribir...")
        self.content_edit.setStyleSheet(f"""
            QTextEdit {{
                background-color: {AppleColors.NOTES_LIST.name()};
                border: 1px solid {AppleColors.SEPARATOR_LIGHT.name()};
                border-radius: 12px;
                color: {AppleColors.PRIMARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 16px;
                line-height: 1.6;
                padding: 20px;
                selection-background-color: {AppleColors.BLUE.name()};
            }}
        """)
        self.content_edit.textChanged.connect(self._on_content_changed)
        layout.addWidget(self.content_edit, 1)
        
        # Panel de estadísticas
        self.stats_label = QLabel("Palabras: 0 | Caracteres: 0")
        self.stats_label.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.SECONDARY.name()};
                font-size: 12px;
                padding: 8px 0px;
            }}
        """)
        layout.addWidget(self.stats_label)
        
        self.refresh_categories()
    def _delete_current_note(self):
        """Elimina la nota actualmente cargada"""
        if not self.current_note_id:
            return
        
        try:
            note = self.db.get_note(self.current_note_id)
            if not note:
                return
            
            reply = QMessageBox.question(
                self, "Confirmar eliminación",
                f"¿Eliminar la nota '{note.title}'?\n\nEsta acción no se puede deshacer.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                # Eliminar de base de datos
                self.db.delete_note(self.current_note_id)
                
                # Eliminar del vector store si existe
                if self.vector:
                    try:
                        self.vector.delete_note_chunks(self.current_note_id)
                    except Exception:
                        pass
                
                # Eliminar archivo de audio si existe
                if note.audio_path and os.path.exists(note.audio_path):
                    try:
                        os.remove(note.audio_path)
                    except Exception:
                        pass
                
                # Limpiar editor
                self.clear_editor()
                
                # Emitir señal para actualizar la lista
                self.note_saved.emit()
                
                QMessageBox.information(self, "Eliminado", "Nota eliminada correctamente")
                
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error al eliminar: {e}")

    def _update_delete_button_visibility(self):
        """Actualiza visibilidad del botón eliminar según el estado"""
        # Mostrar botón eliminar solo si hay una nota cargada (no nueva)
        has_loaded_note = self.current_note_id is not None
        self.btn_delete.setVisible(has_loaded_note)    
    def _new_note(self):
        """Prepara una nueva nota"""
        # Verificar si hay cambios sin guardar
        if self.is_dirty:
            reply = QMessageBox.question(
                self, "Cambios sin guardar",
                "Hay cambios sin guardar. ¿Quieres guardar antes de crear una nueva nota?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save
            )
            
            if reply == QMessageBox.Save:
                if not self._save_note():
                    return  # No continuar si falla el guardado
            elif reply == QMessageBox.Cancel:
                return  # Cancelar la operación
        
        # Limpiar editor para nueva nota (sin guardar automáticamente)
        self.clear_editor()
        self.content_edit.setFocus()
        
        # Mostrar status
        self.save_status.setText("Nueva nota - Escribe contenido para guardar")
        QTimer.singleShot(4000, lambda: self.save_status.clear())
        
    def _on_content_changed(self):
        """Maneja cambios en el contenido"""
        self._mark_dirty()
        self._update_stats()
        self._auto_generate_title()
        
        # Mostrar/ocultar botón guardar según contenido
        has_content = bool(self.content_edit.toPlainText().strip())
        if has_content:
            self.btn_save.show()
        else:
            self.btn_save.hide()
        
    def _auto_generate_title(self):
        """Auto-genera título basado en el contenido"""
        content = self.content_edit.toPlainText().strip()
        if content:
            # Tomar primeras palabras como título
            words = content.split()[:6]  # Primeras 6 palabras
            title = " ".join(words)
            if len(content.split()) > 6:
                title += "..."
            self.title_edit.setText(title)
        else:
            self.title_edit.setText("")
    
    def _update_stats(self):
        """Actualiza estadísticas del texto"""
        text = self.content_edit.toPlainText()
        words = len(text.split()) if text.strip() else 0
        chars = len(text)
        self.stats_label.setText(f"Palabras: {words} | Caracteres: {chars}")
    
    def _mark_dirty(self):
        """Marca como modificado"""
        self.is_dirty = True
        # Cambiar texto del botón para indicar cambios
        if not self.btn_save.text().endswith("*"):
            self.btn_save.setText(self.btn_save.text() + "*")
        
    def refresh_categories(self):
        """Actualiza categorías disponibles"""
        current = self.category_combo.currentText()
        self.category_combo.clear()
        cats = self.db.list_categories()
        if "General" not in cats:
            cats = ["General"] + cats
        for c in cats:
            self.category_combo.addItem(c)
        
        # Restaurar selección
        if current:
            index = self.category_combo.findText(current)
            if index >= 0:
                self.category_combo.setCurrentIndex(index)
                
    def _save_note(self):
        """Guarda la nota con loading"""
        title = self.title_edit.text().strip() or "Sin título"
        content = self.content_edit.toPlainText().strip()
        category = self.category_combo.currentText().strip() or "General"
        
        # Validación estricta: debe haber contenido real
        if not content or len(content) < 3:
            QMessageBox.information(self, "Información", "Escribe contenido antes de guardar (mínimo 3 caracteres).")
            self.content_edit.setFocus()
            return False

        # Mostrar loading
        self._show_saving_state()
        
        # Usar QTimer para no bloquear la UI
        QTimer.singleShot(100, lambda: self._do_save(title, content, category))
        return True
    
    def _show_saving_state(self):
        """Muestra estado de guardado"""
        self.btn_save.setEnabled(False)
        self.btn_save.setText("Guardando...")
        self.loading_spinner.show()
        self.loading_spinner.start()
        self.save_status.setText("Guardando...")
    
    def _do_save(self, title: str, content: str, category: str):
        """Ejecuta el guardado real"""
        try:
            
            chile_tz = pytz.timezone('America/Santiago')
            
            # Usar el método para obtener título final
            final_title = self._get_final_title()
            
            self.db.add_category(category)
            
            note = Note(
                id=self.current_note_id,
                title=final_title,
                content=content,
                category=category,
                tags=[],
                source="manual",
                audio_path=None,
                created_at=datetime.now(chile_tz).isoformat() if not self.current_note_id else None,
                updated_at=datetime.now(chile_tz).isoformat(),
            )
            
            note_id = self.db.upsert_note(note)
            self.current_note_id = note_id

            # Indexar si hay vector store
            if self.vector:
                try:
                    self.vector.index_note(note_id, final_title, content, category, [], "manual")  # CAMBIO AQUÍ
                except Exception as e:
                    print(f"Error indexando: {e}")

            self.refresh_categories()
            self.is_dirty = False
            
            # Actualizar título en UI con el final
            self.title_edit.setText(final_title)
            
            # Mostrar éxito
            self._show_success_state()
            
            # Emitir señal para actualizar la lista
            self.note_saved.emit()
            
        except Exception as e:
            self._show_error_state(str(e))
    def _show_success_state(self):
            """Muestra estado de éxito"""
            self.loading_spinner.stop()
            self.loading_spinner.hide()
            self.btn_save.setEnabled(True)
            self.btn_save.setText("💾 Guardar")
            self.save_status.setText("✅ Guardado")
            self.save_status.setStyleSheet(f"""
                QLabel {{
                    color: {AppleColors.GREEN.name()};
                    font-size: 12px;
                    padding: 4px 8px;
                }}
            """)
            
            # Limpiar status después de 3 segundos
            QTimer.singleShot(3000, self._clear_status)
    
    def _show_error_state(self, error: str):
        """Muestra estado de error"""
        self.loading_spinner.stop()
        self.loading_spinner.hide()
        self.btn_save.setEnabled(True)
        self.btn_save.setText("💾 Guardar")
        self.save_status.setText("❌ Error")
        self.save_status.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.RED.name()};
                font-size: 12px;
                padding: 4px 8px;
            }}
        """)
        
        QMessageBox.critical(self, "Error", f"Error al guardar: {error}")
        QTimer.singleShot(3000, self._clear_status)
    
    def _clear_status(self):
        """Limpia el status"""
        self.save_status.clear()
        self.save_status.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.SECONDARY.name()};
                font-size: 12px;
                padding: 4px 8px;
            }}
        """)
    
    def clear_editor(self):
        """Limpia el editor"""
        self.current_note_id = None
        self.title_edit.clear()
        self.content_edit.clear()
        self.category_combo.setCurrentText("General")
        self.is_dirty = False
        self.title_manually_edited = False
        self.btn_save.setText("💾 Guardar")
        self.btn_save.hide()  # Ocultar botón al limpiar
        self._update_stats()
        self._clear_status()
        
        # NUEVO: Ocultar botón eliminar al limpiar
        self._update_delete_button_visibility()
        
    def load_note(self, note_id):
        """Carga una nota"""
        try:
            note = self.db.get_note(note_id)
            if not note:
                return False
                
            self.current_note_id = note_id
            self.title_edit.setText(note.title)
            self.title_manually_edited = True
            self.content_edit.setPlainText(note.content)
            
            # Actualizar categoría
            if self.category_combo.findText(note.category) == -1:
                self.category_combo.addItem(note.category)
            self.category_combo.setCurrentText(note.category)
            
            self.is_dirty = False
            self.btn_save.setText("💾 Guardar")
            self._update_stats()
            self._clear_status()
            
            # NUEVO: Actualizar visibilidad del botón eliminar
            self._update_delete_button_visibility()
            
            return True
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error cargando nota: {e}")
            return False
    
    def _on_title_changed(self):
        """Maneja cambios en el título"""
        # Si el usuario está escribiendo, marcar como editado manualmente
        if self.title_edit.hasFocus():
            self.title_manually_edited = True
        self._mark_dirty()

    def _on_title_finished(self):
        """Cuando el usuario termina de editar el título"""
        title = self.title_edit.text().strip()
        if not title:
            # Si está vacío, generar automáticamente
            self.title_manually_edited = False
            self._auto_generate_title()
        else:
            # Marcar como editado manualmente
            self.title_manually_edited = True

    def _auto_generate_title(self):
        """Auto-genera título basado en el contenido solo si no fue editado manualmente"""
        if self.title_manually_edited:
            return  # No sobrescribir si fue editado manualmente
            
        content = self.content_edit.toPlainText().strip()
        if content:
            # Tomar primeras palabras como título
            words = content.split()[:6]  # Primeras 6 palabras
            title = " ".join(words)
            if len(content.split()) > 6:
                title += "..."
            
            # Solo actualizar si no está enfocado (evitar interferir mientras escribe)
            if not self.title_edit.hasFocus():
                self.title_edit.setText(title)
        else:
            # Limpiar título si no hay contenido y no fue editado manualmente
            if not self.title_edit.hasFocus():
                self.title_edit.setText("")

    def _get_final_title(self):
        """Obtiene el título final para guardar"""
        title = self.title_edit.text().strip()
        
        if not title:
            # Si no hay título, generar uno automáticamente
            content = self.content_edit.toPlainText().strip()
            if content:
                words = content.split()[:6]
                title = " ".join(words)
                if len(content.split()) > 6:
                    title += "..."
            else:
                title = "Sin título"
        
        return title
    def new_note(self):
        """Método público para crear nueva nota"""
        self._new_note()

class EnhancedNotesView(QWidget):
    """Vista principal de notas mejorada"""
    
    def __init__(self, settings: Settings, db: NotesDB, vector: Optional[VectorIndex], ai: AIService):
        super().__init__()
        self.settings = settings
        self.db = db
        self.vector = vector
        self.ai = ai
        self.current_filters = {}
        self._setup_ui()
        self._load_data()
        
    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Splitter principal
        splitter = QSplitter(Qt.Horizontal)
        
        # Panel izquierdo: búsqueda simple y lista
        left_panel = QWidget()
        left_panel.setFixedWidth(400)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(16, 16, 8, 16)
        left_layout.setSpacing(16)
        
        # Búsqueda simple
        self.search_widget = AdvancedSearchBar(self.db)
        self.search_widget.search_triggered.connect(self._apply_simple_search)
        left_layout.addWidget(self.search_widget)
        
        # Lista de notas
        self.notes_list = QListWidget()
        self.notes_list.setItemDelegate(NotesListDelegate())
        self.notes_list.itemClicked.connect(self._on_note_selected)
        self.notes_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.notes_list.customContextMenuRequested.connect(self._show_context_menu)
        self.notes_list.setStyleSheet(f"""
            QListWidget {{
                background-color: {AppleColors.NOTES_LIST.name()};
                border: none;
                outline: none;
            }}
        """)
        left_layout.addWidget(self.notes_list, 1)
        
        # Status bar
        self.status_label = QLabel("0 notas")
        self.status_label.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.SECONDARY.name()};
                font-size: 12px;
                padding: 8px;
            }}
        """)
        left_layout.addWidget(self.status_label)
        
        # Editor simplificado
        self.note_editor = EnhancedNoteEditor(self.db, self.vector, self.ai)
        self.note_editor.note_saved.connect(self._load_data)
        splitter.addWidget(left_panel)
        splitter.addWidget(self.note_editor)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        
        layout.addWidget(splitter)

    def _apply_simple_search(self, query: str):
        """Aplica búsqueda simple"""
        self.notes_list.clear()
        
        if query:
            notes = self.db.search_notes(query)
        else:
            notes = self.db.list_notes(limit=200)
        
        for note in notes:
            self._add_note_to_list(note)
        
        self.status_label.setText(f"{len(notes)} notas")
    def _apply_search(self, query: str, filters: Dict[str, str]):
        """Aplica búsqueda con filtros"""
        self.current_filters = filters
        self._filter_notes(query, filters)
    
    def _filter_notes(self, query: str, filters: Dict[str, str]):
        """Filtra notas según criterios"""
        self.notes_list.clear()
        
        # Obtener notas base
        if filters.get('category'):
            notes = self.db.search_notes("", category=filters['category'])
        else:
            notes = self.db.list_notes(limit=1000)
        
        # Aplicar filtro de texto
        if query:
            notes = [n for n in notes if query.lower() in n.title.lower() or query.lower() in n.content.lower()]
        
        # Aplicar filtro de fecha
        date_filter = filters.get('date_filter', 'Todas')
        if date_filter != 'Todas':
            now = datetime.utcnow()
            if date_filter == 'Hoy':
                cutoff = now - timedelta(days=1)
            elif date_filter == 'Esta semana':
                cutoff = now - timedelta(days=7)
            elif date_filter == 'Este mes':
                cutoff = now - timedelta(days=30)
            else:
                cutoff = None
            
            if cutoff:
                notes = [n for n in notes if datetime.fromisoformat(n.updated_at.replace('Z', '+00:00')) > cutoff]
        
        # Aplicar filtro de tipo
        type_filter = filters.get('type_filter', 'Todos')
        if type_filter == 'Manual':
            notes = [n for n in notes if n.source == 'manual']
        elif type_filter == 'Transcripciones':
            notes = [n for n in notes if n.source == 'transcript']
        
        # Mostrar resultados
        for note in notes:
            self._add_note_to_list(note)
        
        self.status_label.setText(f"{len(notes)} notas")
    
    def _add_note_to_list(self, note: Note):
        """Agrega una nota a la lista"""
        preview = note.content[:100] + "..." if len(note.content) > 100 else note.content
        date_str = self._format_date(note.updated_at)
        
        # Detectar características especiales
        has_audio = bool(note.audio_path and os.path.exists(note.audio_path))
        is_transcript = note.source == "transcript"
        
        item = QListWidgetItem()
        item.setData(Qt.UserRole, {
            'id': note.id,
            'title': note.title or "Sin título",
            'preview': preview,
            'date': date_str,
            'has_audio': has_audio,
            'is_transcript': is_transcript
        })
        self.notes_list.addItem(item)
    
    def _show_context_menu(self, pos: QPoint):
        """Muestra menú contextual"""
        item = self.notes_list.itemAt(pos)
        if not item:
            return
            
        data = item.data(Qt.UserRole)
        if not data:
            return
            
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {AppleColors.ELEVATED.name()};
                border: 1px solid {AppleColors.SEPARATOR.name()};
                border-radius: 8px;
                padding: 4px 0;
                font-size: 14px;
                color: {AppleColors.PRIMARY.name()};
            }}
            QMenu::item {{
                padding: 8px 16px;
            }}
            QMenu::item:selected {{
                background-color: {AppleColors.BLUE.name()};
                color: white;
            }}
        """)
        
        edit_action = menu.addAction("✏️ Editar")
        duplicate_action = menu.addAction("📋 Duplicar")
        export_action = menu.addAction("📤 Exportar")
        menu.addSeparator()
        delete_action = menu.addAction("🗑️ Eliminar")
        
        action = menu.exec(self.notes_list.mapToGlobal(pos))
        
        if action == duplicate_action:
            self._duplicate_note(data['id'])
        elif action == export_action:
            self._export_note(data['id'])
        elif action == delete_action:
            self._delete_note_from_menu(data['id'], item)
    
    def _duplicate_note(self, note_id: int):
        """Duplica una nota"""
        try:
            original = self.db.get_note(note_id)
            if not original:
                return
                
            duplicate = Note(
                id=None,
                title=f"{original.title} (Copia)",
                content=original.content,
                category=original.category,
                tags=original.tags.copy(),
                source="manual",
                audio_path=None,
                created_at=datetime.utcnow().isoformat(),
                updated_at=datetime.utcnow().isoformat()
            )
            
            new_id = self.db.upsert_note(duplicate)
            
            if self.vector:
                try:
                    self.vector.index_note(new_id, duplicate.title, duplicate.content)
                except Exception as e:
                    print(f"Error indexando: {e}")
            
            self._load_data()
            QMessageBox.information(self, "Duplicado", f"Nota '{original.title}' duplicada")
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error al duplicar: {e}")
    
    def _export_note(self, note_id: int):
        """Exporta una nota específica"""
        note = self.db.get_note(note_id)
        if not note:
            return
        
        # Usar el mismo sistema de exportación del editor
        export_manager = NotesExportManager(self.db)
        
        formats = {
            "Markdown (*.md)": "md",
            "Texto plano (*.txt)": "txt",
            "JSON (*.json)": "json"
        }
        
        format_str, ok = QInputDialog.getItem(
            self, "Formato de exportación",
            "Selecciona formato:", list(formats.keys()), 0, False
        )
        
        if not ok:
            return
        
        format_ext = formats[format_str]
        suggested_name = f"{note.title}.{format_ext}"
        
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Exportar nota", suggested_name, format_str
        )
        
        if file_path:
            try:
                if format_ext == "md":
                    content = export_manager.export_note_markdown(note)
                elif format_ext == "json":
                    content = export_manager.export_notes_json([note])
                else:
                    content = f"# {note.title}\n\n{note.content}"
                
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                
                QMessageBox.information(self, "Exportación", f"Nota exportada a {file_path}")
                
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Error al exportar: {e}")
    
    def _delete_note_from_menu(self, note_id: int, item: QListWidgetItem):
        """Elimina nota desde menú contextual"""
        note = self.db.get_note(note_id)
        if not note:
            return
        
        reply = QMessageBox.question(
            self, "Confirmar eliminación",
            f"¿Eliminar la nota '{note.title}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            try:
                self.db.delete_note(note_id)
                
                if self.vector:
                    try:
                        self.vector.delete_note_chunks(note_id)
                    except Exception:
                        pass
                
                # Eliminar archivo de audio si existe
                if note.audio_path and os.path.exists(note.audio_path):
                    try:
                        os.remove(note.audio_path)
                    except Exception:
                        pass
                
                # Remover de la lista
                self.notes_list.takeItem(self.notes_list.row(item))
                
                # Limpiar editor si era la nota activa
                if self.note_editor.current_note_id == note_id:
                    self.note_editor.clear_editor()
                
                self.status_label.setText(f"{self.notes_list.count()} notas")
                QMessageBox.information(self, "Eliminado", "Nota eliminada correctamente")
                
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Error al eliminar: {e}")
    
    def _on_note_selected(self, item):
        """Maneja selección de nota"""
        data = item.data(Qt.UserRole)
        if data:
            self.note_editor.load_note(data['id'])
    
    def _load_data(self):
        """Carga datos iniciales"""
        # Aplicar filtros actuales o mostrar todas
        if hasattr(self, 'search_widget'):
            self._filter_notes("", self.current_filters)
        else:
            # Carga inicial
            notes = self.db.list_notes(limit=200)
            for note in notes:
                self._add_note_to_list(note)
            self.status_label.setText(f"{len(notes)} notas")
    
    def _format_date(self, date_str):
        """Formatea fecha para mostrar - usando formato chileno"""
        return format_date_chile(date_str)
    
    def new_note(self):
        """Prepara nueva nota sin guardar automáticamente"""
        # Solo limpiar el editor, no crear nota en BD aún
        self.note_editor.clear_editor()
        self.note_editor.content_edit.setFocus()
        
    def refresh_all(self):
        """Refresca toda la vista"""
        self._load_data()
        if hasattr(self, 'note_editor'):
            self.note_editor.refresh_categories()

# Clases TranscriptionWorker y TranscribeTab mejoradas
class TranscriptionWorker(QThread):
    """Worker thread para transcripción sin bloquear UI"""
    finished = Signal(str)
    error = Signal(str)
    progress = Signal(str)  # Nuevo: para actualizaciones de progreso
    
    def __init__(self, ai_service, wav_path):
        super().__init__()
        self.ai = ai_service
        self.wav_path = wav_path
        
    def run(self):
        try:
            self.progress.emit("Conectando con Whisper...")
            text = self.ai.transcribe(self.wav_path)
            self.finished.emit(text)
        except Exception as e:
            self.error.emit(str(e))

class EnhancedTranscribeTab(QWidget):
    """Tab de transcripción en tiempo real con configuración profesional y manejo robusto de errores"""
    
    # Señales thread-safe para comunicación entre hilos
    text_received = Signal(str)
    status_changed = Signal(str)
    error_occurred = Signal(str)
    
    def __init__(self, settings: 'Settings', db: 'NotesDB', vector: Optional['VectorIndex'], ai: 'AIService'):
        super().__init__()
        self.settings = settings
        self.db = db
        self.vector = vector
        self.ai = ai
        
        # Reconocimiento
        self.speech_recognizer = sr.Recognizer()
        self.microphone = None
        self.recognition_thread = None
        self.realtime_text = []
        self.recognition_active = False
        self.recognition_working = False
        self.is_transcribing = False
        self.start_time = None
        
        # Buffer para texto en tiempo real
        self.text_buffer = []
        self.last_update_time = 0
        self.update_interval = 0.12  # UI ~120ms
        
        # --- Pipeline de audio y reconocimiento ---
        self.audio_queue: "queue.Queue[sr.AudioData]" = queue.Queue(maxsize=12)
        self.executor = ThreadPoolExecutor(max_workers=3)  # 2-3 workers es buen balance
        self.capture_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self._overlap_buffer = deque(maxlen=1)  # reservado para futuro streaming
        self.recent_texts = []  # para deduplicación rápida
        
        self._setup_ui()
        self._connect_signals()
        self._test_speech_recognition()
    
    # ------------------------------------------------------------------
    # Señales
    # ------------------------------------------------------------------
    def _connect_signals(self):
        """Conecta señales thread-safe"""
        self.text_received.connect(self._handle_text_update)
        self.status_changed.connect(self._handle_status_update)
        self.error_occurred.connect(self._handle_error_update)
        
    # ------------------------------------------------------------------
    # Inicialización y test de SR
    # ------------------------------------------------------------------
    def _test_speech_recognition(self):
        """Configuración balanceada y estable"""
        if not self._check_and_request_microphone_access():
            self.recognition_working = False
            return
            
        try:
            self.microphone = sr.Microphone()
            with self.microphone as source:
                # Calibración más larga para mejor estabilidad
                self.speech_recognizer.adjust_for_ambient_noise(source, duration=0.3)
            
            # CONFIGURACIÓN BALANCEADA (baja latencia + estabilidad)
            self.speech_recognizer.energy_threshold = max(2500, int(self.speech_recognizer.energy_threshold))
            self.speech_recognizer.pause_threshold = 0.5
            self.speech_recognizer.phrase_threshold = 0.25
            self.speech_recognizer.dynamic_energy_threshold = True
            self.speech_recognizer.dynamic_energy_adjustment_damping = 0.12
            self.speech_recognizer.dynamic_energy_ratio = 1.4
            self.speech_recognizer.operation_timeout = None
            self.speech_recognizer.non_speaking_duration = 0.3
            
            self.recognition_working = True
            print("Reconocimiento configurado de forma balanceada y estable")

            # Actualizar UI si ya existe
            if hasattr(self, 'status_info'):
                self.status_info.setText("✅ listo para transcripción")
                self.status_info.setStyleSheet("color: green; font-weight: 500; border: none;")
            if hasattr(self, 'btn_start'):
                self.btn_start.setEnabled(True)
            if hasattr(self, 'status_label'):
                self.status_label.setText("Listo - Configuración estable activa")
                
        except Exception as e:
            print(f"Error configurando reconocimiento: {e}")
            self.microphone = None
            self.recognition_working = False
            QTimer.singleShot(500, self._show_permission_guide)

    # ------------------------------------------------------------------
    # Pipeline captura + reconocimiento
    # ------------------------------------------------------------------
    def _start_realtime_recognition(self):
        """Inicia pipeline de captura + reconocimiento concurrente."""
        if not self.microphone:
            try:
                self.microphone = sr.Microphone()
            except Exception as e:
                self.error_occurred.emit(f"No hay micrófono: {e}")
                return

        # Reset de estado
        with self.audio_queue.mutex:
            self.audio_queue.queue.clear()
        self.stop_event.clear()

        # Hilo de captura: rápido y continuo
        def _capture_loop():
            try:
                with self.microphone as source:
                    # Captura sensible y con baja latencia
                    self.speech_recognizer.pause_threshold = 0.5
                    self.speech_recognizer.phrase_threshold = 0.25
                    self.speech_recognizer.non_speaking_duration = 0.3
                    phrase_time_limit = 3.0  # chunks cortos

                    while self.recognition_active and not self.stop_event.is_set():
                        try:
                            audio = self.speech_recognizer.listen(
                                source,
                                timeout=1.0,
                                phrase_time_limit=phrase_time_limit
                            )
                            # Encolar sin bloquear; si lleno, descartamos el más viejo
                            try:
                                self.audio_queue.put_nowait(audio)
                            except queue.Full:
                                try:
                                    _ = self.audio_queue.get_nowait()
                                except queue.Empty:
                                    pass
                                try:
                                    self.audio_queue.put_nowait(audio)
                                except queue.Full:
                                    pass
                        except Exception:
                            # WaitTimeoutError u otros: continuar
                            continue
            except Exception as e:
                self.error_occurred.emit(f"Error de captura: {e}")

        # Consumidor: programa reconocimiento en pool
        def _consume_loop():
            while self.recognition_active and not self.stop_event.is_set():
                try:
                    audio = self.audio_queue.get(timeout=0.3)
                except queue.Empty:
                    continue

                def _work(audio_chunk: sr.AudioData):
                    # Reconoce con es-CL y fallback multi-idioma + deduplicación
                    try:
                        text = self.speech_recognizer.recognize_google(audio_chunk, language='es-CL')
                        if not text or not text.strip():
                            return ""
                        # deduplicación rápida
                        text_dedup = self._recognize_with_deduplication(audio_chunk)
                        return text_dedup or text
                    except Exception:
                        try:
                            return self._recognize_with_multiple_languages(audio_chunk)
                        except Exception:
                            return ""

                future: Future = self.executor.submit(_work, audio)
                def _on_done(fut: Future):
                    try:
                        text = fut.result()
                        if text and isinstance(text, str) and text.strip():
                            self.realtime_text.append(text.strip())
                            self.text_received.emit(text.strip())
                    except Exception as e:
                        self.error_occurred.emit(f"Error worker: {e}")

                future.add_done_callback(_on_done)

        # Lanzar hilos
        self.capture_thread = threading.Thread(target=_capture_loop, daemon=True)
        self.recognition_thread = threading.Thread(target=_consume_loop, daemon=True)
        self.capture_thread.start()
        self.recognition_thread.start()

    # ------------------------------------------------------------------
    # Control de transcripción
    # ------------------------------------------------------------------
    def start_transcription(self):
        """Inicia transcripción con configuración profesional"""
        if not self.recognition_working:
            QMessageBox.warning(self, APP_NAME, "El reconocimiento de voz no está disponible.\nVerifica tu micrófono y permisos del sistema.")
            return
        
        try:
            # Configuración adicional profesional
            self._configure_microphone_for_fast_speech()
            
            # Configurar estado
            self.is_transcribing = True
            self.recognition_active = True
            self.start_time = time.time()
            self.realtime_text = []
            self.text_buffer = []
            self.recent_texts = []  # Para deduplicación
            
            # Actualizar UI
            self.btn_start.hide()
            self.btn_stop.show()
            self.btn_stop.setEnabled(True)
            
            self.status_indicator.setText("Transcripción")
            self.status_indicator.setStyleSheet(f"""
                QLabel {{
                    color: white;
                    font-size: 12px;
                    border: none;
                    padding: 4px 8px;
                    background-color: {AppleColors.GREEN.name()};
                    border-radius: 4px;
                }}
            """)
            
            # Limpiar vista previa
            self.transcript_preview.clear()
            self.transcript_preview.append("🎤 Transcripción activa...\n")
            
            # Iniciar pipeline
            self._start_realtime_recognition()
            
            # Timers optimizados
            self.duration_timer.start(1000)
            self.ui_update_timer.setInterval(int(self.update_interval * 1000))
            self.ui_update_timer.start()
            
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, f"No se pudo iniciar la transcripción: {e}")
            self._reset_state()

    def stop_transcription(self):
        """Detiene la transcripción sin perder texto pendiente."""
        if not self.is_transcribing:
            return

        # Señal de stop para todos
        self.recognition_active = False
        self.stop_event.set()

        # Esperar hilos
        if self.capture_thread and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=1.5)
        if self.recognition_thread and self.recognition_thread.is_alive():
            self.recognition_thread.join(timeout=2.0)

        # No drenamos la cola para cierre rápido; el buffer de texto ya se flushea en UI
        self.is_transcribing = False

        # Detener timers
        if hasattr(self, 'ui_update_timer'):
            self.ui_update_timer.stop()

        # Último flush del buffer a la UI
        self._flush_text_buffer()

        self._reset_state()

        total_words = len(" ".join(self.realtime_text).split())
        self.status_label.setText(f"Transcripción completada - {total_words} palabras")

    # ------------------------------------------------------------------
    # Utilidades de reconocimiento
    # ------------------------------------------------------------------
    def _is_duplicate_text(self, new_text: str) -> bool:
        """Verificación simple de duplicados contra el último fragmento."""
        if not self.realtime_text:
            return False
        
        last_text = self.realtime_text[-1] if self.realtime_text else ""
        if len(new_text) > 0 and len(last_text) > 0:
            s1 = set(new_text.lower().split())
            s2 = set(last_text.lower().split())
            if not s1 or not s2:
                return False
            similarity = len(s1 & s2) / max(len(s1), len(s2))
            return similarity > 0.8
        return False

    def _recognize_with_multiple_languages(self, audio):
        """Reconocimiento con múltiples idiomas para mayor precisión (fallback)."""
        language_configs = [
            ('es-CL', False),
            ('es-419', False),
            ('es-AR', False),
            ('es-MX', False),
            ('es-ES', False),
        ]
        
        for language, use_show_all in language_configs:
            try:
                if use_show_all:
                    result = self.speech_recognizer.recognize_google(audio, language=language, show_all=True)
                    text = self._extract_best_result(result)
                else:
                    text = self.speech_recognizer.recognize_google(audio, language=language)
                
                if text and isinstance(text, str) and text.strip():
                    print(f"Reconocido con {language}: {text}")
                    return text
                    
            except sr.UnknownValueError:
                continue
            except sr.RequestError as e:
                print(f"Error de request con {language}: {e}")
                continue
            except Exception as e:
                print(f"Error inesperado con {language}: {e}")
                continue
        
        raise sr.UnknownValueError("No se pudo reconocer con ningún idioma")

    def _extract_best_result(self, result):
        """Extrae el mejor resultado de un diccionario de reconocimiento."""
        if isinstance(result, str):
            return result
        elif isinstance(result, dict):
            if 'alternative' in result:
                alternatives = result['alternative']
                if alternatives and len(alternatives) > 0:
                    best_alt = alternatives[0]
                    if 'transcript' in best_alt:
                        return best_alt['transcript']
            for key in ['transcript', 'text', 'result']:
                if key in result:
                    return str(result[key])
        elif isinstance(result, list) and len(result) > 0:
            return str(result[0])
        return ""

    def _configure_microphone_for_fast_speech(self):
        """Configuración específica del micrófono para habla rápida"""
        try:
            if not self.microphone:
                self.microphone = sr.Microphone()
            with self.microphone as source:
                for _ in range(3):
                    self.speech_recognizer.adjust_for_ambient_noise(source, duration=0.1)
            original_threshold = self.speech_recognizer.energy_threshold
            self.speech_recognizer.energy_threshold = max(300, int(original_threshold * 0.7))
            # Ajustes finos
            self.speech_recognizer.non_speaking_duration = 0.3
            self.speech_recognizer.pause_threshold = min(self.speech_recognizer.pause_threshold, 0.5)
            print(f"Micrófono configurado para habla rápida. Threshold: {self.speech_recognizer.energy_threshold}")
        except Exception as e:
            print(f"Error configurando micrófono para habla rápida: {e}")

    def _recognize_with_deduplication(self, audio):
        """Reconocimiento con deduplicación estilo ventana temporal."""
        try:
            text = self.speech_recognizer.recognize_google(audio, language='es-CL')
            if not text or not text.strip():
                return ""
            current_time = time.time()
            for timestamp, prev_text in list(self.recent_texts):
                if current_time - timestamp < 2.0:
                    similarity = self._calculate_text_similarity(text, prev_text)
                    if similarity > 0.75:
                        return ""  # duplicado reciente
            self.recent_texts.append((current_time, text))
            cutoff = current_time - 3.0
            self.recent_texts = [(t, txt) for t, txt in self.recent_texts if t > cutoff]
            return text
        except Exception as e:
            print(f"Error en reconocimiento con deduplicación: {e}")
            return ""

    def _calculate_text_similarity(self, text1, text2):
        """Calcula similitud entre textos (simple)."""
        if not text1 or not text2:
            return 0.0
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        if not words1 or not words2:
            return 0.0
        intersection = words1.intersection(words2)
        union = words1.union(words2)
        return len(intersection) / len(union) if union else 0.0

    # ------------------------------------------------------------------
    # UI / Buffer
    # ------------------------------------------------------------------
    def _flush_text_buffer(self):
        """Flush ultra rápido y seguro para la UI (evita perder texto)."""
        if not self.text_buffer:
            return

        pending = self.text_buffer[:]
        self.text_buffer.clear()

        # Limpia placeholder si es el primer flush real
        current_text = self.transcript_preview.toPlainText()
        if "🎤" in current_text and "Transcripción profesional activa" in current_text:
            self.transcript_preview.clear()
            current_text = ""

        # Ensambla líneas cortas preservando párrafos de hablantes
        merged = []
        current_paragraph = []

        for chunk in pending:
            chunk_str = self._process_fast_speech_text(str(chunk))
            
            # Si el chunk empieza con salto de línea, es nuevo párrafo
            if chunk_str.startswith('\n\n'):
                # Finalizar párrafo actual
                if current_paragraph:
                    merged.append(" ".join(current_paragraph))
                    current_paragraph = []
                # Agregar nuevo párrafo
                clean_chunk = chunk_str.replace('\n\n', '').strip()
                if clean_chunk:
                    current_paragraph = [clean_chunk]
            else:
                # Agregar al párrafo actual
                clean_chunk = chunk_str.strip()
                if clean_chunk:
                    current_paragraph.append(clean_chunk)
            
            # Si el párrafo se vuelve muy largo, dividir
            if current_paragraph and len(" ".join(current_paragraph)) > 150:
                merged.append(" ".join(current_paragraph))
                current_paragraph = []

        # Agregar último párrafo
        if current_paragraph:
            merged.append(" ".join(current_paragraph))

        # Inserta de una vez para minimizar coste en el hilo de GUI
        if merged:
            cursor = self.transcript_preview.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            for line in merged:
                self.transcript_preview.append(line)
            self.transcript_preview.setTextCursor(cursor)

        # Forzar repintado rápido
        self.transcript_preview.update()
        QCoreApplication.processEvents()
    def _process_fast_speech_text(self, text: str) -> str:
        """Procesa texto específicamente para habla rápida"""
        if not isinstance(text, str):
            text = str(text)
        text = ' '.join(text.split())
        fast_speech_corrections = {
            'esque': 'es que',
            'porfa': 'por favor', 
            'obvio': 'obviamente',
            'osea': 'o sea',
            'porfavor': 'por favor',
            'nose': 'no sé',
            'nomas': 'no más',
            'aver': 'a ver',
            'deuna': 'de una',
            'yapo': 'ya poh'
        }
        words = text.split()
        corrected_words = []
        for word in words:
            word_lower = word.lower().strip('.,!?')
            corrected_words.append(fast_speech_corrections.get(word_lower, word))
        return ' '.join(corrected_words)
    def _detect_speaker_continuation(self, new_text: str) -> bool:
        """Detecta si el texto continúa del mismo hablante"""
        if not self.realtime_text:
            return False
        
        last_text = self.realtime_text[-1] if self.realtime_text else ""
        
        # Patrones que indican continuación del mismo hablante
        continuation_patterns = [
            r'\b(entonces|después|luego|también|además|y|pero|sin embargo)\b',
            r'\b(por eso|por lo tanto|así que|porque)\b',
            r'\b(ahora|ahí|aquí|esto|eso)\b'
        ]
        
        # Verificar si el nuevo texto comienza con palabras de continuación
        new_text_lower = new_text.lower().strip()
        for pattern in continuation_patterns:
            if re.match(pattern, new_text_lower):
                return True
        
        # Verificar si no hay cambio abrupto de tema
        last_words = last_text.split()[-3:] if last_text else []
        new_words = new_text.split()[:3]
        
        # Si hay palabras en común, probablemente es continuación
        common_words = set(word.lower() for word in last_words) & set(word.lower() for word in new_words)
        if len(common_words) > 0:
            return True
        
        return False

    def _should_create_new_paragraph(self, new_text: str) -> bool:
        """Determina si se debe crear un nuevo párrafo"""
        if not self.realtime_text:
            return False
        
        # Patrones que indican nuevo hablante o tema
        new_speaker_patterns = [
            r'^[A-Z][a-z]+:',  # "Juan:"
            r'\b(bueno|ok|vale|perfecto|excelente|gracias)\b',  # Palabras de transición
            r'\b(pregunta|comentario|opinión|creo que|pienso que)\b'
        ]
        
        new_text_lower = new_text.lower().strip()
        for pattern in new_speaker_patterns:
            if re.search(pattern, new_text_lower):
                return True
        
        # Si hay una pausa larga (esto se puede detectar por timestamp si está disponible)
        # Por ahora, usar heurística simple
        last_text = self.realtime_text[-1] if self.realtime_text else ""
        if len(last_text) > 0 and not self._detect_speaker_continuation(new_text):
            return True
        
        return False
    def _handle_text_update(self, text: str):
        """Maneja actualización de texto thread-safe con detección de hablantes"""
        if not isinstance(text, str) or not text.strip():
            return
        
        # Determinar si es continuación o nuevo párrafo
        if self._should_create_new_paragraph(text):
            # Nuevo párrafo
            self.text_buffer.append(f"\n\n{text}")
        elif self._detect_speaker_continuation(text):
            # Continuar en la misma línea
            self.text_buffer.append(f" {text}")
        else:
            # Comportamiento por defecto (nueva línea)
            self.text_buffer.append(text)
        
        self._auto_generate_title()

    # ------------------------------------------------------------------
    # Permisos de micrófono
    # ------------------------------------------------------------------
    def _check_and_request_microphone_access(self):
        """Verifica permisos y guía al usuario si es necesario"""
        try:
            recognizer = sr.Recognizer()
            mic = sr.Microphone()
            with mic as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
            return True
        except Exception as e:
            print(f"Error accediendo al micrófono: {e}")
            try:
                if self._request_microphone_permission():
                    return True
            except Exception as ex:
                print(f"Error solicitando permisos de micrófono: {ex}")
            self._show_permission_guide()
            return False

    def _show_permission_guide(self):
        """Muestra guía detallada para habilitar permisos"""
        guide_text = """
Para habilitar el micrófono:

1. Ve a Configuración del Sistema (o Preferencias del Sistema)
2. Selecciona "Privacidad y Seguridad"
3. En el panel izquierdo, busca "Micrófono"
4. Asegúrate de que esta aplicación esté marcada ✓

Aplicaciones que pueden aparecer:
- Python
- Terminal
- PyCharm (si usas IDE)
- SecreIA

Después de habilitar los permisos, reinicia la aplicación.
        """
        
        msg = QMessageBox(self)
        msg.setWindowTitle("Configurar permisos de micrófono")
        msg.setText("Permisos de micrófono requeridos")
        msg.setDetailedText(guide_text)
        msg.setIcon(QMessageBox.Information)
        
        open_settings_btn = msg.addButton("Abrir Configuración", QMessageBox.ActionRole)
        try_again_btn = msg.addButton("Intentar de nuevo", QMessageBox.AcceptRole)
        cancel_btn = msg.addButton("Cancelar", QMessageBox.RejectRole)
        
        msg.exec()
        
        clicked_button = msg.clickedButton()
        
        if clicked_button == open_settings_btn:
            self._open_system_preferences()
        elif clicked_button == try_again_btn:
            QTimer.singleShot(1000, self._test_speech_recognition)

    def _open_system_preferences(self):
        """Abre la configuración del sistema en la sección correcta"""
        import subprocess
        import sys
        
        try:
            if sys.platform == "darwin":
                subprocess.run([
                    "open", 
                    "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
                ])
            else:
                QMessageBox.information(
                    self,
                    "Configuración manual",
                    "Busca 'Permisos de micrófono' en la configuración de tu sistema."
                )
        except Exception:
            if sys.platform == "darwin":
                subprocess.run(["open", "/System/Applications/System Preferences.app"])

    def _request_microphone_permission(self):
        """Solicita permisos de micrófono de forma natural"""
        try:
            reply = QMessageBox.question(
                self, 
                "Permisos de micrófono",
                "Esta aplicación necesita acceso al micrófono para transcribir audio.\n\n"
                "macOS te pedirá permisos cuando continúes.\n"
                "Por favor, selecciona 'Permitir' cuando aparezca el diálogo.",
                QMessageBox.Ok | QMessageBox.Cancel
            )
            if reply != QMessageBox.Ok:
                return False
            
            recognizer = sr.Recognizer()
            mic = sr.Microphone()
            with mic as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
            return True
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error de permisos",
                f"No se pudieron obtener permisos de micrófono.\n\n"
                f"Error: {e}\n\n"
                f"Ve a Configuración del Sistema > Privacidad y Seguridad > Micrófono"
            )
            return False
    
    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _setup_ui(self):
        """Configura interfaz de transcripción en tiempo real"""
        layout = QVBoxLayout(self)
        layout.setSpacing(24)
        layout.setContentsMargins(40, 40, 40, 40)
        
        # Header
        header = QLabel("Transcripción en tiempo real")
        header.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.PRIMARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 28px;
                font-weight: 300;
            }}
        """)
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)
        
        # Estado del reconocimiento
        status_card = AppleCard()
        status_layout = status_card.layout()
        
        if self.recognition_working:
            status_text = "Reconocimiento de voz: Listo"
            status_color = AppleColors.GREEN.name()
            status_icon = "✅"
        else:
            status_text = "Reconocimiento de voz: No disponible - Verifica micrófono y permisos"
            status_color = AppleColors.RED.name()
            status_icon = "❌"
        
        self.status_info = QLabel(f"{status_icon} {status_text}")
        self.status_info.setStyleSheet(f"""
            QLabel {{
                color: {status_color};
                font-size: 14px;
                font-weight: 500;
                padding: 8px;
                background: transparent;
                border: none;
            }}
        """)
        status_layout.addWidget(self.status_info)
        layout.addWidget(status_card)
        
        # Panel de controles principales
        controls_card = AppleCard()
        controls_layout = controls_card.layout()
        
        # Botones principales
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(16)
        
        self.btn_start = AppleButton("🎤 Iniciar transcripción", "primary")
        self.btn_start.clicked.connect(self.start_transcription)
        self.btn_start.setFixedHeight(50)
        self.btn_start.setEnabled(self.recognition_working)
        
        self.btn_stop = AppleButton("⏹ Detener transcripción", "danger")
        self.btn_stop.clicked.connect(self.stop_transcription)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setFixedHeight(50)
        self.btn_stop.hide()
        
        self.btn_help = AppleButton("🔧 Ayuda con permisos", "ghost")
        self.btn_help.clicked.connect(self._show_permission_guide)
        
        buttons_layout.addWidget(self.btn_start)
        buttons_layout.addWidget(self.btn_stop)
        buttons_layout.addWidget(self.btn_help)
        
        # Indicadores de estado
        status_widget = QWidget()
        status_widget_layout = QVBoxLayout(status_widget)
        status_widget_layout.setContentsMargins(0, 0, 0, 0)
        status_widget_layout.setSpacing(4)
        
        status_title = QLabel("Estado:")
        status_title.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.SECONDARY.name()};
                font-size: 11px;
                border: none;
            }}
        """)
        
        self.status_indicator = QLabel("Listo")
        self.status_indicator.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.SECONDARY.name()};
                font-size: 12px;
                border: none;
                padding: 4px 8px;
                background-color: {AppleColors.CARD.name()};
                border-radius: 4px;
            }}
        """)
        
        status_widget_layout.addWidget(status_title)
        status_widget_layout.addWidget(self.status_indicator)
        
        buttons_layout.addWidget(status_widget)
        buttons_layout.addStretch()
        
        controls_layout.addLayout(buttons_layout)
        
        # Status general
        self.status_label = QLabel("Listo para transcribir" if self.recognition_working else "Configura micrófono para continuar")
        self.status_label.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.SECONDARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 13px;
                padding: 8px 12px;
                border: none;
                background-color: {AppleColors.ELEVATED.name()};
                border-radius: 6px;
            }}
        """)
        self.status_label.setAlignment(Qt.AlignCenter)
        controls_layout.addWidget(self.status_label)
        
        # Separador
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setStyleSheet(f"""
            QFrame {{
                color: {AppleColors.SEPARATOR_LIGHT.name()};
                margin: 8px 0;
            }}
        """)
        controls_layout.addWidget(separator)
        
        # Configuración de nota
        config_layout = QVBoxLayout()
        config_layout.setSpacing(12)
        
        # Título
        title_layout = QHBoxLayout()
        title_layout.setSpacing(12)
        
        title_label = QLabel("Título:")
        title_label.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.PRIMARY.name()};
                font-size: 13px;
                font-weight: 500;
                background: transparent;
                border: none;
                min-width: 60px;
            }}
        """)
        
        self.title_edit = QLineEdit("Título automático...")
        self.title_edit.setStyleSheet(f"""
            QLineEdit {{
                background-color: {AppleColors.SIDEBAR.name()};
                color: {AppleColors.PRIMARY.name()};
                border: 1px solid {AppleColors.SEPARATOR.name()};
                border-radius: 6px;
                padding: 8px 12px;
                font-family: '.AppleSystemUIFont';
                font-size: 13px;
            }}
            QLineEdit:focus {{
                border: 2px solid {AppleColors.BLUE.name()};
                padding: 7px 11px;
                background-color: {AppleColors.NOTES_LIST.name()};
            }}
        """)
        
        title_layout.addWidget(title_label)
        title_layout.addWidget(self.title_edit, 1)
        
        # Categoría
        category_layout = QHBoxLayout()
        category_layout.setSpacing(12)
        
        category_label = QLabel("Categoría:")
        category_label.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.PRIMARY.name()};
                font-size: 13px;
                font-weight: 500;
                background: transparent;
                border: none;
                min-width: 70px;
            }}
        """)
        
        self.category_combo = QComboBox()
        self.category_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {AppleColors.SIDEBAR.name()};
                color: {AppleColors.PRIMARY.name()};
                border: 1px solid {AppleColors.SEPARATOR.name()};
                border-radius: 6px;
                padding: 8px 12px;
                font-family: '.AppleSystemUIFont';
                font-size: 13px;
                min-width: 150px;
            }}
            QComboBox::drop-down {{
                border: none;
            }}
            QComboBox QAbstractItemView {{
                background-color: {AppleColors.ELEVATED.name()};
                border: 1px solid {AppleColors.SEPARATOR.name()};
                selection-background-color: {AppleColors.BLUE.name()};
                selection-color: white;
            }}
        """)
        
        # Cargar categorías
        categories = self.db.list_categories()
        if "Transcripciones" not in categories:
            categories.insert(0, "Transcripciones")
        for cat in categories:
            self.category_combo.addItem(cat)
        self.category_combo.setCurrentText("Transcripciones")
        
        category_layout.addWidget(category_label)
        category_layout.addWidget(self.category_combo)
        category_layout.addStretch()
        
        config_layout.addLayout(title_layout)
        config_layout.addLayout(category_layout)
        controls_layout.addLayout(config_layout)
        
        layout.addWidget(controls_card)
        
        # Vista previa de transcripción
        preview_header = QLabel("Transcripción en vivo")
        preview_header.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.PRIMARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 16px;
                font-weight: 600;
                margin-top: 8px;
            }}
        """)
        layout.addWidget(preview_header)
        
        self.transcript_preview = QTextEdit()
        self.transcript_preview.setReadOnly(True)
        self.transcript_preview.setPlaceholderText("El texto aparecerá aquí mientras hablas...")
        self.transcript_preview.setStyleSheet(f"""
            QTextEdit {{
                background-color: {AppleColors.NOTES_LIST.name()};
                color: {AppleColors.PRIMARY.name()};
                border: none;
                border-radius: 12px;
                padding: 20px;
                font-family: '.AppleSystemUIFont';
                font-size: 15px;
                line-height: 1.6;
            }}
        """)
        
        layout.addWidget(self.transcript_preview, 3)
        
        # Botones de acción
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(12)
        
        self.btn_save_note = AppleButton("💾 Guardar como nota", "secondary")
        self.btn_save_note.clicked.connect(self._save_as_note)
        self.btn_save_note.setEnabled(False)
        
        self.btn_copy = AppleButton("📋 Copiar texto", "ghost")
        self.btn_copy.clicked.connect(self._copy_transcription)
        self.btn_copy.setEnabled(False)
        
        self.btn_clear = AppleButton("🗑️ Limpiar", "ghost")
        self.btn_clear.clicked.connect(self._clear_transcription)
        self.btn_clear.setEnabled(False)
        
        actions_layout.addWidget(self.btn_save_note)
        actions_layout.addWidget(self.btn_copy)
        actions_layout.addWidget(self.btn_clear)
        actions_layout.addStretch()
        layout.addLayout(actions_layout)
        
        # Conectar eventos
        self.transcript_preview.textChanged.connect(self._on_transcript_changed)
        
        # Timer para duración
        self.duration_timer = QTimer()
        self.duration_timer.timeout.connect(self._update_duration)
        
        # Timer para actualizaciones de UI
        self.ui_update_timer = QTimer()
        self.ui_update_timer.timeout.connect(self._flush_text_buffer)
        self.ui_update_timer.setInterval(int(self.update_interval * 1000))

    # ------------------------------------------------------------------
    # Estado/Errores/Auto título
    # ------------------------------------------------------------------
    def _handle_status_update(self, status: str):
        """Maneja actualizaciones de estado"""
        if status == "silence_warning":
            self._show_silence_warning()

    def _handle_error_update(self, error: str):
        """Maneja errores"""
        if error == "api_error":
            self._show_api_error()
        elif error == "recognition_failed":
            self._show_recognition_failed()
        else:
            self._handle_recognition_error(error)

    def _auto_generate_title(self):
        """Genera título automáticamente"""
        if self.title_edit.text().strip() in ["", "Título automático..."]:
            combined_text = " ".join(self.realtime_text)
            if len(combined_text.split()) >= 3:
                words = combined_text.split()[:6]
                suggested_title = " ".join(words)
                if len(combined_text.split()) > 6:
                    suggested_title += "..."
                self.title_edit.setText(suggested_title)

    def _show_silence_warning(self):
        """Muestra advertencia de silencio"""
        self.text_buffer.append("\n⚠️ Silencio detectado - habla más cerca del micrófono")

    def _show_api_error(self):
        """Muestra error de API"""
        self.text_buffer.append("\n❌ Error de conexión - reintentando...")

    def _show_recognition_failed(self):
        """Muestra falla del reconocimiento"""
        self.text_buffer.append("\n❌ Reconocimiento falló - detén y vuelve a intentar")

    def _handle_recognition_error(self, error: str):
        """Maneja errores generales"""
        self.text_buffer.append(f"\n❌ Error: {error}")

    # ------------------------------------------------------------------
    # Mantenimiento de UI/Estado
    # ------------------------------------------------------------------
    def _reset_state(self):
        """Resetea estado de la interfaz"""
        self.btn_start.show()
        self.btn_stop.hide()
        self.btn_start.setEnabled(self.recognition_working)
        self.btn_stop.setEnabled(False)
        
        self.status_indicator.setText("Listo")
        self.status_indicator.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.SECONDARY.name()};
                font-size: 12px;
                border: none;
                padding: 4px 8px;
                background-color: {AppleColors.CARD.name()};
                border-radius: 4px;
            }}
        """)
        
        if hasattr(self, 'duration_timer'):
            self.duration_timer.stop()
        
        if hasattr(self, 'ui_update_timer'):
            self.ui_update_timer.stop()
        
        self.is_transcribing = False
        self.recognition_active = False

    def _update_duration(self):
        """Actualiza duración"""
        if self.is_transcribing and self.start_time:
            duration = time.time() - self.start_time
            mins = int(duration // 60)
            secs = int(duration % 60)
            self.status_label.setText(f"Transcribiendo... {mins:02d}:{secs:02d}")

    def _on_transcript_changed(self):
        """Habilita botones según contenido"""
        text = self.transcript_preview.toPlainText().strip()
        has_valid_text = bool(text and not text.startswith("🎤") and len(text) > 10)
        
        self.btn_save_note.setEnabled(has_valid_text)
        self.btn_copy.setEnabled(has_valid_text)
        self.btn_clear.setEnabled(bool(text))

    # ------------------------------------------------------------------
    # Guardado / Portapapeles
    # ------------------------------------------------------------------
    def _save_as_note(self):
        """Guarda como nota con loading"""
        content = self.transcript_preview.toPlainText().strip()
        title = self.title_edit.text().strip()
        
        if not content or content.startswith("🎤") or len(content) < 10:
            QMessageBox.information(self, "Sin contenido", "No hay suficiente texto para guardar.")
            return
        
        content = self._clean_content(content)
        
        if not title or title == "Título automático...":
            title = "Transcripción " + datetime.now().strftime("%d/%m/%Y %H:%M")
        
        # Mostrar loading
        self._show_transcription_saving_state()
        
        # Usar QTimer para no bloquear UI
        QTimer.singleShot(100, lambda: self._do_save_transcription(title, content))

    def _show_transcription_saving_state(self):
        """Muestra estado de guardado para transcripción"""
        self.btn_save_note.setEnabled(False)
        self.btn_save_note.setText("Guardando...")
        
        # Crear spinner si no existe
        if not hasattr(self, 'save_spinner'):
            self.save_spinner = LoadingSpinner(20)
            # Agregar spinner al layout de botones de acción
            actions_layout = self.btn_save_note.parent().layout()
            actions_layout.insertWidget(0, self.save_spinner)
        
        self.save_spinner.show()
        self.save_spinner.start()

    def _do_save_transcription(self, title: str, content: str):
        """Ejecuta el guardado real de transcripción"""
        try:
            self._save_transcription(title, content)
            self._show_transcription_success_state()
            QTimer.singleShot(2000, self._clear_transcription)
        except Exception as e:
            self._show_transcription_error_state(str(e))

    def _show_transcription_success_state(self):
        """Muestra estado de éxito para transcripción"""
        if hasattr(self, 'save_spinner'):
            self.save_spinner.stop()
            self.save_spinner.hide()
        
        self.btn_save_note.setEnabled(True)
        self.btn_save_note.setText("✅ Guardado")
        
        # Restaurar después de 3 segundos
        QTimer.singleShot(3000, lambda: self.btn_save_note.setText("💾 Guardar como nota"))

    def _show_transcription_error_state(self, error: str):
        """Muestra estado de error para transcripción"""
        if hasattr(self, 'save_spinner'):
            self.save_spinner.stop()
            self.save_spinner.hide()
        
        self.btn_save_note.setEnabled(True)
        self.btn_save_note.setText("❌ Error")
        
        QMessageBox.critical(self, "Error", f"Error al guardar transcripción: {error}")
        QTimer.singleShot(3000, lambda: self.btn_save_note.setText("💾 Guardar como nota"))
    def _clean_content(self, content: str) -> str:
        """Limpia el contenido de mensajes del sistema"""
        content = content.replace("🎤 Escuchando...", "").strip()
        lines = content.split("\n")
        clean_lines = []
        for line in lines:
            line = line.strip()
            if line and not line.startswith("⚠️") and not line.startswith("❌"):
                clean_lines.append(line)
        return " ".join(clean_lines)

    def _copy_transcription(self):
        """Copia al portapapeles"""
        text = self.transcript_preview.toPlainText().strip()
        if text and not text.startswith("🎤"):
            clean_text = self._clean_content(text)
            try:
                if pyperclip is not None:
                    pyperclip.copy(clean_text)
                else:
                    clipboard = QApplication.clipboard()
                    clipboard.setText(clean_text)
                QMessageBox.information(self, "Copiado", "Texto copiado al portapapeles")
            except Exception:
                clipboard = QApplication.clipboard()
                clipboard.setText(clean_text)
                QMessageBox.information(self, "Copiado", "Texto copiado al portapapeles")

    def _clear_transcription(self):
        """Limpia la transcripción"""
        self.transcript_preview.clear()
        self.title_edit.setText("Título automático...")
        self.realtime_text = []
        self.text_buffer = []
        self.status_label.setText("Listo para transcribir" if self.recognition_working else "Configura micrófono para continuar")

    def _save_transcription(self, title: str, content: str):
        """Guarda en base de datos"""
        category = self.category_combo.currentText().strip() or "Transcripciones"
        
        self.db.add_category(category)
        note = Note(
            id=None,
            title=title,
            content=content,
            category=category,
            tags=["transcripción", "tiempo-real"],
            source="transcript",
            audio_path=None,  # Sin archivo de audio
            created_at=datetime.utcnow().isoformat(),
            updated_at=datetime.utcnow().isoformat(),
        )
        note_id = self.db.upsert_note(note)

        if self.vector:
            try:
                self.vector.index_note(note_id, title, content)
            except Exception as e:
                print(f"Error indexando: {e}")

class SearchTab(QWidget):
    """Tab de búsqueda estilo Apple - CORREGIDO"""
    
    def __init__(self, settings: Settings, db: NotesDB, vector: Optional[VectorIndex], ai: AIService):
        super().__init__()
        self.settings = settings
        self.db = db
        self.vector = vector
        self.ai = ai
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(32)
        layout.setContentsMargins(60, 60, 60, 60)
        
        # Header
        header = QLabel("Búsqueda avanzada")
        header.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.PRIMARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 32px;
                font-weight: 300;
            }}
        """)
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)
        
        # Barra de búsqueda
        search_card = AppleCard()
        search_layout = search_card.layout()
        
        search_input_layout = QVBoxLayout()
        search_input_layout.setSpacing(16)
        
        self.q_edit = AppleLineEdit("Buscar en tus notas...")
        self.q_edit.setFixedHeight(50)
        self.q_edit.setStyleSheet(self.q_edit.styleSheet() + f"""
            QLineEdit {{ 
                font-size: 16px;
                padding: 15px 20px;
            }}
        """)
        search_input_layout.addWidget(self.q_edit)
        
        # Botones de tipo de búsqueda
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(12)
        
        self.btn_keyword = AppleButton("🔤 Búsqueda de texto", "primary")
        self.btn_semantic = AppleButton("🧠 Búsqueda semántica", "secondary")
        
        if not self.vector:
            self.btn_semantic.setEnabled(False)
            self.btn_semantic.setToolTip("Requiere configuración de OpenAI API")
        
        buttons_layout.addWidget(self.btn_keyword)
        buttons_layout.addWidget(self.btn_semantic)
        buttons_layout.addStretch()
        
        search_input_layout.addLayout(buttons_layout)
        search_layout.addLayout(search_input_layout)
        layout.addWidget(search_card)
        
        # Resultados con contador
        results_header = QHBoxLayout()
        self.results_title = QLabel("Resultados")
        self.results_title.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.PRIMARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 18px;
                font-weight: 600;
            }}
        """)
        
        self.results_count = QLabel("0 resultados")
        self.results_count.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.SECONDARY.name()};
                font-size: 14px;
            }}
        """)
        
        results_header.addWidget(self.results_title)
        results_header.addStretch()
        results_header.addWidget(self.results_count)
        layout.addLayout(results_header)
        
        self.results = QListWidget()
        self.results.setStyleSheet(f"""
            QListWidget {{
                background-color: transparent;
                border: none;
                outline: none;
                font-family: '.AppleSystemUIFont';
                font-size: 14px;
            }}
            QListWidget::item {{
                background-color: {AppleColors.ELEVATED.name()};
                color: {AppleColors.PRIMARY.name()};
                border-radius: 8px;
                padding: 16px;
                margin-bottom: 8px;
            }}
            QListWidget::item:hover {{
                background-color: {AppleColors.BLUE.name()};
                color: white;
            }}
            QListWidget::item:selected {{
                background-color: {AppleColors.BLUE.name()};
                color: white;
            }}
        """)
        layout.addWidget(self.results, 1)
        
        # Conectar eventos
        self.btn_keyword.clicked.connect(self.search_keyword)
        self.btn_semantic.clicked.connect(self.search_semantic)
        self.q_edit.returnPressed.connect(self.search_keyword)

    def search_keyword(self):
        """Búsqueda por texto"""
        query = self.q_edit.text().strip()
        if not query:
            return
            
        self.results.clear()
        self.btn_keyword.setText("Buscando...")
        self.btn_keyword.setEnabled(False)
        
        try:
            notes = self.db.search_notes(query)
            
            if not notes:
                item = QListWidgetItem("No se encontraron resultados")
                item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
                self.results.addItem(item)
                self.results_count.setText("0 resultados")
            else:
                for n in notes:
                    content_lower = n.content.lower()
                    query_lower = query.lower()
                    
                    pos = content_lower.find(query_lower)
                    if pos != -1:
                        start = max(0, pos - 50)
                        end = min(len(n.content), pos + len(query) + 50)
                        snippet = n.content[start:end]
                        if start > 0:
                            snippet = "..." + snippet
                        if end < len(n.content):
                            snippet = snippet + "..."
                    else:
                        snippet = (n.content[:200] + "...") if len(n.content) > 200 else n.content
                    
                    item_text = f"[TEXTO] {n.title}\n{n.category} • {n.updated_at[:19]}\n{snippet}"
                    item = QListWidgetItem(item_text)
                    item.setData(Qt.UserRole, n.id)
                    item.setToolTip(n.content[:500])
                    self.results.addItem(item)
                
                self.results_count.setText(f"{len(notes)} resultados")
                    
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Error en búsqueda: {e}")
        finally:
            self.btn_keyword.setText("🔤 Búsqueda de texto")
            self.btn_keyword.setEnabled(True)

    def search_semantic(self):
        """Búsqueda semántica"""
        if not self.vector:
            QMessageBox.information(self, APP_NAME, 
                                  "La búsqueda semántica está deshabilitada.\n"
                                  "Configura tu OpenAI API key en Ajustes.")
            return
            
        query = self.q_edit.text().strip()
        if not query:
            return
            
        self.results.clear()
        self.btn_semantic.setText("Buscando...")
        self.btn_semantic.setEnabled(False)
        
        try:
            results = self.vector.search(query, top_k=self.settings.top_k)
            
            if not results:
                item = QListWidgetItem("No se encontraron resultados semánticamente similares")
                item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
                self.results.addItem(item)
                self.results_count.setText("0 resultados")
            else:
                for r in results:
                    similarity_pct = (1 - r['score']) * 100
                    item_text = (
                        f"[SEMÁNTICA] {r['title']}\n"
                        f"Similitud: {similarity_pct:.1f}% • Relevancia: {'⭐' * min(5, int(similarity_pct/20))}\n"
                        f"{r['snippet']}"
                    )
                    item = QListWidgetItem(item_text)
                    item.setData(Qt.UserRole, r['note_id'])
                    item.setToolTip(r['snippet'])
                    self.results.addItem(item)
                
                self.results_count.setText(f"{len(results)} resultados")
                    
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Error en búsqueda semántica: {e}")
        finally:
            self.btn_semantic.setText("🧠 Búsqueda semántica")
            self.btn_semantic.setEnabled(True)
class AnalyzeTab(QWidget):
    """Tab de análisis con RAG estilo Apple - MEJORADO"""
    
    def __init__(self, settings: Settings, db: NotesDB, vector: Optional[VectorIndex], ai: AIService):
        super().__init__()
        self.settings = settings
        self.db = db
        self.vector = vector
        self.ai = ai
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(32)
        layout.setContentsMargins(60, 60, 60, 60)
        
        # Header
        header = QLabel("Análisis inteligente")
        header.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.PRIMARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 32px;
                font-weight: 300;
            }}
        """)
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)
        
        # Descripción
        description = QLabel("Haz preguntas sobre el contenido de tus notas. La IA analizará tu información para darte respuestas precisas.")
        description.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.SECONDARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 16px;
            }}
        """)
        description.setWordWrap(True)
        description.setAlignment(Qt.AlignCenter)
        layout.addWidget(description)
        
        # Input de pregunta
        question_card = AppleCard()
        question_layout = question_card.layout()
        
        question_input_layout = QVBoxLayout()
        question_input_layout.setSpacing(16)
        
        self.q_edit = AppleLineEdit("¿Qué quieres saber sobre tus notas?")
        self.q_edit.setFixedHeight(50)
        self.q_edit.setStyleSheet(self.q_edit.styleSheet() + """
            QLineEdit { 
                font-size: 16px;
                padding: 15px 20px;
            }
        """)
        question_input_layout.addWidget(self.q_edit)
        
        # Configuraciones
        config_layout = QHBoxLayout()
        config_layout.setSpacing(16)
        
        config_layout.addWidget(QLabel("Documentos a considerar:"))
        
        self.k_spin = QSpinBox()
        self.k_spin.setRange(1, 20)
        self.k_spin.setValue(self.settings.top_k)
        self.k_spin.setStyleSheet(f"""
            QSpinBox {{
                background-color: {AppleColors.CARD.name()};
                color: {AppleColors.PRIMARY.name()};
                border: 1px solid {AppleColors.SEPARATOR_LIGHT.name()};
                border-radius: 6px;
                padding: 8px 12px;
                font-family: '.AppleSystemUIFont';
                font-size: 14px;
                min-width: 60px;
            }}
        """)
        config_layout.addWidget(self.k_spin)
        
        self.btn_ask = AppleButton("🤖 Analizar", "primary")
        if not self.vector:
            self.btn_ask.setEnabled(False)
            self.btn_ask.setToolTip("Requiere configuración de OpenAI API")
        config_layout.addWidget(self.btn_ask)
        config_layout.addStretch()
        
        question_input_layout.addLayout(config_layout)
        question_layout.addLayout(question_input_layout)
        layout.addWidget(question_card)
        
        # Respuesta con botones de acción
        answer_header = QHBoxLayout()
        answer_title = QLabel("Análisis")
        answer_title.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.PRIMARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 18px;
                font-weight: 600;
            }}
        """)
        
        self.copy_answer_btn = AppleButton("📋 Copiar", "ghost")
        self.copy_answer_btn.clicked.connect(self._copy_answer)
        self.copy_answer_btn.setEnabled(False)
        
        answer_header.addWidget(answer_title)
        answer_header.addStretch()
        answer_header.addWidget(self.copy_answer_btn)
        layout.addLayout(answer_header)
        
        self.answer = QTextEdit()
        self.answer.setReadOnly(True)
        self.answer.setPlaceholderText("Las respuestas del análisis aparecerán aquí...")
        self.answer.setStyleSheet(f"""
            QTextEdit {{
                background-color: {AppleColors.NOTES_LIST.name()};
                color: {AppleColors.PRIMARY.name()};
                border: none;
                border-radius: 8px;
                padding: 20px;
                font-family: '.AppleSystemUIFont';
                font-size: 15px;
                line-height: 1.6;
            }}
        """)
        self.answer.textChanged.connect(self._on_answer_changed)
        layout.addWidget(self.answer, 1)
        
        # Conectar eventos
        self.btn_ask.clicked.connect(self.ask)
        self.q_edit.returnPressed.connect(self.ask)
    
    def _on_answer_changed(self):
        """Habilita botón de copiar cuando hay respuesta"""
        has_text = bool(self.answer.toPlainText().strip())
        self.copy_answer_btn.setEnabled(has_text)
    
    def _copy_answer(self):
        """Copia respuesta al portapapeles"""
        try:
            text = self.answer.toPlainText()
            if text:
                pyperclip.copy(text)
                QMessageBox.information(self, "Copiado", "Respuesta copiada al portapapeles")
        except ImportError:
            QMessageBox.warning(self, "Error", "pyperclip no está disponible")

    def ask(self):
        if not self.vector:
            QMessageBox.information(self, APP_NAME, 
                                  "El análisis inteligente está deshabilitado.\n"
                                  "Configura tu OpenAI API key en Ajustes.")
            return
            
        question = self.q_edit.text().strip()
        if not question:
            return
            
        self.btn_ask.setText("Analizando...")
        self.btn_ask.setEnabled(False)
        self.answer.clear()
        
        QTimer.singleShot(100, lambda: self._do_analysis(question))
    
    def _do_analysis(self, question: str):
        try:
            retrieved = self.vector.search(question, top_k=self.k_spin.value())
            if not retrieved:
                self.answer.setPlainText("No se encontraron notas relevantes para responder a tu pregunta.")
                return
            
            contexts = []
            for r in retrieved:
                n = self.db.get_note(int(r["note_id"]))
                if n:
                    contexts.append({"title": n.title, "content": n.content[:4000]})
            
            if not contexts:
                self.answer.setPlainText("No se pudieron cargar las notas relevantes.")
                return
            
            answer = self.ai.answer_with_context(question, contexts, extended_analysis=True)
            
            response_text = f"{answer}\n\n"
            response_text += "─" * 50 + "\n"
            response_text += "📚 Fuentes consultadas:\n\n"
            for i, context in enumerate(contexts, 1):
                response_text += f"{i}. {context['title']}\n"
            
            self.answer.setPlainText(response_text)
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error en el análisis: {e}")
            self.answer.setPlainText(f"Error al procesar la consulta: {e}")
        finally:
            self.btn_ask.setText("🤖 Analizar")
            self.btn_ask.setEnabled(True)

class SettingsTab(QWidget):
    """Tab de configuraciones estilo Apple - MEJORADO"""
    
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(24)
        layout.setContentsMargins(40, 40, 40, 40)
        
        # Header
        header = QLabel("Configuraciones")
        header.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.PRIMARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 28px;
                font-weight: 300;
            }}
        """)
        layout.addWidget(header)
        
        # API Configuration
        api_card = AppleCard("Configuración de OpenAI", "Configura tu acceso a los servicios de IA")
        api_layout = api_card.layout()
        
        # Grid layout para campos organizados
        grid_layout = QGridLayout()
        grid_layout.setSpacing(16)
        grid_layout.setColumnStretch(1, 1)  # La columna de inputs se expande
        
        # Estilo común para labels (sin bordes)
        label_style = f"""
            QLabel {{
                color: {AppleColors.PRIMARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 14px;
                font-weight: 500;
                background: transparent;
                border: none;
                padding: 0;
            }}
        """
        
        # Estilo común para inputs (fondo más oscuro)
        input_style = f"""
            QLineEdit {{
                background-color: {AppleColors.SIDEBAR.name()};
                color: {AppleColors.PRIMARY.name()};
                border: 1px solid {AppleColors.SEPARATOR.name()};
                border-radius: 8px;
                padding: 12px 16px;
                font-family: '.AppleSystemUIFont';
                font-size: 14px;
                selection-background-color: {AppleColors.BLUE.name()};
            }}
            QLineEdit:focus {{
                border: 2px solid {AppleColors.BLUE.name()};
                padding: 11px 15px;
                background-color: {AppleColors.NOTES_LIST.name()};
            }}
        """
        
        # API Key (fila 0)
        api_key_label = QLabel("API Key:")
        api_key_label.setStyleSheet(label_style)
        api_key_label.setFixedWidth(160)
        
        api_key_container = QWidget()
        api_key_layout = QHBoxLayout(api_key_container)
        api_key_layout.setContentsMargins(0, 0, 0, 0)
        api_key_layout.setSpacing(8)
        
        self.api_key = QLineEdit()
        self.api_key.setPlaceholderText("sk-proj-...")
        self.api_key.setText(self.settings.openai_api_key or "")
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setStyleSheet(input_style)
        
        self.toggle_visibility = AppleButton("👁", "ghost")
        self.toggle_visibility.setFixedSize(36, 36)
        self.toggle_visibility.clicked.connect(self._toggle_api_key_visibility)
        
        api_key_layout.addWidget(self.api_key, 1)
        api_key_layout.addWidget(self.toggle_visibility)
        
        grid_layout.addWidget(api_key_label, 0, 0)
        grid_layout.addWidget(api_key_container, 0, 1)
        
        # Modelo de chat (fila 1)
        chat_label = QLabel("Modelo de chat:")
        chat_label.setStyleSheet(label_style)
        chat_label.setFixedWidth(160)
        
        self.chat_model = QLineEdit(self.settings.chat_model)
        self.chat_model.setStyleSheet(input_style)
        
        grid_layout.addWidget(chat_label, 1, 0)
        grid_layout.addWidget(self.chat_model, 1, 1)
        
        # Modelo de embeddings (fila 2)
        embedding_label = QLabel("Modelo de embeddings:")
        embedding_label.setStyleSheet(label_style)
        embedding_label.setFixedWidth(160)
        
        self.embedding_model = QLineEdit(self.settings.embedding_model)
        self.embedding_model.setStyleSheet(input_style)
        
        grid_layout.addWidget(embedding_label, 2, 0)
        grid_layout.addWidget(self.embedding_model, 2, 1)
        
        # Modelo de transcripción (fila 3)
        transcription_label = QLabel("Modelo de transcripción:")
        transcription_label.setStyleSheet(label_style)
        transcription_label.setFixedWidth(160)
        
        self.transcription_model = QLineEdit(self.settings.transcription_model)
        self.transcription_model.setStyleSheet(input_style)
        
        grid_layout.addWidget(transcription_label, 3, 0)
        grid_layout.addWidget(self.transcription_model, 3, 1)
        
        # Top-K (fila 4)
        top_k_label = QLabel("Documentos búsqueda:")
        top_k_label.setStyleSheet(label_style)
        top_k_label.setFixedWidth(160)
        
        self.top_k = QSpinBox()
        self.top_k.setRange(1, 50)
        self.top_k.setValue(self.settings.top_k)
        self.top_k.setStyleSheet(f"""
            QSpinBox {{
                background-color: {AppleColors.SIDEBAR.name()};
                color: {AppleColors.PRIMARY.name()};
                border: 1px solid {AppleColors.SEPARATOR.name()};
                border-radius: 8px;
                padding: 12px 16px;
                font-family: '.AppleSystemUIFont';
                font-size: 14px;
                min-width: 100px;
            }}
            QSpinBox:focus {{
                border: 2px solid {AppleColors.BLUE.name()};
                padding: 11px 15px;
                background-color: {AppleColors.NOTES_LIST.name()};
            }}
            QSpinBox::up-button, QSpinBox::down-button {{
                background-color: transparent;
                border: none;
            }}
        """)
        
        grid_layout.addWidget(top_k_label, 4, 0)
        grid_layout.addWidget(self.top_k, 4, 1, Qt.AlignLeft)
        
        api_layout.addLayout(grid_layout)
        
        # Test button
        test_layout = QHBoxLayout()
        test_layout.addStretch()
        self.test_btn = AppleButton("Probar conexión", "secondary")
        self.test_btn.clicked.connect(self._test_connection)
        test_layout.addWidget(self.test_btn)
        api_layout.addLayout(test_layout)
        
        layout.addWidget(api_card)
        
        # Configuración de almacenamiento
        storage_card = AppleCard("Almacenamiento", "Configuración de datos locales")
        storage_layout = storage_card.layout()
        
        # Carpeta de datos - layout horizontal
        storage_grid = QGridLayout()
        storage_grid.setSpacing(16)
        storage_grid.setColumnStretch(1, 1)
        
        data_dir_label = QLabel("Carpeta de datos:")
        data_dir_label.setStyleSheet(label_style)
        data_dir_label.setFixedWidth(160)
        
        data_dir_container = QWidget()
        data_dir_layout = QHBoxLayout(data_dir_container)
        data_dir_layout.setContentsMargins(0, 0, 0, 0)
        data_dir_layout.setSpacing(8)
        
        self.data_dir = QLineEdit(self.settings.data_dir)
        self.data_dir.setStyleSheet(input_style)
        
        self.btn_browse = AppleButton("📁 Elegir", "ghost")
        self.btn_browse.clicked.connect(self.browse_dir)
        
        data_dir_layout.addWidget(self.data_dir, 1)
        data_dir_layout.addWidget(self.btn_browse)
        
        storage_grid.addWidget(data_dir_label, 0, 0)
        storage_grid.addWidget(data_dir_container, 0, 1)
        
        storage_layout.addLayout(storage_grid)
        layout.addWidget(storage_card)
        
        layout.addStretch()
        
        # Botones de acción
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(12)
        
        self.btn_reset = AppleButton("Restablecer", "secondary")
        self.btn_reset.clicked.connect(self._reset_settings)
        
        self.btn_save = AppleButton("Guardar configuración", "primary")
        self.btn_save.clicked.connect(self.save)
        
        actions_layout.addStretch()
        actions_layout.addWidget(self.btn_reset)
        actions_layout.addWidget(self.btn_save)
        layout.addLayout(actions_layout)
   
    def _toggle_api_key_visibility(self):
        if self.api_key.echoMode() == QLineEdit.Password:
            self.api_key.setEchoMode(QLineEdit.Normal)
            self.toggle_visibility.setText("🙈")
        else:
            self.api_key.setEchoMode(QLineEdit.Password)
            self.toggle_visibility.setText("👁")
    
    def _test_connection(self):
        api_key = self.api_key.text().strip()
        if not api_key or not api_key.startswith('sk-'):
            QMessageBox.warning(self, "API Key inválida", 
                              "Por favor ingresa una API Key válida de OpenAI")
            return
            
        self.test_btn.setText("Probando...")
        self.test_btn.setEnabled(False)
        QTimer.singleShot(100, lambda: self._do_connection_test(api_key))
    
    def _do_connection_test(self, api_key: str):
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            client.embeddings.create(input=["test"], model="text-embedding-3-small")
            QMessageBox.information(self, "Conexión exitosa", "La conexión con OpenAI fue exitosa.")
        except Exception as e:
            QMessageBox.warning(self, "Error de conexión", f"No se pudo conectar con OpenAI:\n{str(e)}")
        finally:
            self.test_btn.setText("Probar conexión")
            self.test_btn.setEnabled(True)

    def browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Selecciona carpeta de datos", self.data_dir.text())
        if d:
            self.data_dir.setText(d)

    def _reset_settings(self):
        reply = QMessageBox.question(self, "Restablecer configuración", 
                                   "¿Estás seguro de que quieres restablecer toda la configuración?",
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            from app.settings import DEFAULT_CONFIG
            self.api_key.clear()
            self.chat_model.setText(DEFAULT_CONFIG["chat_model"])
            self.embedding_model.setText(DEFAULT_CONFIG["embedding_model"])
            self.transcription_model.setText(DEFAULT_CONFIG["transcription_model"])
            self.top_k.setValue(DEFAULT_CONFIG["top_k"])
            self.data_dir.setText(DEFAULT_CONFIG["data_dir"])

    def save(self):
        api_key = self.api_key.text().strip()
        if api_key and not api_key.startswith('sk-'):
            QMessageBox.warning(self, "API Key inválida", "La API Key de OpenAI debe comenzar con 'sk-'")
            return
        
        try:
            self.settings.openai_api_key = api_key
            self.settings.chat_model = self.chat_model.text().strip()
            self.settings.embedding_model = self.embedding_model.text().strip()
            self.settings.transcription_model = self.transcription_model.text().strip()
            self.settings.top_k = self.top_k.value()
            
            data_dir = self.data_dir.text().strip()
            if data_dir:
                os.makedirs(data_dir, exist_ok=True)
                self.settings.data_dir = data_dir
            
            QMessageBox.information(self, "Configuración guardada", 
                                  "Los cambios se han guardado correctamente.\n"
                                  "Reinicia la aplicación para aplicar todos los cambios.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error al guardar configuración: {e}")
class CategoriesTab(QWidget):
    """Tab de administración de categorías estilo Apple"""
    
    # Señales para notificar cambios
    categories_changed = Signal()
    
    def __init__(self, settings: Settings, db: NotesDB, main_window):
        super().__init__()
        self.settings = settings
        self.db = db
        self.main_window = main_window
        self._setup_ui()
        self._refresh_categories()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(24)
        layout.setContentsMargins(40, 40, 40, 40)
        
        # Header
        header_layout = QVBoxLayout()
        title = QLabel("Administrar categorías")
        title.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.PRIMARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 28px;
                font-weight: 300;
            }}
        """)
        
        subtitle = QLabel("Organiza y gestiona las categorías de tus notas")
        subtitle.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.SECONDARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 16px;
                margin-top: 4px;
            }}
        """)
        
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        layout.addLayout(header_layout)
        
        # Panel de nueva categoría
        new_category_card = AppleCard("Nueva categoría", "Crear una nueva categoría para organizar tus notas")
        new_category_layout = new_category_card.layout()
        
        input_layout = QHBoxLayout()
        input_layout.setSpacing(12)
        
        self.new_category_input = AppleLineEdit("Nombre de la categoría...")
        self.new_category_input.returnPressed.connect(self._create_category)
        
        self.btn_create = AppleButton("Crear categoría", "primary")
        self.btn_create.clicked.connect(self._create_category)
        self.btn_create.setEnabled(False)
        
        # Conectar para habilitar/deshabilitar botón
        self.new_category_input.textChanged.connect(self._on_input_changed)
        
        input_layout.addWidget(self.new_category_input, 1)
        input_layout.addWidget(self.btn_create)
        new_category_layout.addLayout(input_layout)
        layout.addWidget(new_category_card)
        
        # Lista de categorías existentes
        categories_header = QHBoxLayout()
        categories_title = QLabel("Categorías existentes")
        categories_title.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.PRIMARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 20px;
                font-weight: 500;
            }}
        """)
        
        self.categories_count = QLabel("0 categorías")
        self.categories_count.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.SECONDARY.name()};
                font-size: 14px;
            }}
        """)
        
        categories_header.addWidget(categories_title)
        categories_header.addStretch()
        categories_header.addWidget(self.categories_count)
        layout.addLayout(categories_header)
        
        # Lista de categorías con estadísticas
        self.categories_list = QListWidget()
        self.categories_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.categories_list.customContextMenuRequested.connect(self._show_context_menu)
        self.categories_list.setStyleSheet(f"""
            QListWidget {{
                background-color: transparent;
                border: none;
                outline: none;
                font-family: '.AppleSystemUIFont';
                font-size: 14px;
            }}
            QListWidget::item {{
                background-color: {AppleColors.CARD.name()};
                color: {AppleColors.PRIMARY.name()};
                padding: 20px;
                margin-bottom: 8px;
                border-radius: 12px;
                border: 1px solid {AppleColors.SEPARATOR_LIGHT.name()};
            }}
            QListWidget::item:hover {{
                background-color: {AppleColors.ELEVATED.name()};
                border-color: {AppleColors.BLUE.name()};
            }}
            QListWidget::item:selected {{
                background-color: {AppleColors.BLUE.name()};
                color: white;
                border-color: {AppleColors.BLUE.name()};
            }}
        """)
        layout.addWidget(self.categories_list, 1)
        
        # Panel de acciones masivas
        actions_card = AppleCard("Acciones avanzadas")
        actions_layout = actions_card.layout()
        
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(12)
        
        self.btn_merge = AppleButton("Fusionar categorías", "secondary")
        self.btn_merge.clicked.connect(self._merge_categories)
        self.btn_merge.setEnabled(False)
        
        self.btn_export = AppleButton("Exportar listado", "ghost")
        self.btn_export.clicked.connect(self._export_categories)
        
        self.btn_cleanup = AppleButton("Limpiar vacías", "ghost")
        self.btn_cleanup.clicked.connect(self._cleanup_empty_categories)
        
        buttons_layout.addWidget(self.btn_merge)
        buttons_layout.addWidget(self.btn_export)
        buttons_layout.addWidget(self.btn_cleanup)
        buttons_layout.addStretch()
        actions_layout.addLayout(buttons_layout)
        layout.addWidget(actions_card)
    
    def _on_input_changed(self):
        """Habilita/deshabilita botón según el input"""
        text = self.new_category_input.text().strip()
        self.btn_create.setEnabled(bool(text))
    
    def _create_category(self):
        """Crea nueva categoría"""
        name = self.new_category_input.text().strip()
        if not name:
            return
        
        # Validar que no exista
        existing_categories = [cat.lower() for cat in self.db.list_categories()]
        if name.lower() in existing_categories:
            QMessageBox.warning(self, "Categoría existente", 
                              f"La categoría '{name}' ya existe.")
            return
        
        # Validar longitud
        if len(name) > 50:
            QMessageBox.warning(self, "Nombre muy largo", 
                              "El nombre de la categoría no puede exceder 50 caracteres.")
            return
        
        # Validar caracteres válidos
        if not all(c.isalnum() or c.isspace() or c in '-_()' for c in name):
            QMessageBox.warning(self, "Caracteres inválidos", 
                              "El nombre solo puede contener letras, números, espacios, guiones y paréntesis.")
            return
        
        try:
            self.db.add_category(name)
            self.new_category_input.clear()
            self._refresh_categories()
            self._notify_categories_changed()
            
            QMessageBox.information(self, "Categoría creada", 
                                  f"La categoría '{name}' se creó correctamente.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo crear la categoría: {e}")
    
    def _refresh_categories(self):
        """Actualiza la lista de categorías con estadísticas"""
        self.categories_list.clear()
        
        try:
            categories = self.db.list_categories()
            all_notes = self.db.list_notes(limit=10000)
            
            # Calcular estadísticas por categoría
            category_stats = {}
            for note in all_notes:
                cat = note.category
                if cat not in category_stats:
                    category_stats[cat] = {
                        'total': 0,
                        'recent': 0,
                        'transcripts': 0
                    }
                category_stats[cat]['total'] += 1
                
                # Notas recientes (última semana)
                try:
                    note_date = datetime.fromisoformat(note.created_at.replace('Z', '+00:00'))
                    week_ago = datetime.now(note_date.tzinfo if note_date.tzinfo else None) - timedelta(days=7)
                    if note_date > week_ago:
                        category_stats[cat]['recent'] += 1
                except:
                    pass
                
                # Transcripciones
                if note.source == 'transcript':
                    category_stats[cat]['transcripts'] += 1
            
            # Ordenar por número de notas (descendente)
            sorted_categories = sorted(categories, 
                                     key=lambda x: category_stats.get(x, {}).get('total', 0), 
                                     reverse=True)
            
            for category in sorted_categories:
                stats = category_stats.get(category, {'total': 0, 'recent': 0, 'transcripts': 0})
                
                # Icono según el tipo de categoría
                if category.lower() in ['transcripciones', 'transcripción', 'audio']:
                    icon = "🎤"
                elif category.lower() in ['personal', 'privado']:
                    icon = "👤"
                elif category.lower() in ['trabajo', 'work', 'laboral']:
                    icon = "💼"
                elif category.lower() in ['ideas', 'proyectos']:
                    icon = "💡"
                else:
                    icon = "📁"
                
                # Crear texto con estadísticas
                main_text = f"{icon} {category}"
                stats_text = f"{stats['total']} notas"
                
                details = []
                if stats['recent'] > 0:
                    details.append(f"{stats['recent']} esta semana")
                if stats['transcripts'] > 0:
                    details.append(f"{stats['transcripts']} transcripciones")
                
                if details:
                    stats_text += f" • {' • '.join(details)}"
                
                full_text = f"{main_text}\n{stats_text}"
                
                item = QListWidgetItem(full_text)
                item.setData(Qt.UserRole, {
                    'name': category,
                    'stats': stats
                })
                self.categories_list.addItem(item)
            
            self.categories_count.setText(f"{len(categories)} categorías")
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error cargando categorías: {e}")
    
    def _show_context_menu(self, pos: QPoint):
        """Muestra menú contextual para categorías"""
        item = self.categories_list.itemAt(pos)
        if not item:
            return
        
        data = item.data(Qt.UserRole)
        if not data:
            return
        
        category_name = data['name']
        stats = data['stats']
        
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {AppleColors.ELEVATED.name()};
                border: 1px solid {AppleColors.SEPARATOR.name()};
                border-radius: 8px;
                padding: 4px 0;
                font-size: 14px;
                color: {AppleColors.PRIMARY.name()};
            }}
            QMenu::item {{
                padding: 8px 16px;
            }}
            QMenu::item:selected {{
                background-color: {AppleColors.BLUE.name()};
                color: white;
            }}
        """)
        
        # Acciones disponibles
        view_action = menu.addAction(f"Ver notas ({stats['total']})")
        rename_action = menu.addAction("Renombrar")
        menu.addSeparator()
        
        if stats['total'] == 0:
            delete_action = menu.addAction("Eliminar categoría vacía")
        else:
            delete_action = menu.addAction(f"Eliminar y reasignar {stats['total']} notas")
        
        action = menu.exec(self.categories_list.mapToGlobal(pos))
        
        if action == view_action:
            self._view_category_notes(category_name)
        elif action == rename_action:
            self._rename_category(category_name)
        elif action == delete_action:
            self._delete_category(category_name, stats['total'])
    
    def _view_category_notes(self, category_name: str):
        """Navega a las notas de una categoría específica"""
        if hasattr(self.main_window, 'stack') and hasattr(self.main_window, 'notes_view'):
            # Ir al tab de notas
            self.main_window.stack.setCurrentIndex(1)
            
            # Aplicar filtro de categoría
            QTimer.singleShot(200, lambda: self._apply_category_filter(category_name))
    
    def _apply_category_filter(self, category_name: str):
        """Aplica filtro de categoría en la vista de notas"""
        if hasattr(self.main_window, 'notes_view'):
            notes_view = self.main_window.notes_view
            if hasattr(notes_view, '_filter_notes'):
                # Aplicar filtro específico
                filters = {'category': category_name}
                notes_view._filter_notes("", filters)
    
    def _rename_category(self, old_name: str):
        """Renombra una categoría"""
        new_name, ok = QInputDialog.getText(
            self, "Renombrar categoría",
            f"Nuevo nombre para '{old_name}':",
            text=old_name
        )
        
        if not ok or not new_name.strip():
            return
        
        new_name = new_name.strip()
        
        # Validaciones
        if new_name == old_name:
            return
        
        existing_categories = [cat.lower() for cat in self.db.list_categories()]
        if new_name.lower() in existing_categories:
            QMessageBox.warning(self, "Categoría existente", 
                              f"La categoría '{new_name}' ya existe.")
            return
        
        if len(new_name) > 50:
            QMessageBox.warning(self, "Nombre muy largo", 
                              "El nombre no puede exceder 50 caracteres.")
            return
        
        # Confirmar cambio
        reply = QMessageBox.question(
            self, "Confirmar cambio",
            f"¿Renombrar '{old_name}' a '{new_name}'?\n\n"
            "Esto actualizará todas las notas que usen esta categoría.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        try:
            # Actualizar todas las notas con la categoría antigua
            self.db.rename_category(old_name, new_name)
            self._refresh_categories()
            self._notify_categories_changed()
            
            QMessageBox.information(self, "Categoría renombrada", 
                                  f"La categoría se renombró correctamente.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error al renombrar: {e}")
    
    def _delete_category(self, category_name: str, note_count: int):
        """Elimina una categoría"""
        if note_count > 0:
            # Obtener otras categorías para reasignar
            other_categories = [cat for cat in self.db.list_categories() if cat != category_name]
            
            if not other_categories:
                QMessageBox.warning(self, "No se puede eliminar", 
                                  "No puedes eliminar la única categoría existente.")
                return
            
            # Seleccionar categoría de destino
            target_category, ok = QInputDialog.getItem(
                self, "Reasignar notas",
                f"¿A qué categoría reasignar las {note_count} notas de '{category_name}'?",
                other_categories, 0, False
            )
            
            if not ok:
                return
            
            # Confirmar eliminación
            reply = QMessageBox.question(
                self, "Confirmar eliminación",
                f"¿Eliminar la categoría '{category_name}'?\n\n"
                f"Las {note_count} notas se moverán a '{target_category}'.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply != QMessageBox.Yes:
                return
            
            try:
                # Reasignar notas y eliminar categoría
                self.db.delete_category_and_reassign(category_name, target_category)
                self._refresh_categories()
                self._notify_categories_changed()
                
                QMessageBox.information(self, "Categoría eliminada", 
                                      f"La categoría se eliminó y las notas se movieron a '{target_category}'.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Error al eliminar: {e}")
        else:
            # Categoría vacía - eliminación directa
            reply = QMessageBox.question(
                self, "Eliminar categoría vacía",
                f"¿Eliminar la categoría vacía '{category_name}'?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                try:
                    self.db.delete_category(category_name)
                    self._refresh_categories()
                    self._notify_categories_changed()
                    
                    QMessageBox.information(self, "Categoría eliminada", 
                                          "La categoría vacía se eliminó correctamente.")
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Error al eliminar: {e}")
    
    def _merge_categories(self):
        """Fusiona múltiples categorías en una"""
        categories = self.db.list_categories()
        if len(categories) < 2:
            QMessageBox.information(self, "Fusión no disponible", 
                                  "Necesitas al menos 2 categorías para fusionar.")
            return
        
        # Diálogo personalizado para selección múltiple
        dialog = QDialog(self)
        dialog.setWindowTitle("Fusionar categorías")
        dialog.setFixedSize(400, 500)
        
        layout = QVBoxLayout(dialog)
        
        layout.addWidget(QLabel("Selecciona las categorías a fusionar:"))
        
        # Lista de checkboxes
        scroll = QScrollArea()
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        
        checkboxes = {}
        for category in categories:
            cb = QCheckBox(category)
            checkboxes[category] = cb
            scroll_layout.addWidget(cb)
        
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)
        
        layout.addWidget(QLabel("Nombre de la categoría resultante:"))
        target_input = QLineEdit()
        layout.addWidget(target_input)
        
        # Botones
        buttons_layout = QHBoxLayout()
        cancel_btn = QPushButton("Cancelar")
        merge_btn = QPushButton("Fusionar")
        cancel_btn.clicked.connect(dialog.reject)
        merge_btn.clicked.connect(dialog.accept)
        buttons_layout.addWidget(cancel_btn)
        buttons_layout.addWidget(merge_btn)
        layout.addLayout(buttons_layout)
        
        if dialog.exec() != QDialog.Accepted:
            return
        
        # Procesar fusión
        selected_categories = [cat for cat, cb in checkboxes.items() if cb.isChecked()]
        target_name = target_input.text().strip()
        
        if len(selected_categories) < 2:
            QMessageBox.warning(self, "Selección insuficiente", 
                              "Selecciona al menos 2 categorías para fusionar.")
            return
        
        if not target_name:
            QMessageBox.warning(self, "Nombre requerido", 
                              "Especifica el nombre de la categoría resultante.")
            return
        
        try:
            self.db.merge_categories(selected_categories, target_name)
            self._refresh_categories()
            self._notify_categories_changed()
            
            QMessageBox.information(self, "Fusión completada", 
                                  f"Las categorías se fusionaron en '{target_name}'.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error en fusión: {e}")
    
    def _export_categories(self):
        """Exporta listado de categorías con estadísticas"""
        try:
            file_path, _ = QFileDialog.getSaveFileName(
                self, "Exportar categorías", 
                f"categorias_{datetime.now().strftime('%Y%m%d')}.txt",
                "Archivos de texto (*.txt);;CSV (*.csv)"
            )
            
            if not file_path:
                return
            
            # Generar contenido
            categories = self.db.list_categories()
            all_notes = self.db.list_notes(limit=10000)
            
            content = f"Listado de Categorías - {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
            content += "=" * 60 + "\n\n"
            
            category_stats = {}
            for note in all_notes:
                cat = note.category
                if cat not in category_stats:
                    category_stats[cat] = {'total': 0, 'transcripts': 0}
                category_stats[cat]['total'] += 1
                if note.source == 'transcript':
                    category_stats[cat]['transcripts'] += 1
            
            for category in sorted(categories):
                stats = category_stats.get(category, {'total': 0, 'transcripts': 0})
                content += f"📁 {category}\n"
                content += f"   Total de notas: {stats['total']}\n"
                content += f"   Transcripciones: {stats['transcripts']}\n\n"
            
            content += f"\nResumen:\n"
            content += f"Total de categorías: {len(categories)}\n"
            content += f"Total de notas: {sum(stats['total'] for stats in category_stats.values())}\n"
            
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            QMessageBox.information(self, "Exportación completa", 
                                  f"Listado exportado a {file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error al exportar: {e}")
    
    def _cleanup_empty_categories(self):
        """Elimina categorías sin notas"""
        categories = self.db.list_categories()
        all_notes = self.db.list_notes(limit=10000)
        used_categories = set(note.category for note in all_notes)
        
        empty_categories = [cat for cat in categories if cat not in used_categories]
        
        if not empty_categories:
            QMessageBox.information(self, "Limpieza completa", 
                                  "No hay categorías vacías para eliminar.")
            return
        
        reply = QMessageBox.question(
            self, "Limpiar categorías vacías",
            f"¿Eliminar {len(empty_categories)} categorías vacías?\n\n" +
            "\n".join(f"• {cat}" for cat in empty_categories[:5]) +
            ("..." if len(empty_categories) > 5 else ""),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            try:
                for category in empty_categories:
                    self.db.delete_category(category)
                
                self._refresh_categories()
                self._notify_categories_changed()
                
                QMessageBox.information(self, "Limpieza completa", 
                                      f"Se eliminaron {len(empty_categories)} categorías vacías.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Error en limpieza: {e}")
    
    def _notify_categories_changed(self):
        """Notifica cambios a otras vistas"""
        # Actualizar vista de notas
        if hasattr(self.main_window, 'notes_view'):
            notes_view = self.main_window.notes_view
            if hasattr(notes_view, 'note_editor') and hasattr(notes_view.note_editor, 'refresh_categories'):
                notes_view.note_editor.refresh_categories()
        
        # Actualizar vista de transcripción
        if hasattr(self.main_window, 'transcribe_tab'):
            transcribe_tab = self.main_window.transcribe_tab
            if hasattr(transcribe_tab, 'category_combo'):
                current_text = transcribe_tab.category_combo.currentText()
                transcribe_tab.category_combo.clear()
                categories = self.db.list_categories()
                for cat in categories:
                    transcribe_tab.category_combo.addItem(cat)
                # Restaurar selección si existe
                index = transcribe_tab.category_combo.findText(current_text)
                if index >= 0:
                    transcribe_tab.category_combo.setCurrentIndex(index)
class DashboardTab(QWidget):
    """Dashboard con estadísticas estilo Apple - SIMPLIFICADO con indicadores en línea"""
    
    def __init__(self, settings: Settings, db: NotesDB, main_window):
        super().__init__()
        self.settings = settings
        self.db = db
        self.main_window = main_window
        self._setup_ui()
        self._refresh_stats()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(24)
        layout.setContentsMargins(40, 40, 40, 40)
        
        # Header
        header_layout = QVBoxLayout()
        title = QLabel("Dashboard")
        title.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.PRIMARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 28px;
                font-weight: 300;
            }}
        """)
        
        subtitle = QLabel(f"Bienvenido • {datetime.now().strftime('%d de %B')}")
        subtitle.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.SECONDARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 14px;
                margin-top: 4px;
            }}
        """)
        
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        layout.addLayout(header_layout)
        
        # Indicadores en línea
        self.stats_label = QLabel("Notas: 0 | Categorías: 0 | Esta semana: 0 | Transcripciones: 0")
        self.stats_label.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.SECONDARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 14px;
                font-weight: 500;
                background-color: {AppleColors.CARD.name()};
                padding: 12px 20px;
                border-radius: 8px;
                border: 1px solid {AppleColors.SEPARATOR_LIGHT.name()};
            }}
        """)
        self.stats_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.stats_label)
        
        # Header de notas con acciones
        notes_header = QHBoxLayout()
        recent_label = QLabel("Notas recientes")
        recent_label.setStyleSheet(f"""
            QLabel {{
                color: {AppleColors.PRIMARY.name()};
                font-family: '.AppleSystemUIFont';
                font-size: 20px;
                font-weight: 500;
            }}
        """)
        
        new_note_action = AppleButton("+ Nueva nota", "primary")
        new_note_action.clicked.connect(lambda: self._switch_tab(1))
        
        notes_header.addWidget(recent_label)
        notes_header.addStretch()
        notes_header.addWidget(new_note_action)
        layout.addLayout(notes_header)
        
        # Lista de notas
        self.recent_notes_list = QListWidget()
        self.recent_notes_list.setItemDelegate(NotesListDelegate())
        self.recent_notes_list.setStyleSheet(f"""
            QListWidget {{
                background-color: transparent;
                border: none;
                outline: none;
                font-family: '.AppleSystemUIFont';
                font-size: 14px;
            }}
        """)
        self.recent_notes_list.itemDoubleClicked.connect(self._open_note_from_list)
        layout.addWidget(self.recent_notes_list, 1)
        
        QTimer.singleShot(100, self._refresh_stats)
        QTimer.singleShot(1000, self._refresh_stats)

    def _switch_tab(self, index: int):
        if hasattr(self.main_window, "stack"):
            self.main_window.stack.setCurrentIndex(index)
            
            # Si es nueva nota, crear una automáticamente
            if index == 1 and hasattr(self.main_window, 'notes_view'):
                # Pequeño delay para asegurar que la UI se actualice
                QTimer.singleShot(100, self.main_window.notes_view.new_note)

    def _open_note_from_list(self, item):
        """Abre nota seleccionada desde la lista reciente"""
        data = item.data(Qt.UserRole)
        if data and hasattr(self.main_window, 'notes_view'):
            note_id = data.get('id')  # Extraer el ID del diccionario
            if note_id:
                self._switch_tab(1)  # Ir a notas
                # Cargar la nota específica
                QTimer.singleShot(200, lambda: self.main_window.notes_view.note_editor.load_note(note_id))

    def _refresh_stats(self):
        """Actualiza estadísticas del dashboard"""
        try:
            # Obtener datos base con tolerancia a None
            all_notes = self.db.list_notes(limit=10000) or []
            total_notes = len(all_notes)

            try:
                total_categories = len(self.db.list_categories() or [])
            except Exception as e:
                total_categories = 0

            # Ventana de 7 días con timezone-aware
            week_ago = datetime.now(timezone.utc) - timedelta(days=7)
            recent_notes = 0
            transcripts = 0

            for note in all_notes:
                # Conteo de recientes (created_at)
                try:
                    created_raw = getattr(note, "created_at", None) or ""
                    if created_raw:
                        # Normalizar 'Z' -> '+00:00'
                        note_date = datetime.fromisoformat(created_raw.replace('Z', '+00:00'))
                        if note_date.tzinfo is None:
                            note_date = note_date.replace(tzinfo=timezone.utc)
                        if note_date > week_ago:
                            recent_notes += 1
                except Exception:
                    # No interrumpir por un error de parsing individual
                    pass

                # Conteo de transcripciones
                if (getattr(note, "source", "") or "").lower() == "transcript":
                    transcripts += 1

            # Actualizar la línea de estadísticas
            stats_text = f"Notas: {total_notes} | Categorías: {total_categories} | Esta semana: {recent_notes} | Transcripciones: {transcripts}"
            self.stats_label.setText(stats_text)

            # Procesar eventos para forzar redraw inmediato
            QApplication.processEvents()

            # --- Cargar notas recientes (ordenadas por updated_at desc) ---
            def parse_dt(value: str) -> datetime:
                if not value:
                    return datetime.min.replace(tzinfo=timezone.utc)
                try:
                    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
                    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                except Exception:
                    return datetime.min.replace(tzinfo=timezone.utc)

            sorted_notes = sorted(
                all_notes,
                key=lambda n: parse_dt(getattr(n, "updated_at", None)),
                reverse=True
            )

            self.recent_notes_list.clear()

            # En el loop donde se crean los items de la lista (línea donde se construye el texto)
            for note in sorted_notes[:50]:
                icon = "🎤" if (getattr(note, "source", "") or "").lower() == "transcript" else "📄"
                title = getattr(note, "title", "") or "(Sin título)"
                category = getattr(note, "category", "") or "Sin categoría"
                updated_raw = getattr(note, "updated_at", "") or ""
                updated_show = format_date_chile(updated_raw) if updated_raw else ""
                content = getattr(note, "content", "") or ""
                preview = (content[:150] + "...") if len(content) > 150 else content

                # Usar el mismo formato que NotesListDelegate espera
                item = QListWidgetItem()
                item.setData(Qt.UserRole, {
                    'id': getattr(note, "id", None),
                    'title': title,  # El delegate aplicará .upper() automáticamente
                    'preview': preview,
                    'date': updated_show,
                    'has_audio': False,
                    'is_transcript': (getattr(note, "source", "") or "").lower() == "transcript"
                })
                item.setToolTip(content[:500])
                self.recent_notes_list.addItem(item)

        except Exception as e:
            self.stats_label.setText("Error cargando estadísticas")
class SideNav(QWidget):
    """Navegación lateral estilo Apple - SIMPLIFICADA"""
    
    def __init__(self, on_change_index: callable):
        super().__init__()
        self.on_change_index = on_change_index
        self.setFixedWidth(220)
        self._setup_ui()
        
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
                
        # Lista de navegación
        self.list = QListWidget()
        self.list.setStyleSheet(f"""
            QListWidget {{
                background-color: transparent;
                border: none;
                outline: none;
                font-family: '.AppleSystemUIFont';
                font-size: 14px;
            }}
            QListWidget::item {{
                color: {AppleColors.PRIMARY.name()};
                padding: 10px 16px;
                border-radius: 8px;
                margin: 2px 0px;
            }}
            QListWidget::item:hover {{
                background-color: {AppleColors.CARD.name()};
            }}
            QListWidget::item:selected {{
                background-color: {AppleColors.BLUE.name()};
                color: white;
            }}
        """)
        
        # En la clase SideNav, actualizar el método _setup_ui:
        items = [
            ("📊  Dashboard", 0),
            ("📄  Notas", 1),  
            ("🎤  Transcribir", 2),
            ("🔍  Buscar", 3),
            ("🧠  Analizar", 4),
            ("📝  Resumen IA", 5),      # NUEVA LÍNEA
            ("🏷️  Categorías", 6),     # Cambiar índice de 5 a 6
            ("⚙️  Ajustes", 7),        # Cambiar índice de 6 a 7
        ]
        
        for text, idx in items:
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, idx)
            self.list.addItem(item)
        
        self.list.currentRowChanged.connect(self._on_change)
        layout.addWidget(self.list)
        layout.addStretch()  # Solo el stretch al final
        
        # Seleccionar primer item por defecto
        self.list.setCurrentRow(0)
    
    def _on_change(self, row: int):
        if callable(self.on_change_index):
            self.on_change_index(row)
    
    # ELIMINAR método update_connection_status completamente
class MainWindow(QMainWindow):
    """Ventana principal con diseño Apple - MEJORADA"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1400, 900)
        self.setMinimumSize(1000, 700)
        
        self.settings = Settings()
        self.app_state = self._determine_app_state()
        
        self._setup_style()
        
        if self.app_state == AppState.FIRST_RUN:
            self._show_welcome()
        elif self.app_state == AppState.SETUP:
            self._show_setup()
        else:
            self._setup_main_app()
    
    def _determine_app_state(self) -> AppState:
        if not os.path.exists(self.settings.config_path):
            return AppState.FIRST_RUN
        if not self.settings.openai_api_key and not os.environ.get("OPENAI_API_KEY"):
            return AppState.SETUP
        return AppState.READY
    
    def _setup_style(self):
        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: {AppleColors.CONTENT.name()};
            }}
        """)
    
    def _show_welcome(self):
        self.welcome_screen = WelcomeScreen(self._show_setup)
        self.setCentralWidget(self.welcome_screen)
    
    def _show_setup(self):
        self.setup_screen = SetupScreen(self.settings, self._setup_main_app)
        self.setCentralWidget(self.setup_screen)
    
    def _setup_main_app(self):
        self._setup_services()
        
        # Widget central con tabs
        self.stack = QStackedWidget()
        
        # Crear todos los tabs con las versiones mejoradas
        self.dashboard_tab = DashboardTab(self.settings, self.db, self)
        self.notes_view = EnhancedNotesView(self.settings, self.db, self.vector, self.ai)
        self.transcribe_tab = EnhancedTranscribeTab(self.settings, self.db, self.vector, self.ai)
        self.search_tab = SearchTab(self.settings, self.db, self.vector, self.ai)
        self.analyze_tab = AnalyzeTab(self.settings, self.db, self.vector, self.ai)
        self.summary_tab = SummaryTab(self.settings, self.db, self.ai)  # NUEVA LÍNEA
        self.categories_tab = CategoriesTab(self.settings, self.db, self)
        self.settings_tab = SettingsTab(self.settings)
        
        # Agregar al stack
        for widget in [self.dashboard_tab, self.notes_view, self.transcribe_tab, 
                    self.search_tab, self.analyze_tab, self.summary_tab,     # AGREGAR AQUÍ
                    self.categories_tab, self.settings_tab]:
            self.stack.addWidget(widget)
        
        # Layout principal
        main_widget = QWidget()
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Sidebar de navegación
        self.nav = SideNav(self._on_nav_changed)
        self.nav.setStyleSheet(f"""
            QWidget {{
                background-color: {AppleColors.SIDEBAR.name()};
                border-right: 1px solid {AppleColors.SEPARATOR.name()};
            }}
        """)
        main_layout.addWidget(self.nav)
        
        # Stack de contenido
        main_layout.addWidget(self.stack, 1)
        
        self.setCentralWidget(main_widget)
        self.stack.currentChanged.connect(self._on_view_changed)
        
    
    def _setup_services(self):
        db_path = os.path.join(self.settings.data_dir, "notes.db")
        self.db = NotesDB(db_path)
        self.ai = AIService(self.settings)
        
        self.vector = None
        try:
            if self.settings.openai_api_key or os.environ.get("OPENAI_API_KEY"):
                self.vector = VectorIndex(self.settings, self.ai)
        except Exception as e:
            print(f"Vector store deshabilitado: {e}")
    

    
    def _on_nav_changed(self, index: int):
        self.stack.setCurrentIndex(index)
    def _cleanup(self):
        """Limpia recursos antes de cerrar"""
        try:
            # Detener transcripción si está activa
            if hasattr(self, 'transcribe_tab') and hasattr(self.transcribe_tab, 'is_transcribing'):
                if self.transcribe_tab.is_transcribing:
                    self.transcribe_tab.stop_transcription()
            
            # Detener audio del resumen si está reproduciéndose
            if hasattr(self, 'summary_tab') and hasattr(self.summary_tab, 'audio_playing'):
                if self.summary_tab.audio_playing:
                    self.summary_tab._stop_audio()
            
            print("Recursos limpiados correctamente")
        except Exception as e:
            print(f"Error en limpieza: {e}")

    def closeEvent(self, event):
        """Maneja el cierre de la ventana"""
        self._cleanup()
        event.accept()
    def _on_view_changed(self, index: int):
        # Actualizar dashboard cuando se selecciona
        if index == 0 and hasattr(self, "dashboard_tab"):
            self.dashboard_tab._refresh_stats()
        
        # Actualizar búsqueda cuando se selecciona
        elif index == 3 and hasattr(self, "search_tab"):
            # Limpiar resultados previos
            if hasattr(self.search_tab, 'results'):
                self.search_tab.results.clear()
                self.search_tab.results_count.setText("0 resultados")

def main():
    app = QApplication(sys.argv)
    
    # HiDPI support
    QCoreApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    
    # Configurar fuente del sistema
    if sys.platform == "darwin":
        font = QFont(".AppleSystemUIFont", 13)
    else:
        font = QFont("Segoe UI", 11)
    app.setFont(font)
    
    # Paleta de colores Apple
    palette = QPalette()
    palette.setColor(QPalette.Window, AppleColors.CONTENT)
    palette.setColor(QPalette.WindowText, AppleColors.PRIMARY)
    palette.setColor(QPalette.Base, AppleColors.NOTES_LIST)
    palette.setColor(QPalette.AlternateBase, AppleColors.CARD)
    palette.setColor(QPalette.ToolTipBase, AppleColors.ELEVATED)
    palette.setColor(QPalette.ToolTipText, AppleColors.PRIMARY)
    palette.setColor(QPalette.Text, AppleColors.PRIMARY)
    palette.setColor(QPalette.Button, AppleColors.CARD)
    palette.setColor(QPalette.ButtonText, AppleColors.PRIMARY)
    palette.setColor(QPalette.Highlight, AppleColors.BLUE)
    palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    palette.setColor(QPalette.Link, AppleColors.BLUE)
    
    # Disabled states
    palette.setColor(QPalette.Disabled, QPalette.Text, AppleColors.TERTIARY)
    palette.setColor(QPalette.Disabled, QPalette.WindowText, AppleColors.TERTIARY)
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, AppleColors.TERTIARY)
    palette.setColor(QPalette.Disabled, QPalette.Highlight, AppleColors.SEPARATOR)
    palette.setColor(QPalette.Disabled, QPalette.HighlightedText, AppleColors.PRIMARY)
    
    app.setPalette(palette)
    
    # Tooltips estilo Apple
    app.setStyleSheet(f"""
        QToolTip {{
            background-color: {AppleColors.ELEVATED.name()};
            color: {AppleColors.PRIMARY.name()};
            border: 1px solid {AppleColors.SEPARATOR.name()};
            padding: 8px 12px;
            border-radius: 8px;
            font-family: '.AppleSystemUIFont';
            font-size: 12px;
        }}
    """)
    
    try:
        window = MainWindow()
        window.show()
        
        # Manejo seguro de señales del sistema
        import signal
        def signal_handler(signum, frame):
            print("\nCerrando aplicación de forma segura...")
            if hasattr(window, '_cleanup'):
                window._cleanup()
            app.quit()
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        sys.exit(app.exec())
    except KeyboardInterrupt:
        print("\nInterrupción detectada. Cerrando aplicación...")
        sys.exit(0)
    except Exception as e:
        QMessageBox.critical(None, "Error crítico", f"No se pudo iniciar la aplicación:\n{e}")
        sys.exit(1)

if __name__ == "__main__":
    main()