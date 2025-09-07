import queue
import threading
import time
import os
from typing import Optional, Callable, Dict, Any
from datetime import datetime
import numpy as np

import sounddevice as sd
import soundfile as sf


class SimpleRecorder:
    """Grabador simple solo para respaldo opcional"""

    def __init__(self, samplerate: int = 16000, channels: int = 1):
        self.samplerate = samplerate
        self.channels = channels
        self._q = queue.Queue()
        self._recording = False
        self._thread = None
        self._outfile = None
        self._stream = None

    def start(self, outfile: str) -> Dict[str, Any]:
        """Inicia grabación simple"""
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
        
        # Stream de audio
        try:
            self._stream = sd.RawInputStream(
                samplerate=self.samplerate,
                channels=self.channels,
                dtype="int16",
                callback=self._callback,
                blocksize=1024
            )
            self._stream.start()
            return {'status': 'recording', 'file': outfile}
        except Exception as e:
            self._recording = False
            raise RuntimeError(f"Error iniciando grabación: {e}")

    def _callback(self, indata, frames, time_info, status):
        """Callback simple"""
        if self._recording:
            self._q.put(bytes(indata))

    def _writer_thread(self):
        """Thread de escritura simple"""
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
                    except Exception:
                        break
        except Exception as e:
            print(f"Error escribiendo audio: {e}")

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
            self._thread.join(timeout=2.0)
            self._thread = None
        
        return {'file': self._outfile} if self._outfile else None

