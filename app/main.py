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
# Agregar después de los imports existentes (línea ~40)


class AnalysisWorker(QThread):
    """Worker thread para análisis RAG sin bloquear UI"""
    
    # Señales para comunicación thread-safe
    analysis_finished = Signal(str)  # respuesta final
    analysis_error = Signal(str)     # error
    analysis_progress = Signal(str)  # actualizaciones de progreso
    analysis_streaming = Signal(str)  # NUEVO: para streaming de texto
    
    def __init__(self, db: 'NotesDB', vector: 'VectorIndex', ai: 'AIService', 
                 question: str, k_value: int):
        super().__init__()
        self.db = db
        self.vector = vector
        self.ai = ai
        self.question = question
        self.k_value = k_value
        
    def run(self):
        """Ejecuta análisis en hilo separado con streaming - SOLO VECTORIAL"""
        try:
            self.analysis_progress.emit("Verificando índice vectorial...")
            
            # VERIFICACIÓN PURAMENTE VECTORIAL
            total_chunks = self.vector.col.count()
            if total_chunks == 0:
                self.analysis_progress.emit("Reindexando notas existentes...")
                self._force_reindex()
                total_chunks = self.vector.col.count()
            
            if total_chunks == 0:
                self.analysis_finished.emit("No hay notas indexadas. Crea y guarda algunas notas primero.")
                return
            
            self.analysis_progress.emit("Buscando documentos relevantes...")
            retrieved = self.vector.search_optimized(self.question, top_k=self.k_value)
            
            if not retrieved:
                self.analysis_finished.emit("No se encontraron notas relevantes para tu pregunta.")
                return
            
            self.analysis_progress.emit(f"Analizando {len(retrieved)} documentos...")
            
            # CONSTRUIR CONTEXTOS SOLO DESDE VECTORIAL
            contexts = self._build_contexts_from_vectorial(retrieved)
            
            if not contexts:
                self.analysis_finished.emit("No se pudieron procesar las notas relevantes.")
                return
            
            self.analysis_progress.emit("Generando análisis con IA...")
            self._generate_streaming_response(contexts)
            
        except Exception as e:
            self.analysis_error.emit(f"Error en el análisis: {str(e)}")

    def _build_contexts_from_vectorial(self, retrieved: list) -> list:
        """Construye contextos EXCLUSIVAMENTE desde datos vectoriales"""
        try:
            contexts = []
            seen_notes = set()
            
            for result in retrieved[:self.k_value]:  # Limitar desde el inicio
                note_id = result.get("note_id")
                if note_id in seen_notes:
                    continue
                seen_notes.add(note_id)
                
                title = result.get("title", "Sin título")
                snippet = result.get("snippet", "")
                
                # Limpiar snippet si tiene prefijo
                content = snippet
                if content.startswith("Título:"):
                    lines = content.split("\n", 2)
                    if len(lines) >= 3:
                        content = lines[2]
                
                contexts.append({
                    "title": title,
                    "content": content[:2000]  # Limitar longitud
                })
            
            return contexts
            
        except Exception as e:
            print(f"Error construyendo contextos vectoriales: {e}")
            return []  # FALTABA RETURN

    def _force_reindex(self):
        """Fuerza reindexación de todas las notas desde SQLite hacia vectorial"""
        try:
            print("🔄 Iniciando reindexación forzada...")
            all_notes = self.db.list_notes(limit=1000)
            
            for i, note in enumerate(all_notes):
                try:
                    self.vector.index_note(
                        note.id, note.title, note.content, 
                        note.category, note.tags, note.source
                    )
                    if i % 10 == 0:  # Log cada 10 notas
                        print(f"✅ Reindexadas {i+1}/{len(all_notes)} notas")
                except Exception as e:
                    print(f"❌ Error reindexando nota {note.id}: {e}")
                    
            print(f"✅ Reindexación completa: {len(all_notes)} notas procesadas")
            
        except Exception as e:
            print(f"❌ Error en reindexación forzada: {e}")

    def _generate_streaming_response(self, contexts: list):
        """Genera respuesta con streaming"""
        try:
            # Usar el método de streaming de AIService
            full_response = ""
            
            for chunk in self.ai.answer_with_context_streaming(
                self.question, 
                contexts, 
                extended_analysis=True,
                max_tokens=2000
            ):
                if chunk:
                    full_response += chunk
                    self.analysis_streaming.emit(chunk)  # Emitir cada chunk
            
            # Formatear respuesta final con fuentes
            final_response = f"{full_response}\n\n"
            final_response += "─" * 50 + "\n"
            final_response += "📚 Fuentes consultadas:\n\n"
            for i, context in enumerate(contexts, 1):
                final_response += f"{i}. {context['title']}\n"
            
            self.analysis_finished.emit(final_response)
            
        except Exception as e:
            self.analysis_error.emit(f"Error generando respuesta: {str(e)}")
