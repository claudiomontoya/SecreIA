import queue
import threading
import time
import os
from typing import Optional, Callable, Dict, Any
from datetime import datetime
import numpy as np
import sounddevice as sd
import soundfile as sf

# Configuración de audio optimizada para Windows
WINDOWS_AUDIO_CONFIG = {
    'samplerate': 44100,  # Windows prefiere 44.1kHz
    'channels': 1,
    'dtype': 'int16',
    'blocksize': 2048,    # Buffer más grande para Windows
    'latency': 'low',     # Baja latencia
}

class AudioQualityAnalyzer:
    """Analiza calidad de audio en tiempo real para Windows"""
    
    def __init__(self):
        self.volume_history = []
        self.silence_threshold = 0.01
        self.noise_threshold = 0.8
    
    def analyze_chunk(self, audio_data: np.ndarray) -> Dict[str, Any]:
        """Analiza un chunk de audio y devuelve métricas básicas"""
        if len(audio_data) == 0:
            return {"volume": 0, "quality": "silent"}
        
        # Convertir a float si es necesario
        if audio_data.dtype != np.float32:
            audio_data = audio_data.astype(np.float32) / 32768.0
        
        # Calcular volumen RMS
        rms = np.sqrt(np.mean(audio_data ** 2))
        self.volume_history.append(rms)
        
        # Mantener solo últimos 20 chunks
        if len(self.volume_history) > 20:
            self.volume_history.pop(0)
        
        # Detectar calidad básica
        if rms < self.silence_threshold:
            quality = "silent"
        elif rms > self.noise_threshold:
            quality = "loud"
        else:
            quality = "good"
        
        return {
            "volume": float(rms),
            "quality": quality
        }

class WindowsRecorder:
    """Grabador optimizado para Windows"""

    def __init__(self):
        self.samplerate = WINDOWS_AUDIO_CONFIG['samplerate']
        self.channels = WINDOWS_AUDIO_CONFIG['channels']
        self.blocksize = WINDOWS_AUDIO_CONFIG['blocksize']
        self._q = queue.Queue()
        self._recording = False
        self._thread = None
        self._outfile = None
        self._stream = None

    def start(self, outfile: str) -> Dict[str, Any]:
        """Inicia grabación optimizada para Windows"""
        outdir = os.path.dirname(outfile)
        if outdir and not os.path.exists(outdir):
            os.makedirs(outdir, exist_ok=True)
        
        self._outfile = outfile
        self._recording = True
        
        # Limpiar queue
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        
        # Thread de escritura
        self._thread = threading.Thread(target=self._writer_thread, daemon=True)
        self._thread.start()
        
        # Stream de audio con configuración Windows
        try:
            self._stream = sd.RawInputStream(
                samplerate=self.samplerate,
                channels=self.channels,
                dtype=WINDOWS_AUDIO_CONFIG['dtype'],
                callback=self._callback,
                blocksize=self.blocksize,
                latency=WINDOWS_AUDIO_CONFIG['latency']
            )
            self._stream.start()
            return {'status': 'recording', 'file': outfile}
        except Exception as e:
            self._recording = False
            raise RuntimeError(f"Error iniciando grabación en Windows: {e}")

    def _callback(self, indata, frames, time_info, status):
        """Callback optimizado para Windows"""
        if self._recording and status is None:  # Solo procesar si no hay errores
            self._q.put(bytes(indata))

    def _writer_thread(self):
        """Thread de escritura optimizado para Windows"""
        if not self._outfile:
            return
            
        try:
            with sf.SoundFile(
                self._outfile,
                mode="w",
                samplerate=self.samplerate,
                channels=self.channels,
                subtype="PCM_16",
            ) as f:
                while self._recording or not self._q.empty():
                    try:
                        data = self._q.get(timeout=0.1)
                        f.buffer_write(data, dtype="int16")
                    except queue.Empty:
                        continue
                    except Exception as e:
                        print(f"Error escribiendo audio en Windows: {e}")
                        break
        except Exception as e:
            print(f"Error configurando archivo de audio: {e}")

    def stop(self):
        """Detiene grabación"""
        if not self._recording:
            return None
        
        self._recording = False
        
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        
        if self._thread:
            self._thread.join(timeout=3.0)  # Más tiempo para Windows
            self._thread = None
        
        return {'file': self._outfile} if self._outfile else None

# Alias para compatibilidad
SimpleRecorder = WindowsRecorder
AdvancedRecorder = WindowsRecorder
Recorder = WindowsRecorder