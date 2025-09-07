import os
import json
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from openai import OpenAI
from .settings import Settings

# Import OpenAI client from v1.x SDK. If not installed, runtime will throw.

@dataclass
class RetrievalResult:
    """Container for semantic search results."""

    note_id: int
    title: str
    snippet: str
    score: float


class AIService:
    """Wraps OpenAI API calls for embeddings, classification, RAG, and transcription."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: Optional[OpenAI] = None

    @property
    def client(self) -> OpenAI:
        """Lazily instantiate an OpenAI client."""
        if self._client is None:
            if OpenAI is None:
                raise RuntimeError("El paquete 'openai' no está instalado. Instálalo con: pip install openai")
            api_key = self.settings.openai_api_key or os.environ.get("OPENAI_API_KEY", "")
            if not api_key:
                raise RuntimeError("Falta OPENAI_API_KEY. Configura tu clave en 'Ajustes'.")
            self._client = OpenAI(api_key=api_key)
        return self._client

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Get embeddings for a list of texts using the configured model."""
        resp = self.client.embeddings.create(input=texts, model=self.settings.embedding_model)
        return [d.embedding for d in resp.data]

    def classify(self, content: str) -> Tuple[str, List[str]]:
        """Suggest a category and tags for the given content using the chat model."""
        system = (
            "Eres un asistente que clasifica notas personales. "
            "Devuelve un JSON con 'category' (una palabra o frase corta) y 'tags' (lista de 3-7 palabras). "
            "No agregues nada más."
        )
        user = f"Contenido:\n{content}\n\nResponde solo con JSON."
        resp = self.client.chat.completions.create(
            model=self.settings.chat_model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.2,
        )
        txt = resp.choices[0].message.content.strip()
        try:
            data = json.loads(txt)
            category = str(data.get("category", "General"))
            tags = [str(t) for t in data.get("tags", [])][:7]
            return category, tags
        except Exception:
            return "General", []

    def answer_with_context(self, question: str, contexts: List[Dict], extended_analysis: bool = False) -> str:
        """Use RAG to answer a question given context notes."""
        if extended_analysis:
            system = (
                "Eres una secretaria IA especializada en análisis profundo de información. "
                "Proporciona análisis completos y detallados usando SOLO el contexto proporcionado. "
                "IMPORTANTE: NO uses formato Markdown. Escribe texto plano con estructura clara:\n"
                "- Usa MAYÚSCULAS para títulos principales\n"
                "- Usa números para secciones (1., 2., etc.)\n"
                "- Usa guiones (-) para sublistas\n"
                "- Usa saltos de línea dobles para separar secciones\n"
                "- Resalta puntos importantes con MAYÚSCULAS o repetición\n\n"
                "Estructura requerida:\n"
                "ANÁLISIS INTEGRAL\n\n"
                "1. RESUMEN EJECUTIVO\n\n"
                "2. ANÁLISIS DETALLADO\n\n"
                "3. PUNTOS CLAVE IDENTIFICADOS\n\n"
            )
            max_tokens = 2500
        else:
            system = (
                "Eres una secretaria IA que contesta SOLO usando el contexto proporcionado. "
                "Responde en texto plano, sin formato especial. "
                "Si la respuesta no está en el contexto, admite que no está y sugiere cómo capturar esa información."
            )
            max_tokens = 1000
        
        ctx_text = "\n\n".join([f"Título: {c['title']}\nContenido:\n{c['content']}" for c in contexts])
        user = f"Pregunta: {question}\n\nContexto:\n{ctx_text}"
        
        resp = self.client.chat.completions.create(
            model=self.settings.chat_model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.1,
            max_tokens=max_tokens
        )
        
        return resp.choices[0].message.content.strip()

    def transcribe(self, wav_path: str) -> str:
        """Transcribe an audio file using Whisper API."""
        with open(wav_path, "rb") as f:
            tr = self.client.audio.transcriptions.create(
                model=self.settings.transcription_model, file=f
            )
        text = getattr(tr, "text", None) or getattr(tr, "data", None) or ""
        if isinstance(text, str):
            return text
        return str(text)