class SummaryWorker(QThread):
    """Worker thread para resumen con streaming"""
    
    # Señales para comunicación thread-safe
    summary_finished = Signal(str)  # resumen final
    summary_error = Signal(str)     # error
    summary_progress = Signal(str)  # actualizaciones de progreso
    summary_streaming = Signal(str)  # streaming de texto
    
    def __init__(self, vector: 'VectorIndex', ai: 'AIService'):
        super().__init__()
        self.vector = vector
        self.ai = ai
        
    def run(self):
        """Ejecuta generación de resumen con streaming"""
        try:
            from datetime import datetime, timedelta
            import pytz
            
            chile_tz = pytz.timezone('America/Santiago')
            now = datetime.now(chile_tz)
            three_days_ago = now - timedelta(days=3)
            
            self.summary_progress.emit("Consultando base vectorial...")
            
            # OBTENER TODOS LOS CHUNKS
            try:
                all_data = self.vector.col.get(include=["metadatas", "documents"])
            except Exception as e:
                self.summary_error.emit(f"Error accediendo a base vectorial: {e}")
                return
            
            if not all_data or not all_data.get("metadatas"):
                self.summary_error.emit("No hay datos en la base vectorial.")
                return
            
            self.summary_progress.emit("Procesando chunks de notas...")
            
            # AGRUPAR POR NOTE_ID Y FILTRAR POR FECHA
            notes_data = {}
            for doc, meta in zip(all_data["documents"], all_data["metadatas"]):
                note_id = meta["note_id"]
                title = meta["title"]
                created_at = meta.get("created_at", "")
                
                # FILTRAR POR FECHA
                try:
                    if created_at:
                        note_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                        if note_date.tzinfo is None:
                            note_date = note_date.replace(tzinfo=pytz.UTC)
                        note_date_chile = note_date.astimezone(chile_tz)
                        
                        if note_date_chile < three_days_ago:
                            continue
                except Exception:
                    pass
                
                # AGRUPAR CONTENIDO
                if note_id not in notes_data:
                    notes_data[note_id] = {
                        "title": title,
                        "chunks": [],
                        "created_at": created_at
                    }
                
                chunk_content = doc
                if chunk_content.startswith("Título:"):
                    lines = chunk_content.split("\n", 2)
                    if len(lines) >= 3:
                        chunk_content = lines[2]
                
                notes_data[note_id]["chunks"].append({
                    "content": chunk_content,
                    "chunk_type": meta.get("chunk_type", "content"),
                    "start": meta.get("start", 0)
                })
            
            if not notes_data:
                self.summary_error.emit("No se encontraron notas de los últimos 3 días.")
                return
            
            self.summary_progress.emit(f"Reconstruyendo {len(notes_data)} notas...")
            
            # RECONSTRUIR NOTAS
            recent_notes = []
            for note_id, note_data in notes_data.items():
                chunks = sorted(note_data["chunks"], key=lambda x: x["start"])
                content_parts = []
                for chunk in chunks:
                    if chunk["chunk_type"] != "title":
                        content_parts.append(chunk["content"])
                
                combined_content = " ".join(content_parts)
                recent_notes.append({
                    "title": note_data["title"],
                    "content": combined_content,
                    "created_at": note_data["created_at"]
                })
            
            # Limitar a 20 notas más recientes
            recent_notes = sorted(recent_notes, 
                                key=lambda x: x.get("created_at", ""), 
                                reverse=True)[:20]
            
            self.summary_progress.emit(f"Generando resumen de {len(recent_notes)} notas...")
            
            # PREPARAR CONTENIDO
            content_parts = []
            for note in recent_notes:
                date_str = format_date_chile(note["created_at"]) if note["created_at"] else "Fecha desconocida"
                content_parts.append(f"=== {note['title']} ({date_str}) ===\n{note['content']}\n")
            
            combined_content = "\n".join(content_parts)
            if len(combined_content) > 15000:
                combined_content = combined_content[:15000] + "\n[...contenido truncado]"
            
            self.summary_progress.emit("Generando resumen con IA...")
            
            # GENERAR RESUMEN CON STREAMING
            self._generate_streaming_summary(combined_content)
            
        except Exception as e:
            self.summary_error.emit(f"Error generando resumen: {str(e)}")

    def _generate_streaming_summary(self, content: str):
        """Genera resumen con streaming"""
        try:
            system_prompt = """Eres el Asistente de Claudio Montoya jefe del departamento de desarrollo de software, especializado en crear resúmenes ejecutivos claros y útiles. 
            Analiza las notas proporcionadas y crea un resumen estructurado que incluya:
            
            #Formato Salida
                -texto sin formato 
                -valido para tranformar a audio incluye punto y comas para pausas.
                
            1. **Resumen Ejecutivo**: Los aspectos más críticos y urgentes, enfocándote en decisiones tomadas, problemas identificados y avances concretos
            2. **Actividades Principales**: Eventos específicos, reuniones importantes y acciones ejecutadas (evita repetir títulos obvios)
            3. **Ideas y Decisiones Clave**: Decisiones técnicas, criterios establecidos, metodologías adoptadas y soluciones propuestas
            4. **Pendientes y Acciones**: Tareas específicas identificadas, responsabilidades asignadas y plazos mencionados
            5. **Temas Recurrentes**: Patrones en problemáticas, enfoques técnicos o procesos que aparecen múltiples veces
            6. **Filtros de Relevancia**: 
               - INCLUYE: decisiones técnicas, problemas operativos, configuraciones, procesos de trabajo
               - EXCLUYE: información obvia del contexto, títulos redundantes, generalidades sin valor
            
            Mantén un tono profesional útil para TTS de macOS nativo. Evita caracteres o símbolos que provoquen problemas de transcripción. Enfócate en INSIGHTS reales, no en información evidente."""
            
            user_prompt = f"Analiza y resume las siguientes notas de los últimos 3 días:\n\n{content}"
            
            # STREAMING CON OPENAI
            full_response = ""
            
            stream = self.ai.client.chat.completions.create(
                model=self.ai.settings.chat_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,
                max_tokens=3500,
                stream=True  # HABILITAR STREAMING
            )
            
            for chunk in stream:
                if chunk.choices[0].delta.content:
                    content_chunk = chunk.choices[0].delta.content
                    full_response += content_chunk
                    self.summary_streaming.emit(content_chunk)  # Emitir cada chunk
            
            self.summary_finished.emit(full_response)
            
        except Exception as e:
            self.summary_error.emit(f"Error generando resumen: {str(e)}")

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
    
    def __init__(self, settings: Settings, db: NotesDB, ai: AIService, vector: Optional[VectorIndex] = None):
        super().__init__()
        self.settings = settings
        self.db = db
        self.ai = ai
        self.vector = vector
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
        
        # CORREGIR VERIFICACIÓN - usar self.vector en lugar de self.ai.vector
        if not self.ai.settings.openai_api_key or not self.vector:
            self.btn_generate.setEnabled(False)
            self.btn_generate.setToolTip("Requiere configuración de OpenAI API y base vectorial")
        
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
        """Genera resumen de los últimos 3 días con streaming"""
        if not self.ai.settings.openai_api_key:
            QMessageBox.information(self, APP_NAME, 
                                "El resumen IA está deshabilitado.\n"
                                "Configura tu OpenAI API key en Ajustes.")
            return
        
        if not self.vector:
            QMessageBox.information(self, APP_NAME, 
                                "Base vectorial no disponible.\n"
                                "Asegúrate de tener notas indexadas.")
            return
        
        # Preparar UI para resumen asíncrono
        self._start_summary_ui()
        
        # Crear y lanzar worker thread
        self.summary_worker = SummaryWorker(self.vector, self.ai)
        
        # Conectar señales
        self.summary_worker.summary_finished.connect(self._on_summary_finished)
        self.summary_worker.summary_error.connect(self._on_summary_error)
        self.summary_worker.summary_progress.connect(self._on_summary_progress)
        self.summary_worker.summary_streaming.connect(self._on_summary_streaming)  # NUEVO
        
        # Iniciar resumen en hilo separado
        self.summary_worker.start()

    def _start_summary_ui(self):
        """Configura UI para estado de resumen"""
        self.btn_generate.setText("Generando...")
        self.btn_generate.setEnabled(False)
        self.summary_text.clear()
        self.summary_text.setPlaceholderText("🔄 Generando resumen...")

    def _on_summary_progress(self, message: str):
        """Actualiza progreso en UI"""
        self.summary_text.setPlaceholderText(f"🔄 {message}")

    def _on_summary_streaming(self, chunk: str):
        """Maneja chunks de streaming en tiempo real"""
        current_text = self.summary_text.toPlainText()
        
        # Si es el primer chunk, limpiar placeholder
        if "🔄" in current_text or "El resumen aparecerá aquí..." in current_text:
            self.summary_text.clear()
            current_text = ""
        
        # Añadir nuevo chunk
        self.summary_text.setPlainText(current_text + chunk)
        
        # Mover cursor al final
        cursor = self.summary_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.summary_text.setTextCursor(cursor)
        
        # Actualizar visualmente
        self.summary_text.update()
        QCoreApplication.processEvents()

    def _on_summary_finished(self, response: str):
        """Maneja finalización exitosa Y ACTIVA AUDIO"""
        self._reset_summary_ui()
        self.btn_copy_summary.setEnabled(True)
        
        # GENERAR AUDIO AUTOMÁTICAMENTE CUANDO TERMINA EL STREAM
        final_text = self.summary_text.toPlainText()
        if final_text.strip():
            self._generate_audio(final_text)

    def _on_summary_error(self, error: str):
        """Maneja errores de resumen"""
        self.summary_text.clear()
        self.summary_text.setPlainText(f"Error generando resumen: {error}")
        self._reset_summary_ui()

    def _reset_summary_ui(self):
        """Resetea UI después de resumen"""
        self.btn_generate.setText("🤖 Generar Resumen de últimos 3 días")
        self.btn_generate.setEnabled(True)
        
        # Limpiar worker
        if hasattr(self, 'summary_worker'):
            self.summary_worker.deleteLater()
    

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
                    background-color: #65ab65;
                    color: #ffffff;
                    border: none;
                    border-radius: 8px;
                    padding: 10px 20px;
                    font-family: '.AppleSystemUIFont';
                    font-size: 14px;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    background-color: #4d8c4d;
                }}
                QPushButton:disabled {{
                   background-color: #a8cfa8;   
                   color: #f0f0f0;
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
    # En la clase EnhancedNoteEditor (línea ~890 aprox)
    def _delete_current_note(self):
        """Elimina la nota actualmente cargada de forma atómica"""
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
                # ELIMINACIÓN ATÓMICA
                vector_success = False
                
                # 1. Eliminar de vector store primero
                if self.vector:
                    try:
                        self.vector.delete_note_chunks(self.current_note_id)
                        vector_success = True
                    except Exception as e:
                        QMessageBox.critical(self, "Error", f"Error eliminando de índice vectorial: {e}")
                        return
                
                # 2. Eliminar de SQLite solo si vector fue exitoso
                try:
                    self.db.delete_note(self.current_note_id)
                except Exception as e:
                    # Rollback: intentar restaurar en vector si se eliminó
                    if vector_success and self.vector:
                        try:
                            self.vector.index_note(
                                note.id, note.title, note.content, 
                                note.category, note.tags, note.source
                            )
                        except:
                            pass
                    QMessageBox.critical(self, "Error", f"Error eliminando de base de datos: {e}")
                    return
                
                # 3. Eliminar archivo de audio si existe
                if note.audio_path and os.path.exists(note.audio_path):
                    try:
                        os.remove(note.audio_path)
                    except Exception:
                        pass
                
                # 4. Limpiar editor y notificar
                self.clear_editor()
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
    
    # En la clase EnhancedNoteEditor (línea ~750 aprox) - Reemplazar _do_save()
    def _do_save(self, title: str, content: str, category: str):
        """Ejecuta el guardado real de forma atómica"""
        try:
            chile_tz = pytz.timezone('America/Santiago')
            final_title = self._get_final_title()
            
            # Backup para rollback
            old_note = None
            if self.current_note_id:
                old_note = self.db.get_note(self.current_note_id)
            
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
            
            # GUARDADO ATÓMICO
            try:
                # 1. Guardar en SQLite
                note_id = self.db.upsert_note(note)
                self.current_note_id = note_id

                # 2. Indexar en vector store
                if self.vector:
                    try:
                        self.vector.index_note(note_id, final_title, content, category, [], "manual")
                        print(f"Nota {note_id} indexada correctamente")
                    except Exception as e:
                        # Rollback SQLite si falla vector
                        if old_note:
                            try:
                                self.db.upsert_note(old_note)
                                self.current_note_id = old_note.id
                            except:
                                pass
                        raise Exception(f"Error indexando en vector store: {e}")

                # 3. Actualizar UI solo si todo fue exitoso
                self.refresh_categories()
                self.is_dirty = False
                self.title_edit.setText(final_title)
                self._show_success_state()
                self.note_saved.emit()
                
            except Exception as e:
                # Ya se hizo rollback arriba, solo mostrar error
                self._show_error_state(str(e))
                
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
    
    # En la clase EnhancedNotesView (línea ~1200 aprox) - Reemplazar _delete_note_from_menu()
    def _delete_note_from_menu(self, note_id: int, item: QListWidgetItem):
        """Elimina nota desde menú contextual de forma atómica"""
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
                # ELIMINACIÓN ATÓMICA (mismo patrón que arriba)
                vector_success = False
                
                if self.vector:
                    try:
                        self.vector.delete_note_chunks(note_id)
                        vector_success = True
                    except Exception as e:
                        QMessageBox.critical(self, "Error", f"Error eliminando de índice: {e}")
                        return
                
                try:
                    self.db.delete_note(note_id)
                except Exception as e:
                    if vector_success and self.vector:
                        try:
                            self.vector.index_note(note.id, note.title, note.content, 
                                                note.category, note.tags, note.source)
                        except:
                            pass
                    QMessageBox.critical(self, "Error", f"Error eliminando: {e}")
                    return
                
                # Limpiar UI
                if note.audio_path and os.path.exists(note.audio_path):
                    try:
                        os.remove(note.audio_path)
                    except Exception:
                        pass
                
                self.notes_list.takeItem(self.notes_list.row(item))
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
    text_received = Signal(str)
    status_changed = Signal(str)
    error_occurred = Signal(str)
    note_saved = Signal()
    def __init__(self, settings: 'Settings', db: 'NotesDB', vector: Optional['VectorIndex'], ai: 'AIService'):
        super().__init__()
        self.settings = settings
        self.db = db
        self.vector = vector
        self.ai = ai
        
        self.speech_recognizer = sr.Recognizer()
        self.microphone = None
        self.recognition_thread = None
        self.realtime_text = []
        self.recognition_active = False
        self.recognition_working = False
        self.is_transcribing = False
        self.start_time = None
        self.text_buffer = []
        self.last_update_time = 0
        self.update_interval = 0.12
        self.speaker_history = []
        self.last_speaker_time = 0
        self.speaker_threshold = 3.0
        self.voice_profiles = []
        self.current_voice_features = None
        self.vad = None
        self.vad_available = self._init_webrtc_vad()
        self.audio_queue = queue.Queue(maxsize=12)
        self.executor = ThreadPoolExecutor(max_workers=3)
        self.capture_thread = None
        self.stop_event = threading.Event()
        self._overlap_buffer = deque(maxlen=1)
        self.recent_texts = []
        self.final_buffer = []
        self.last_activity_time = 0
        self._emergency_audio_buffer = []
        self._setup_ui()
        self._connect_signals()
        self._test_speech_recognition()
    
    def _init_webrtc_vad(self):
        try:
            import webrtcvad
            self.vad = webrtcvad.Vad(2)
            return True
        except ImportError:
            return False
        except Exception:
            return False
    
    def _setup_ui(self):
        BG_CARD = "#2a2a2e"; BG_INPUT = "#1c1c1e"; BORDER = "#3a3a3c"; TEXT = "#e5e5e7"; ACCENT = "#0a84ff"

        root = QVBoxLayout(self); root.setSpacing(16); root.setContentsMargins(20, 20, 20, 20)


        controls = QHBoxLayout()
        self.status_label = QLabel("Listo")
        self.status_label.setStyleSheet(f"color:{TEXT};font-size:12px;")
        controls.addWidget(self.status_label, 1, Qt.AlignLeft)

        def style_btn(b: QPushButton):
            b.setMinimumHeight(36)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{background:{BG_CARD};color:{TEXT};border:1px solid {BORDER};
                border-radius:10px;padding:8px 16px;}}
                QPushButton:hover {{border-color:{ACCENT};}}
                QPushButton:disabled {{color:#8e8e93;background:#2b2b2f;border-color:#2f2f33;}}
            """)

        self.btn_start = QPushButton("🟢 Iniciar"); style_btn(self.btn_start)
        self.btn_stop  = QPushButton("⏹️ Detener"); self.btn_stop.setEnabled(False); 
        self.btn_stop.setStyleSheet(f"""
            QPushButton {{background:#ff453a;color:white;border:1px solid #ff453a;
            border-radius:10px;padding:8px 16px;}}
            QPushButton:hover {{background:#ff6961;}}
            QPushButton:disabled {{color:#8e8e93;background:#2b2b2f;border-color:#2f2f33;}}
        """)
        self.btn_config = QPushButton("🔧 Configuración"); style_btn(self.btn_config)

        self.btn_start.clicked.connect(self.start_transcription)
        self.btn_stop.clicked.connect(self.stop_transcription)
        self.btn_config.clicked.connect(self._open_transcribe_settings)

        controls.addWidget(self.btn_start); controls.addWidget(self.btn_stop); controls.addWidget(self.btn_config)
        root.addLayout(controls)

        form = QGridLayout(); form.setHorizontalSpacing(12); form.setVerticalSpacing(8)
        label_style = f"color:{TEXT};font-size:13px;"
        input_css = f"border:1px solid {BORDER};border-radius:8px;padding:6px 10px;background:{BG_INPUT};color:{TEXT};min-height:34px;"

        def mk_lbl(t): l=QLabel(t); l.setStyleSheet(label_style); return l

        form.addWidget(mk_lbl("Título:"), 0, 0)
        self.title_edit = QLineEdit(); self.title_edit.setPlaceholderText("Título de la transcripción")
        self.title_edit.setStyleSheet(f"QLineEdit{{{input_css}}} QLineEdit:focus{{border-color:{ACCENT};}}")
        form.addWidget(self.title_edit, 0, 1, 1, 2)

        form.addWidget(mk_lbl("Categoría:"), 1, 0)
        self.category_combo = QComboBox()
        self.category_combo.setStyleSheet(f"""
            QComboBox{{{input_css}}}
            QComboBox::drop-down{{width:28px;border:none;}}
            QComboBox QAbstractItemView{{background:{BG_INPUT};color:{TEXT};
                selection-background-color:{ACCENT};border:1px solid {BORDER};}}
        """)
        
        categories = self.db.list_categories()
        if "Transcripciones" not in categories:
            categories.insert(0, "Transcripciones")
        for cat in categories:
            self.category_combo.addItem(cat)
        self.category_combo.setCurrentText("Transcripciones")
        
        form.addWidget(self.category_combo, 1, 1)

        self.single_speaker_mode = QCheckBox("Un solo hablante")
        self.single_speaker_mode.setChecked(True)
        self.single_speaker_mode.setStyleSheet(f"color:{TEXT};")
        form.addWidget(self.single_speaker_mode, 1, 2, Qt.AlignLeft)

        root.addLayout(form)

        root.addWidget(mk_lbl("Transcripción en vivo"))
        self.transcript_preview = QTextEdit(); self.transcript_preview.setReadOnly(True)
        self.transcript_preview.setPlaceholderText("El texto aparecerá aquí mientras hablas...")
        self.transcript_preview.setStyleSheet(f"""
            QTextEdit{{background:{BG_INPUT};color:{TEXT};border:1px solid {BORDER};border-radius:10px;
            padding:8px 10px;font-family:'.AppleSystemUIFont';font-size:14px;}}
        """)
        self.transcript_preview.textChanged.connect(self._on_transcript_changed)
        root.addWidget(self.transcript_preview, 1)

        actions = QHBoxLayout(); actions.addStretch(1)
        self.btn_save_note = QPushButton("💾 Guardar"); self.btn_save_note.setEnabled(False); style_btn(self.btn_save_note)
        self.btn_copy = QPushButton("📋 Copiar"); self.btn_copy.setEnabled(False); style_btn(self.btn_copy)
        self.btn_clear = QPushButton("🗑️ Limpiar"); style_btn(self.btn_clear)
        self.btn_save_note.clicked.connect(self._save_as_note)
        self.btn_copy.clicked.connect(self._copy_transcription)
        self.btn_clear.clicked.connect(self._clear_transcription)
        actions.addWidget(self.btn_save_note); actions.addWidget(self.btn_copy); actions.addWidget(self.btn_clear)
        root.addLayout(actions)
        
        self.duration_timer = QTimer()
        self.duration_timer.timeout.connect(self._update_duration)
        
        self.ui_update_timer = QTimer()
        self.ui_update_timer.timeout.connect(self._flush_text_buffer)
        self.ui_update_timer.setInterval(int(self.update_interval * 1000))
    
    def _connect_signals(self):
        self.text_received.connect(self._handle_text_update)
        self.status_changed.connect(self._handle_status_update)
        self.error_occurred.connect(self._handle_error_update)
        
    def _test_speech_recognition(self):
        if not self._check_and_request_microphone_access():
            self.recognition_working = False
            return
            
        try:
            self.microphone = sr.Microphone()
            with self.microphone as source:
                self.speech_recognizer.adjust_for_ambient_noise(source, duration=0.3)
            
            self.speech_recognizer.energy_threshold = max(2500, int(self.speech_recognizer.energy_threshold))
            self.speech_recognizer.pause_threshold = 0.8
            self.speech_recognizer.phrase_threshold = 0.25
            self.speech_recognizer.dynamic_energy_threshold = True
            self.speech_recognizer.dynamic_energy_adjustment_damping = 0.12
            self.speech_recognizer.dynamic_energy_ratio = 1.4
            self.speech_recognizer.operation_timeout = None
            self.speech_recognizer.non_speaking_duration = 0.6
            
            self.recognition_working = True
            self.btn_start.setEnabled(True)
            self.status_label.setText("Listo - Configuración estable activa")
                
        except Exception as e:
            self.microphone = None
            self.recognition_working = False
            QTimer.singleShot(500, self._show_permission_guide)

    def start_transcription(self):
        if not self.recognition_working:
            QMessageBox.warning(self, APP_NAME, "El reconocimiento de voz no está disponible.")
            return
        
        try:
            self._configure_microphone_for_fast_speech()
            
            self.is_transcribing = True
            self.recognition_active = True
            self.start_time = time.time()
            self.last_activity_time = time.time()
            self.realtime_text = []
            self.text_buffer = []
            self.final_buffer = []
            self.recent_texts = []
            self.speaker_history = []
            self.last_speaker_time = 0
            self.voice_profiles = []
            self.current_voice_features = None
            
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)
            
            self.status_label.setText("🔴 Transcribiendo...")
            
            self.transcript_preview.clear()
            vad_info = " (VAD activo)" if self.vad_available else ""
            self.transcript_preview.append(f"🎤 Transcripción activa{vad_info}...\n")
            
            self._start_realtime_recognition()
            
            self.duration_timer.start(1000)
            self.ui_update_timer.setInterval(int(self.update_interval * 1000))
            self.ui_update_timer.start()
            
            self.periodic_flush_timer = QTimer()
            self.periodic_flush_timer.timeout.connect(self._periodic_flush_and_check)
            self.periodic_flush_timer.start(1500)
                        
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, f"No se pudo iniciar la transcripción: {e}")
            self._reset_state()

    def stop_transcription(self):
        if not self.is_transcribing:
            return

        self.recognition_active = False
        # CAMBIO: Dar más tiempo para capturar últimos audios
        QTimer.singleShot(2000, self._finish_transcription_cleanup)  # 800ms -> 2000ms
    
    def _start_realtime_recognition(self):
        if not self.microphone:
            try:
                self.microphone = sr.Microphone()
            except Exception as e:
                self.error_occurred.emit(f"No hay micrófono: {e}")
                return

        with self.audio_queue.mutex:
            self.audio_queue.queue.clear()
        self.stop_event.clear()

        def _capture_loop():
            try:
                with self.microphone as source:
                    self.speech_recognizer.pause_threshold = 0.8
                    self.speech_recognizer.phrase_threshold = 0.25
                    self.speech_recognizer.non_speaking_duration = 0.6
                    phrase_time_limit = 4.0

                    while self.recognition_active and not self.stop_event.is_set():
                        try:
                            audio = self.speech_recognizer.listen(
                                source,
                                timeout=1.5,
                                phrase_time_limit=phrase_time_limit
                            )
                            
                            audio_timestamp = time.time()
                            self.last_activity_time = audio_timestamp
                            
                            # CAMBIO: Manejo más agresivo de cola llena
                            try:
                                self.audio_queue.put_nowait((audio, audio_timestamp))
                            except queue.Full:
                                # Eliminar hasta 3 elementos antiguos para hacer espacio
                                for _ in range(3):
                                    try:
                                        _ = self.audio_queue.get_nowait()
                                    except queue.Empty:
                                        break
                                try:
                                    self.audio_queue.put_nowait((audio, audio_timestamp))
                                except queue.Full:
                                    # Solo como último recurso, guardar en buffer de emergencia
                                    if not hasattr(self, '_emergency_audio_buffer'):
                                        self._emergency_audio_buffer = []
                                    self._emergency_audio_buffer.append((audio, audio_timestamp))
                                    print("Audio guardado en buffer de emergencia")
                        except Exception:
                            continue
            except Exception as e:
                self.error_occurred.emit(f"Error de captura: {e}")
        def _consume_loop():
            while self.recognition_active and not self.stop_event.is_set():
                try:
                    audio_data = self.audio_queue.get(timeout=0.5)
                    audio, timestamp = audio_data
                except queue.Empty:
                    if time.time() - self.last_activity_time > 3.0:
                        self._process_final_buffer()
                    continue

                def _work(audio_chunk, chunk_timestamp):
                    try:
                        text = self.speech_recognizer.recognize_google(audio_chunk, language='es-CL')
                        if not text or not text.strip():
                            return None
                        
                        voice_features = {}
                        try:
                            if self.vad_available:
                                voice_features = self._analyze_voice_activity(audio_chunk)
                        except Exception:
                            voice_features = {"vad_available": False}
                        
                        return {
                            "text": text.strip(),
                            "voice_features": voice_features,
                            "timestamp": chunk_timestamp,
                            "processing_time": time.time()
                        }
                        
                    except Exception:
                        try:
                            backup_text = self._recognize_with_multiple_languages(audio_chunk)
                            if backup_text:
                                return {
                                    "text": backup_text.strip(),
                                    "voice_features": {"vad_available": False},
                                    "timestamp": chunk_timestamp,
                                    "processing_time": time.time(),
                                    "backup_recognition": True
                                }
                        except Exception:
                            pass
                    return None
                    
                future = self.executor.submit(_work, audio, timestamp)
                
                def _on_done(fut):
                    try:
                        result = fut.result()
                        if result:
                            self.final_buffer.append(result)
                            
                            text = result.get("text", "")
                            if text:
                                self.realtime_text.append(text)
                                self.text_received.emit(json.dumps(result))
                    except Exception as e:
                        print(f"Error en callback: {e}")
                        
                future.add_done_callback(_on_done)

        self.capture_thread = threading.Thread(target=_capture_loop, daemon=True)
        self.recognition_thread = threading.Thread(target=_consume_loop, daemon=True)
        self.capture_thread.start()
        self.recognition_thread.start()

    def _periodic_flush_and_check(self):
        try:
            self._flush_text_buffer()
            
            silence_duration = time.time() - self.last_activity_time
            
            if silence_duration > 4.0 and self.final_buffer:
                self._process_final_buffer()
                
            if self.is_transcribing:
                word_count = len(" ".join(self.realtime_text).split())
                self.status_label.setText(f"🔴 Transcribiendo... ({word_count} palabras)")
                
        except Exception as e:
            print(f"Error en flush periódico: {e}")

    def _process_final_buffer(self):
        if not self.final_buffer:
            return
        
        try:
            recent_results = []
            current_time = time.time()
            
            for item in self.final_buffer:
                # Manejar tanto diccionarios como strings
                if isinstance(item, dict):
                    processing_time = item.get("processing_time", current_time)
                    if current_time - processing_time < 8.0:  # CAMBIO: 5.0 -> 8.0 segundos
                        recent_results.append(item)
                elif isinstance(item, str) and item.strip():
                    recent_results.append({
                        "text": item.strip(),
                        "processing_time": current_time - 1.0
                    })
            
            if recent_results:
                final_texts = []
                existing_texts = [t for t in self.realtime_text[-8:]] if self.realtime_text else []  # CAMBIO: -5 -> -8
                
                for result in recent_results:
                    text = result.get("text", "") if isinstance(result, dict) else str(result)
                    if text and text not in existing_texts:
                        final_texts.append(text)
                
                if final_texts:
                    consolidated = " ".join(final_texts)
                    formatted_text = self._detect_speaker_changes_with_vad(consolidated, {})
                    self.text_buffer.append(formatted_text)
                    QTimer.singleShot(100, self._flush_text_buffer)
            
            # CAMBIO: No limpiar buffer inmediatamente, solo marcar como procesado
            for item in self.final_buffer:
                if isinstance(item, dict):
                    item["processed"] = True
            
            # Limpiar solo items procesados hace más de 10 segundos
            current_time = time.time()
            self.final_buffer = [
                item for item in self.final_buffer 
                if not (isinstance(item, dict) and item.get("processed") and 
                    current_time - item.get("processing_time", 0) > 10.0)
            ]
            
        except Exception as e:
            print(f"Error procesando buffer final: {e}")
            # NO limpiar en caso de error, mantener para reintento
    def _finish_transcription_cleanup(self):
        self.stop_event.set()

        # 1. Procesar buffer de emergencia si existe
        emergency_audio = getattr(self, '_emergency_audio_buffer', [])
        
        # 2. Procesar TODO el audio restante en queue
        remaining_audio = []
        try:
            while True:
                audio_data = self.audio_queue.get_nowait()
                remaining_audio.append(audio_data)
        except queue.Empty:
            pass
        
        # Combinar audio de emergencia con el restante
        all_remaining = emergency_audio + remaining_audio
        
        # 3. Procesar audio restante con más tiempo y tolerancia a errores
        for audio, timestamp in all_remaining:
            try:
                text = self.speech_recognizer.recognize_google(audio, language='es-CL')
                if text and text.strip():
                    formatted_text = self._detect_speaker_changes_with_vad(text, {})
                    self.text_buffer.append(formatted_text)
                    self.realtime_text.append(text)
            except Exception:
                # Intentar con backup
                try:
                    backup_text = self._recognize_with_multiple_languages(audio)
                    if backup_text:
                        self.text_buffer.append(f"\n{backup_text}")
                        self.realtime_text.append(backup_text)
                except Exception:
                    pass

        # 4. Procesar buffer final una vez más
        self._process_final_buffer()
        
        # 5. Flush final del texto
        self._flush_text_buffer()

        # 6. Esperar threads con timeout más generoso
        if self.capture_thread and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=5.0)  # CAMBIO: 1.5 -> 5.0
        if self.recognition_thread and self.recognition_thread.is_alive():
            self.recognition_thread.join(timeout=5.0)  # CAMBIO: 2.0 -> 5.0
        
        # 7. NUEVO: Esperar ThreadPoolExecutor
        try:
            self.executor.shutdown(wait=True)
            # Dar tiempo adicional para trabajos pendientes
            import concurrent.futures
            try:
                # Esperar hasta 8 segundos por trabajos pendientes
                self.executor = ThreadPoolExecutor(max_workers=3)  # Recrear para próxima vez
            except Exception:
                pass
        except Exception as e:
            print(f"Error cerrando executor: {e}")

        # 8. Limpiar buffer de emergencia
        if hasattr(self, '_emergency_audio_buffer'):
            delattr(self, '_emergency_audio_buffer')

        self.is_transcribing = False

        if hasattr(self, 'ui_update_timer'):
            self.ui_update_timer.stop()
        if hasattr(self, 'periodic_flush_timer'):
            self.periodic_flush_timer.stop()

        self._reset_state()
        self._auto_generate_title()

        total_words = len(self._clean_content(self.transcript_preview.toPlainText()).split())
        self.status_label.setText(f"✅ Transcripción completada - {total_words} palabras")
    
    def _analyze_voice_activity(self, audio_data):
        try:
            import numpy as np
            
            raw_data = audio_data.get_raw_data()
            audio_array = np.frombuffer(raw_data, dtype=np.int16)
            
            energy = np.sqrt(np.mean(audio_array**2))
            zero_crossings = len(np.where(np.diff(np.signbit(audio_array)))[0])
            duration = len(audio_array) / audio_data.sample_rate
            
            vad_frames = []
            speech_ratio = 0.0
            
            if self.vad_available and audio_data.sample_rate == 16000:
                try:
                    frame_duration = 30
                    samples_per_frame = int(audio_data.sample_rate * frame_duration / 1000)
                    
                    for i in range(0, len(audio_array), samples_per_frame):
                        frame = audio_array[i:i+samples_per_frame]
                        if len(frame) == samples_per_frame:
                            frame_bytes = frame.tobytes()
                            is_speech = self.vad.is_speech(frame_bytes, audio_data.sample_rate)
                            vad_frames.append(is_speech)
                    
                    if vad_frames:
                        speech_ratio = sum(vad_frames) / len(vad_frames)
                        
                except Exception:
                    pass
            
            dominant_freq = 0.0
            try:
                fft = np.fft.fft(audio_array)
                freqs = np.fft.fftfreq(len(audio_array), 1/audio_data.sample_rate)
                positive_freqs = freqs[:len(freqs)//2]
                positive_fft = np.abs(fft[:len(fft)//2])
                if len(positive_fft) > 0:
                    dominant_freq = positive_freqs[np.argmax(positive_fft)]
            except Exception:
                pass
            
            return {
                "energy": float(energy),
                "zero_crossings": int(zero_crossings),
                "dominant_freq": float(abs(dominant_freq)),
                "duration": float(duration),
                "speech_ratio": float(speech_ratio),
                "vad_available": self.vad_available,
                "total_frames": len(vad_frames),
                "speech_frames": sum(vad_frames) if vad_frames else 0
            }
            
        except Exception:
            return {
                "energy": 0.0, "zero_crossings": 0, "dominant_freq": 0.0,
                "duration": 0.0, "speech_ratio": 0.0, "vad_available": False,
                "total_frames": 0, "speech_frames": 0
            }
    
    def _detect_speaker_changes_with_vad(self, text, voice_features):
        if not text or len(text.strip()) < 2:
            return text
        
        current_time = time.time()
        current_text = self.transcript_preview.toPlainText()
        
        if not current_text or "🎤" in current_text:
            self.last_speaker_time = current_time
            self.voice_profiles = [voice_features] if voice_features else []
            self.current_voice_features = voice_features
            return f"\n\n👤 Usuario 1:\n{text}"
        
        if hasattr(self, 'single_speaker_mode') and self.single_speaker_mode.isChecked():
            self.last_speaker_time = current_time
            return f"\n{text}"
        
        time_gap = current_time - self.last_speaker_time
        should_new_speaker = False
        
        if time_gap > 4.0:
            should_new_speaker = True
        elif self.current_voice_features and voice_features.get('vad_available', False):
            voice_change_score = self._calculate_voice_change_score(
                self.current_voice_features, voice_features
            )
            
            if voice_change_score > 0.6:
                should_new_speaker = True
        
        last_lines = current_text.split('\n')[-2:]
        last_content = ' '.join(line for line in last_lines if line.strip() and not line.startswith('👤'))
        
        if last_content and self._has_strong_conversation_markers(text, last_content):
            should_new_speaker = True
        
        current_speaker_count = current_text.count('👤 Usuario')
        if should_new_speaker and current_speaker_count >= 4:
            should_new_speaker = False
        
        if should_new_speaker:
            speaker_count = current_speaker_count + 1
            self.last_speaker_time = current_time
            self.voice_profiles.append(voice_features)
            self.current_voice_features = voice_features
            return f"\n\n👤 Usuario {speaker_count}:\n{text}"
        else:
            self.last_speaker_time = current_time
            if self.current_voice_features:
                self.current_voice_features = self._merge_voice_features(
                    self.current_voice_features, voice_features
                )
            return f"\n{text}"

    def _calculate_voice_change_score(self, prev_features, curr_features):
        try:
            score = 0.0
            
            if prev_features.get('energy', 0) > 0 and curr_features.get('energy', 0) > 0:
                energy_ratio = abs(curr_features['energy'] - prev_features['energy']) / prev_features['energy']
                if energy_ratio > 0.5:
                    score += 0.3
            
            prev_freq = prev_features.get('dominant_freq', 0)
            curr_freq = curr_features.get('dominant_freq', 0)
            if prev_freq > 50 and curr_freq > 50:
                freq_change = abs(curr_freq - prev_freq) / max(prev_freq, curr_freq)
                if freq_change > 0.2:
                    score += 0.4
            
            prev_speech_ratio = prev_features.get('speech_ratio', 0)
            curr_speech_ratio = curr_features.get('speech_ratio', 0)
            speech_ratio_change = abs(curr_speech_ratio - prev_speech_ratio)
            if speech_ratio_change > 0.3:
                score += 0.2
            
            prev_zcr = prev_features.get('zero_crossings', 0)
            curr_zcr = curr_features.get('zero_crossings', 0)
            if prev_zcr > 0 and curr_zcr > 0:
                zcr_change = abs(curr_zcr - prev_zcr) / max(prev_zcr, curr_zcr)
                if zcr_change > 0.4:
                    score += 0.1
            
            return min(score, 1.0)
            
        except Exception:
            return 0.0
    
    def _merge_voice_features(self, prev_features, curr_features):
        try:
            merged = {}
            
            for key in ['energy', 'zero_crossings', 'dominant_freq', 'speech_ratio']:
                prev_val = prev_features.get(key, 0)
                curr_val = curr_features.get(key, 0)
                merged[key] = prev_val * 0.7 + curr_val * 0.3
            
            merged['vad_available'] = curr_features.get('vad_available', False)
            merged['duration'] = curr_features.get('duration', 0)
            
            return merged
            
        except Exception:
            return curr_features
    
    def _has_strong_conversation_markers(self, current_text, previous_text):
        current_lower = current_text.lower()
        
        strong_markers = [
            'perdón pero', 'disculpa', 'perdona', 'una pregunta',
            'yo opino que', 'no estoy de acuerdo', 'me parece que no',
            'por mi parte', 'desde mi punto de vista', 'en mi experiencia',
            'cambiando de tema', 'otra cosa', 'bueno ahora',
            'quería agregar', 'solo para aclarar', 'tengo una duda'
        ]
        
        starts_with_marker = any(current_lower.startswith(marker) for marker in strong_markers)
        
        if previous_text:
            similarity = self._calculate_text_similarity(current_text, previous_text[-100:])
            semantic_change = similarity < 0.1
        else:
            semantic_change = False
        
        return starts_with_marker or semantic_change

    def _handle_text_update(self, data):
        try:
            text = ""
            voice_features = {}
            
            try:
                if data.startswith('{"text"') and data.endswith('}'):
                    parsed_data = json.loads(data)
                    text = parsed_data.get("text", "")
                    voice_features = parsed_data.get("voice_features", {})
                else:
                    text = data
                    voice_features = {}
            except (json.JSONDecodeError, TypeError):
                text = str(data) if data else ""
                voice_features = {}
            
            if not text or len(text.strip()) < 2:
                return
            
            formatted_text = self._detect_speaker_changes_with_vad(text, voice_features)
            self.text_buffer.append(formatted_text)
            self._auto_generate_title()
            QTimer.singleShot(100, self._force_button_update)
            
        except Exception as e:
            if isinstance(data, str) and data.strip():
                self.text_buffer.append(f"\n{data}")

    def _force_button_update(self):
        text = self.transcript_preview.toPlainText().strip()
        clean_text = self._clean_content(text)
        
        if clean_text and len(clean_text) > 10:
            self.btn_save_note.setEnabled(True)
            self.btn_copy.setEnabled(True)

    def _auto_generate_title(self):
        if self.title_edit.text().strip() in ["", "Título automático..."]:
            if self.realtime_text and len(self.realtime_text) > 0:
                combined_text = " ".join(self.realtime_text)
                if len(combined_text.split()) >= 3:
                    words = combined_text.split()[:6]
                    suggested_title = " ".join(words)
                    if len(combined_text.split()) > 6:
                        suggested_title += "..."
                    self.title_edit.setText(suggested_title)

    def _flush_text_buffer(self):
        if not self.text_buffer:
            return

        pending = self.text_buffer[:]
        self.text_buffer.clear()

        current_text = self.transcript_preview.toPlainText()
        if "🎤" in current_text and "Transcripción" in current_text:
            self.transcript_preview.clear()
            current_text = ""

        merged = []
        current_paragraph = []

        for chunk in pending:
            chunk_str = self._process_fast_speech_text(str(chunk))
            
            if chunk_str.startswith('\n\n'):
                if current_paragraph:
                    merged.append(" ".join(current_paragraph))
                    current_paragraph = []
                clean_chunk = chunk_str.replace('\n\n', '').strip()
                if clean_chunk:
                    current_paragraph = [clean_chunk]
            else:
                clean_chunk = chunk_str.strip()
                if clean_chunk:
                    current_paragraph.append(clean_chunk)
            
            if current_paragraph and len(" ".join(current_paragraph)) > 150:
                merged.append(" ".join(current_paragraph))
                current_paragraph = []

        if current_paragraph:
            merged.append(" ".join(current_paragraph))

        if merged:
            cursor = self.transcript_preview.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            for line in merged:
                self.transcript_preview.append(line)
            self.transcript_preview.setTextCursor(cursor)

        self.transcript_preview.update()
        QCoreApplication.processEvents()

    def _process_fast_speech_text(self, text):
        if not isinstance(text, str):
            text = str(text)
        text = ' '.join(text.split())
        fast_speech_corrections = {
            'esque': 'es que', 'porfa': 'por favor', 'obvio': 'obviamente',
            'osea': 'o sea', 'porfavor': 'por favor', 'nose': 'no sé',
            'nomas': 'no más', 'aver': 'a ver', 'deuna': 'de una',
            'yapo': 'ya poh'
        }
        words = text.split()
        corrected_words = []
        for word in words:
            word_lower = word.lower().strip('.,!?')
            corrected_words.append(fast_speech_corrections.get(word_lower, word))
        return ' '.join(corrected_words)

    def _calculate_text_similarity(self, text1, text2):
        if not text1 or not text2:
            return 0.0
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        if not words1 or not words2:
            return 0.0
        intersection = words1.intersection(words2)
        union = words1.union(words2)
        return len(intersection) / len(union) if union else 0.0

    def _recognize_with_multiple_languages(self, audio):
        language_configs = [
            ('es-CL', False), ('es-419', False), ('es-AR', False),
            ('es-MX', False), ('es-ES', False),
        ]
        
        for language, use_show_all in language_configs:
            try:
                text = self.speech_recognizer.recognize_google(audio, language=language)
                if text and isinstance(text, str) and text.strip():
                    return text
            except:
                continue
        
        raise sr.UnknownValueError("No se pudo reconocer con ningún idioma")

    def _configure_microphone_for_fast_speech(self):
        try:
            if not self.microphone:
                self.microphone = sr.Microphone()
            with self.microphone as source:
                for _ in range(3):
                    self.speech_recognizer.adjust_for_ambient_noise(source, duration=0.1)
            original_threshold = self.speech_recognizer.energy_threshold
            self.speech_recognizer.energy_threshold = max(300, int(original_threshold * 0.7))
            self.speech_recognizer.non_speaking_duration = 0.6
            self.speech_recognizer.pause_threshold = min(self.speech_recognizer.pause_threshold, 0.8)
        except Exception as e:
            print(f"Error configurando micrófono: {e}")

    def _check_and_request_microphone_access(self):
        try:
            recognizer = sr.Recognizer()
            mic = sr.Microphone()
            with mic as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
            return True
        except Exception:
            self._show_permission_guide()
            return False

    def _show_permission_guide(self):
        guide_text = """
Para habilitar el micrófono:

1. Ve a Configuración del Sistema (o Preferencias del Sistema)
2. Selecciona "Privacidad y Seguridad"
3. En el panel izquierdo, busca "Micrófono"
4. Asegúrate de que esta aplicación esté marcada ✓

Después de habilitar los permisos, reinicia la aplicación.
        """
        
        msg = QMessageBox(self)
        msg.setWindowTitle("Configurar permisos de micrófono")
        msg.setText("Permisos de micrófono requeridos")
        msg.setDetailedText(guide_text)
        msg.setIcon(QMessageBox.Information)
        msg.exec()

    def _handle_status_update(self, status):
        if status == "silence_warning":
            self.text_buffer.append("\n⚠️ Silencio detectado")

    def _handle_error_update(self, error):
        self.text_buffer.append(f"\n❌ Error: {error}")

    def _reset_state(self):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        
        self.status_label.setText("Listo")
        
        if hasattr(self, 'duration_timer'):
            self.duration_timer.stop()
        if hasattr(self, 'ui_update_timer'):
            self.ui_update_timer.stop()
        if hasattr(self, 'periodic_flush_timer'):
            self.periodic_flush_timer.stop()
        
        self.is_transcribing = False
        self.recognition_active = False

    def _update_duration(self):
        if self.is_transcribing and self.start_time:
            duration = time.time() - self.start_time
            mins = int(duration // 60)
            secs = int(duration % 60)
            self.status_label.setText(f"Transcribiendo... {mins:02d}:{secs:02d}")

    def _clean_content(self, content):
        if not content:
            return ""
        
        content = content.replace("🎤 Transcripción activa", "")
        content = content.replace("(VAD activo)", "")
        
        lines = content.split("\n")
        clean_lines = []
        
        for line in lines:
            line = line.strip()
            
            if not line:
                continue
            if line.startswith("⚠️") or line.startswith("❌"):
                continue
            if line.startswith('{"text"') or "voice_features" in line:
                continue
            
            if line.startswith("👤 Usuario"):
                parts = line.split(":", 1)
                if len(parts) > 1:
                    clean_text = parts[1].strip()
                    if clean_text:
                        clean_lines.append(clean_text)
            else:
                clean_lines.append(line)
        
        return "\n".join(clean_lines).strip()

    def _copy_transcription(self):
        text = self.transcript_preview.toPlainText().strip()
        if text and not text.startswith("🎤"):
            clean_text = self._clean_content(text)
            try:
                import pyperclip
                pyperclip.copy(clean_text)
                QMessageBox.information(self, "Copiado", "Texto copiado al portapapeles")
            except:
                clipboard = QApplication.clipboard()
                clipboard.setText(clean_text)
                QMessageBox.information(self, "Copiado", "Texto copiado al portapapeles")

    def _clear_transcription(self):
        self.transcript_preview.clear()
        self.title_edit.clear()
        self.realtime_text = []
        self.text_buffer = []
        self.final_buffer = []
        self.speaker_history = []
        self.last_speaker_time = 0
        self.voice_profiles = []
        self.current_voice_features = None
        self.status_label.setText("Listo para transcribir" if self.recognition_working else "Configura micrófono para continuar")

    def _save_as_note(self):
        content = self.transcript_preview.toPlainText().strip()
        title = self.title_edit.text().strip()
        
        if not content or content.startswith("🎤") or len(content) < 10:
            QMessageBox.information(self, "Sin contenido", "No hay suficiente texto para guardar.")
            return
        
        content = self._clean_content(content)
        
        if not title or title == "Título automático...":
            title = "Transcripción " + datetime.now().strftime("%d/%m/%Y %H:%M")
        
        try:
            from app.db import Note
            category = self.category_combo.currentText().strip() or "Transcripciones"
            
            self.db.add_category(category)
            note = Note(
                id=None,
                title=title,
                content=content,
                category=category,
                tags=["transcripción", "tiempo-real"],
                source="transcript",
                audio_path=None,
                created_at=datetime.utcnow().isoformat(),
                updated_at=datetime.utcnow().isoformat(),
            )
            note_id = self.db.upsert_note(note)

            if self.vector:
                try:
                    self.vector.index_note(note_id, title, content, category, ["transcripción", "tiempo-real"], "transcript")
                except Exception as e:
                    print(f"Error indexando: {e}")
            
            # NUEVO: Emitir señal para notificar a otras vistas
            self.note_saved.emit()
            
            QMessageBox.information(self, "Guardado", f"Transcripción guardada como '{title}'")
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error al guardar: {e}")
    def _open_transcribe_settings(self):
        msg = QMessageBox(self)
        msg.setWindowTitle("Configuración de Transcripción")
        msg.setText("Ajustes de reconocimiento de voz")
        msg.setDetailedText(f"""
Configuración actual:
- Umbral de energía: {getattr(self.speech_recognizer, 'energy_threshold', 'N/A')}
- Pausa entre frases: {getattr(self.speech_recognizer, 'pause_threshold', 'N/A')} seg
- Duración sin habla: {getattr(self.speech_recognizer, 'non_speaking_duration', 'N/A')} seg
- Detección VAD: {'Activa' if self.vad_available else 'Inactiva'}

Para mejor captura:
- Hablar claramente y sin prisa
- Hacer pausas naturales entre ideas
- Evitar ruidos de fondo
- Usar micrófono de calidad
        """)
        msg.exec()

    def _on_transcript_changed(self):
        text = self.transcript_preview.toPlainText().strip()
        clean_text = self._clean_content(text)
        has_content = bool(clean_text and len(clean_text) > 10)
        
        self.btn_save_note.setEnabled(has_content)
        self.btn_copy.setEnabled(has_content)
        
class SearchTab(QWidget):
    """Tab de búsqueda estilo Apple - CORREGIDO"""
    
    def __init__(self, settings: Settings, db: NotesDB, vector: Optional[VectorIndex], ai: AIService, main_window=None):
        super().__init__()
        self.settings = settings
        self.db = db
        self.vector = vector
        self.ai = ai
        self.main_window = main_window  # NUEVO: Referencia al main window
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(32)
        layout.setContentsMargins(60, 60, 60, 60)
               
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
        
        # Lista de resultados con funcionalidad de doble clic
        self.results = QListWidget()
        self.results.setStyleSheet(f"""
            QListWidget {{
                background-color: {AppleColors.NOTES_LIST.name()};
                border: 1px solid {AppleColors.SEPARATOR_LIGHT.name()};
                border-radius: 12px;
                outline: none;
                font-family: '.AppleSystemUIFont';
                font-size: 14px;
                padding: 8px;
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
        
        # NUEVO: Conectar doble clic para abrir nota
        self.results.itemDoubleClicked.connect(self._open_note_from_search)
        
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

    def _open_note_from_search(self, item):
        """Abre nota seleccionada desde resultados de búsqueda"""
        try:
            # Obtener note_id del item
            note_id = item.data(Qt.UserRole)
            
            if not note_id:
                return
            
            # Verificar que la nota existe
            note = self.db.get_note(note_id)
            if not note:
                QMessageBox.warning(self, "Nota no encontrada", 
                                  "La nota seleccionada no existe o fue eliminada.")
                return
            
            # Usar referencia directa al main_window
            if self.main_window:
                # Cambiar al tab de notas (índice 1)
                self.main_window.stack.setCurrentIndex(1)
                
                # Cargar nota con pequeño delay para asegurar que el tab se carga
                QTimer.singleShot(100, lambda: self._load_note_in_editor(note_id))
            else:
                QMessageBox.warning(self, "Error de navegación", 
                                  "No se puede navegar a la nota. Intenta desde el tab de notas.")
        
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo abrir la nota: {e}")

    def _load_note_in_editor(self, note_id):
        """Carga nota específica en el editor"""
        try:
            if (self.main_window and 
                hasattr(self.main_window, 'notes_view') and 
                hasattr(self.main_window.notes_view, 'note_editor')):
                
                success = self.main_window.notes_view.note_editor.load_note(note_id)
                if not success:
                    QMessageBox.warning(self.main_window, "Error", 
                                      "No se pudo cargar la nota seleccionada.")
        except Exception as e:
            print(f"Error cargando nota en editor: {e}")
            
class AnalyzeTab(QWidget):
    """Tab de análisis con RAG estilo Apple - MEJORADO con streaming"""
    
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
            # Fallback si pyperclip no está disponible
            clipboard = QApplication.clipboard()
            clipboard.setText(text)
            QMessageBox.information(self, "Copiado", "Respuesta copiada al portapapeles")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"No se pudo copiar: {e}")

    def ask(self):
        if not self.vector:
            QMessageBox.information(self, APP_NAME, 
                                "El análisis inteligente está deshabilitado.\n"
                                "Configura tu OpenAI API key en Ajustes.")
            return
            
        question = self.q_edit.text().strip()
        if not question:
            return
        
        # Preparar UI para análisis asíncrono
        self._start_analysis_ui()
        
        # Crear y lanzar worker thread
        self.analysis_worker = AnalysisWorker(
            self.db, self.vector, self.ai, question, self.k_spin.value()
        )
        
        # Conectar señales (incluyendo nueva señal de streaming)
        self.analysis_worker.analysis_finished.connect(self._on_analysis_finished)
        self.analysis_worker.analysis_error.connect(self._on_analysis_error)
        self.analysis_worker.analysis_progress.connect(self._on_analysis_progress)
        self.analysis_worker.analysis_streaming.connect(self._on_analysis_streaming)
        
        # Iniciar análisis en hilo separado
        self.analysis_worker.start()

    def _start_analysis_ui(self):
        """Configura UI para estado de análisis"""
        self.btn_ask.setText("Analizando...")
        self.btn_ask.setEnabled(False)
        self.answer.clear()
        self.answer.setPlaceholderText("🔄 Analizando documentos...")

    def _on_analysis_progress(self, message: str):
        """Actualiza progreso en UI"""
        self.answer.setPlaceholderText(f"🔄 {message}")

    def _on_analysis_streaming(self, chunk: str):
        """Maneja chunks de streaming en tiempo real"""
        current_text = self.answer.toPlainText()
        
        # Si es el primer chunk, limpiar placeholder
        if "🔄" in current_text or "Las respuestas del análisis aparecerán aquí..." in current_text:
            self.answer.clear()
            current_text = ""
        
        # Añadir nuevo chunk
        self.answer.setPlainText(current_text + chunk)
        
        # Mover cursor al final para que se vea el nuevo texto
        cursor = self.answer.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.answer.setTextCursor(cursor)
        
        # Asegurar que el área de texto se actualice visualmente
        self.answer.update()
        QCoreApplication.processEvents()

    def _on_analysis_finished(self, response: str):
        """Maneja finalización exitosa"""
        # Solo actualizar con fuentes si no se recibió por streaming
        current_text = self.answer.toPlainText()
        if "📚 Fuentes consultadas:" not in current_text:
            # Si no hay streaming text, usar respuesta completa
            if not current_text or current_text.startswith("🔄"):
                self.answer.setPlainText(response)
            else:
                # Hay streaming text, solo añadir fuentes
                if "📚 Fuentes consultadas:" in response:
                    sources_part = response[response.find("📚 Fuentes consultadas:"):]
                    self.answer.setPlainText(current_text + "\n\n" + sources_part)
        
        self._reset_analysis_ui()

    def _on_analysis_error(self, error: str):
        """Maneja errores de análisis"""
        self.answer.clear()
        self.answer.setPlaceholderText("❌ Error en el análisis")
        QMessageBox.critical(self, "Error", error)
        self._reset_analysis_ui()

    def _reset_analysis_ui(self):
        """Resetea UI después de análisis"""
        self.btn_ask.setText("🤖 Analizar")
        self.btn_ask.setEnabled(True)
        
        # Limpiar worker
        if hasattr(self, 'analysis_worker'):
            self.analysis_worker.deleteLater()
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
        
        # Indicadores en línea (mantener igual)
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
        
        # Header de notas con acciones (mantener igual)
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
        
        new_note_action = AppleButton("+ Nueva nota", "success")
        new_note_action.clicked.connect(lambda: self._switch_tab(1))
        
        notes_header.addWidget(recent_label)
        notes_header.addStretch()
        notes_header.addWidget(new_note_action)
        layout.addLayout(notes_header)
        
        # Lista de notas CON FONDO DIFERENTE
        self.recent_notes_list = QListWidget()
        self.recent_notes_list.setItemDelegate(NotesListDelegate())
        self.recent_notes_list.setStyleSheet(f"""
            QListWidget {{
                background-color: {AppleColors.NOTES_LIST.name()};
                border: 1px solid {AppleColors.SEPARATOR_LIGHT.name()};
                border-radius: 12px;
                outline: none;
                font-family: '.AppleSystemUIFont';
                font-size: 14px;
                padding: 8px;
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
                font-size: 18px;
            }}
            QListWidget::item {{
                color: {AppleColors.PRIMARY.name()};
                padding: 10px 16px;
                border-radius: 8px;
                margin: 3px 0px;
                font-size: 20px;
                font-weight: 700;
            }}
            QListWidget::item:hover {{
                background-color: rgba(255,255,255,0.06);
            }}
            QListWidget::item:selected {{
                background-color: {AppleColors.BLUE.name()};
                color: white;
                font-size: 24px;
                font-weight: 700;
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
        self.search_tab = SearchTab(self.settings, self.db, self.vector, self.ai, self)
        self.analyze_tab = AnalyzeTab(self.settings, self.db, self.vector, self.ai)
        self.summary_tab = SummaryTab(self.settings, self.db, self.ai, self.vector)
        self.categories_tab = CategoriesTab(self.settings, self.db, self)
        self.settings_tab = SettingsTab(self.settings)
        
        # NUEVO: Conectar señales entre tabs para sincronización
        self._connect_cross_tab_signals()
        
        # Agregar al stack
        for widget in [self.dashboard_tab, self.notes_view, self.transcribe_tab, 
                    self.search_tab, self.analyze_tab, self.summary_tab,
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

    def _connect_cross_tab_signals(self):
        """Conecta señales entre tabs para sincronización"""
        # Cuando se guarda desde editor de notas
        self.notes_view.note_editor.note_saved.connect(self._on_note_saved_anywhere)
        
        # NUEVO: Cuando se guarda desde transcripción
        self.transcribe_tab.note_saved.connect(self._on_note_saved_anywhere)
        
        # Cuando cambian categorías
        self.categories_tab.categories_changed.connect(self._on_categories_changed)

    def _on_note_saved_anywhere(self):
        """NUEVO: Maneja guardado de notas desde cualquier tab"""
        # Refrescar dashboard
        if hasattr(self.dashboard_tab, '_refresh_stats'):
            self.dashboard_tab._refresh_stats()
        
        # Refrescar lista de notas
        if hasattr(self.notes_view, '_load_data'):
            self.notes_view._load_data()
        
        # Limpiar resultados de búsqueda para forzar nueva búsqueda
        if hasattr(self.search_tab, 'results'):
            self.search_tab.results.clear()
            self.search_tab.results_count.setText("0 resultados")

    def _on_categories_changed(self):
        """NUEVO: Maneja cambios en categorías"""
        # Refrescar combos de categorías en transcripción
        if hasattr(self.transcribe_tab, 'category_combo'):
            current_text = self.transcribe_tab.category_combo.currentText()
            self.transcribe_tab.category_combo.clear()
            categories = self.db.list_categories()
            for cat in categories:
                self.transcribe_tab.category_combo.addItem(cat)
            index = self.transcribe_tab.category_combo.findText(current_text)
            if index >= 0:
                self.transcribe_tab.category_combo.setCurrentIndex(index)
        
        # Refrescar editor de notas
        if hasattr(self.notes_view, 'note_editor'):
            self.notes_view.note_editor.refresh_categories()
    def _setup_services(self):
        db_path = os.path.join(self.settings.data_dir, "notes.db")
        self.db = NotesDB(db_path)
        self.ai = AIService(self.settings)
        
        self.vector = None
        try:
            if self.settings.openai_api_key or os.environ.get("OPENAI_API_KEY"):
                self.vector = VectorIndex(self.settings, self.ai)
                # FORZAR VERIFICACIÓN DE INICIALIZACIÓN
                test_count = self.vector.col.count()
                print(f"Vector store inicializado correctamente con {test_count} chunks")
            else:
                print("Vector store deshabilitado: No hay API key")
        except Exception as e:
            print(f"Vector store deshabilitado: {e}")
            self.vector = None
    

    
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
        
        # NUEVO: Actualizar notas cuando se selecciona
        elif index == 1 and hasattr(self, "notes_view"):
            self.notes_view._load_data()
        
        # Actualizar búsqueda cuando se selecciona
        elif index == 3 and hasattr(self, "search_tab"):
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