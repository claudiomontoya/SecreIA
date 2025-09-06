# app/vectorstore_improved.py
import os
import re
import uuid
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional, Any
from datetime import datetime
import hashlib
from .settings import Settings
from .ai import AIService

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("CHROMADB_TELEMETRY_IMPLEMENTATION", "noop")
os.environ.setdefault("CHROMADB_DISABLE_TELEMETRY", "1")

try:
    import chromadb
    from chromadb.utils import embedding_functions
    CHROMADB_AVAILABLE = True
except ImportError:
    chromadb = None
    embedding_functions = None
    CHROMADB_AVAILABLE = False



@dataclass
class MeetingContext:
    """Contexto específico para reuniones"""
    meeting_id: Optional[str] = None
    attendees: List[str] = None
    topics: List[str] = None
    action_items: List[str] = None
    decisions: List[str] = None
    timestamp: Optional[str] = None


@dataclass
class ChunkRef:
    """Descriptor for a chunk of a note in the vector store."""
    chunk_id: str
    note_id: int
    start: int
    end: int
    chunk_type: str  # 'paragraph', 'sentence', 'title'
    semantic_score: float = 0.0

class SmartChunker:
    """Inteligente chunking que preserva estructura semántica"""
    
    def __init__(self, max_chars: int = 1000, overlap: int = 100):
        self.max_chars = max_chars
        self.overlap = overlap
    
    def chunk_text(self, text: str, title: str = "") -> List[Tuple[int, int, str, str, Dict[str, Any]]]:
        """
        Divide texto inteligentemente preservando estructura semántica
        Returns: List[(start, end, chunk_text, chunk_type, metadata)]
        """
        chunks = []
        
        # Chunk del título si existe
        if title.strip():
            chunks.append((0, 0, title, "title", {
                "importance": 1.0,
                "is_title": True,
                "word_count": len(title.split())
            }))
        
        # Normalizar texto
        text = text.strip()
        if not text:
            return chunks
        
        # Dividir por párrafos primero
        paragraphs = self._split_paragraphs(text)
        current_pos = 0
        
        for para_text in paragraphs:
            if not para_text.strip():
                current_pos += len(para_text)
                continue
                
            para_start = current_pos
            para_end = current_pos + len(para_text)
            
            # Si el párrafo es pequeño, mantenerlo completo
            if len(para_text) <= self.max_chars:
                metadata = self._extract_metadata(para_text, para_start, "paragraph")
                chunks.append((para_start, para_end, para_text, "paragraph", metadata))
            else:
                # Dividir párrafo largo por oraciones
                sent_chunks = self._chunk_by_sentences(para_text, para_start)
                chunks.extend(sent_chunks)
            
            current_pos = para_end
        
        # Post-procesamiento: combinar chunks muy pequeños
        chunks = self._merge_small_chunks(chunks)
        
        return chunks
    
    def _split_paragraphs(self, text: str) -> List[str]:
        """Divide texto en párrafos inteligentemente"""
        # Dividir por doble salto de línea o cambios significativos
        paragraphs = re.split(r'\n\s*\n', text)
        return [p.strip() for p in paragraphs if p.strip()]
    
    def _chunk_by_sentences(self, text: str, base_offset: int) -> List[Tuple[int, int, str, str, Dict[str, Any]]]:
        """Divide párrafo largo por oraciones"""
        chunks = []
        
        # Dividir por oraciones usando regex más sofisticado
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)
        
        current_chunk = ""
        chunk_start = base_offset
        chunk_sentences = []
        
        for i, sentence in enumerate(sentences):
            sentence = sentence.strip()
            if not sentence:
                continue
                
            # Verificar si agregar esta oración excedería el límite
            potential_chunk = current_chunk + (" " if current_chunk else "") + sentence
            
            if len(potential_chunk) > self.max_chars and current_chunk:
                # Guardar chunk actual
                metadata = self._extract_metadata(current_chunk, chunk_start, "sentence_group")
                chunks.append((
                    chunk_start, 
                    chunk_start + len(current_chunk),
                    current_chunk,
                    "sentence_group",
                    metadata
                ))
                
                # Iniciar nuevo chunk con overlap
                overlap_text = self._get_overlap_text(chunk_sentences)
                current_chunk = overlap_text + sentence
                chunk_start = chunk_start + len(current_chunk) - len(sentence) - len(overlap_text)
                chunk_sentences = [sentence]
            else:
                current_chunk = potential_chunk
                chunk_sentences.append(sentence)
        
        # Agregar último chunk si existe
        if current_chunk:
            metadata = self._extract_metadata(current_chunk, chunk_start, "sentence_group")
            chunks.append((
                chunk_start,
                chunk_start + len(current_chunk),
                current_chunk,
                "sentence_group",
                metadata
            ))
        
        return chunks
    
    def _get_overlap_text(self, sentences: List[str]) -> str:
        """Obtiene texto de overlap inteligente"""
        if not sentences:
            return ""
        
        # Tomar última oración para overlap si es corta
        last_sentence = sentences[-1]
        if len(last_sentence) <= self.overlap:
            return last_sentence + " "
        
        # Tomar fragmento final
        return last_sentence[-self.overlap:] + " "
    
    def _merge_small_chunks(self, chunks: List[Tuple]) -> List[Tuple]:
        """Combina chunks muy pequeños con vecinos"""
        if len(chunks) <= 1:
            return chunks
        
        merged = []
        i = 0
        
        while i < len(chunks):
            current = chunks[i]
            current_text = current[2]
            
            # Si el chunk es muy pequeño, intentar combinar
            if len(current_text) < 200 and i < len(chunks) - 1:
                next_chunk = chunks[i + 1]
                combined_text = current_text + " " + next_chunk[2]
                
                if len(combined_text) <= self.max_chars:
                    # Combinar chunks
                    combined_metadata = current[4].copy()
                    combined_metadata.update(next_chunk[4])
                    combined_metadata["combined"] = True
                    
                    merged.append((
                        current[0],  # start del primer chunk
                        next_chunk[1],  # end del segundo chunk
                        combined_text,
                        "combined",
                        combined_metadata
                    ))
                    i += 2  # Saltar ambos chunks
                    continue
            
            merged.append(current)
            i += 1
        
        return merged
    
    def _extract_metadata(self, text: str, position: int, chunk_type: str) -> Dict[str, Any]:
        """Extrae metadata útil del texto"""
        words = text.split()
        sentences = re.split(r'[.!?]+', text)
        
        # Detectar elementos importantes
        has_numbers = bool(re.search(r'\d+', text))
        has_dates = bool(re.search(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b', text))
        has_emails = bool(re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', text))
        has_phones = bool(re.search(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', text))
        has_urls = bool(re.search(r'http[s]?://\S+', text))
        
        # Calcular importancia basada en contenido
        importance = 0.5  # base
        if has_dates: importance += 0.2
        if has_numbers: importance += 0.1
        if has_emails or has_phones: importance += 0.3
        if has_urls: importance += 0.1
        if len(words) > 50: importance += 0.1  # chunks más largos pueden ser más importantes
        
        return {
            "word_count": len(words),
            "sentence_count": len([s for s in sentences if s.strip()]),
            "char_count": len(text),
            "position": position,
            "has_numbers": has_numbers,
            "has_dates": has_dates,
            "has_contacts": has_emails or has_phones,
            "has_urls": has_urls,
            "importance": min(importance, 1.0),
            "chunk_type": chunk_type,
            "created_at": datetime.utcnow().isoformat()
        }

class AdvancedChunker:
    """Chunking optimizado para transcripciones de reuniones"""
    
    def __init__(self, max_chars: int = 600, overlap: int = 50):
        self.max_chars = max_chars
        self.overlap = overlap
        # Patrones para detectar elementos importantes en reuniones
        self.meeting_patterns = {
            'action_item': re.compile(r'\b(acción|tarea|pendiente|asignar|responsable|deadline)\b', re.IGNORECASE),
            'decision': re.compile(r'\b(decidir|acordar|resolver|definir|conclusión)\b', re.IGNORECASE),
            'question': re.compile(r'\?|¿.*?\?'),
            'names': re.compile(r'\b[A-Z][a-z]+\s+[A-Z][a-z]+\b'),  # Detectar nombres propios
            'dates': re.compile(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{1,2}\s+de\s+\w+\b'),
            'numbers': re.compile(r'\b\d+([.,]\d+)?\s*(%|pesos|dolares|euros)\b'),
            'times': re.compile(r'\b\d{1,2}:\d{2}\b')
        }
    
    def chunk_transcription(self, text: str, title: str = "", context: MeetingContext = None) -> List[Tuple[int, int, str, str, Dict[str, Any]]]:
        """Chunking especializado para transcripciones de reuniones"""
        chunks = []
        
        # Chunk del título con metadata de reunión
        if title.strip():
            title_metadata = self._extract_meeting_metadata(title, "title", context)
            chunks.append((0, 0, title, "title", title_metadata))
        
        # Normalizar y detectar speakers
        text = self._preprocess_transcription(text)
        
        # Dividir por speakers o párrafos temporales
        segments = self._split_by_speakers_or_time(text)
        current_pos = 0
        
        for segment_text, segment_type in segments:
            if not segment_text.strip():
                current_pos += len(segment_text)
                continue
                
            segment_start = current_pos
            segment_end = current_pos + len(segment_text)
            
            # Chunking adaptativo según contenido
            if len(segment_text) <= self.max_chars:
                metadata = self._extract_meeting_metadata(segment_text, segment_type, context, segment_start)
                chunks.append((segment_start, segment_end, segment_text, segment_type, metadata))
            else:
                # Subdividir segmentos largos preservando contexto
                sub_chunks = self._smart_split_segment(segment_text, segment_start, segment_type, context)
                chunks.extend(sub_chunks)
            
            current_pos = segment_end
        
        # Post-procesamiento: enriquecer con contexto inter-chunk
        chunks = self._enrich_with_conversation_context(chunks)
        
        return chunks
    
    def _preprocess_transcription(self, text: str) -> str:
        """Preprocesa transcripción para mejor chunking"""
        # Normalizar espacios
        text = re.sub(r'\s+', ' ', text)
        
        # Detectar y normalizar speakers
        text = re.sub(r'^([A-Z][a-z]+):\s*', r'\n\1: ', text, flags=re.MULTILINE)
        
        # Normalizar timestamps si existen
        text = re.sub(r'\[(\d{2}:\d{2})\]', r'\n[\1] ', text)
        
        return text.strip()
    
    def _split_by_speakers_or_time(self, text: str) -> List[Tuple[str, str]]:
        """Divide por speakers o marcadores temporales"""
        segments = []
        
        # Buscar patrones de speaker o tiempo
        speaker_pattern = r'^([A-Z][a-z]+):\s*(.+?)(?=^[A-Z][a-z]+:|$)'
        time_pattern = r'\[(\d{2}:\d{2})\]\s*(.+?)(?=\[\d{2}:\d{2}\]|$)'
        
        # Intentar división por speakers primero
        speaker_matches = re.finditer(speaker_pattern, text, re.MULTILINE | re.DOTALL)
        
        if list(re.finditer(speaker_pattern, text, re.MULTILINE | re.DOTALL)):
            for match in re.finditer(speaker_pattern, text, re.MULTILINE | re.DOTALL):
                speaker = match.group(1)
                content = match.group(2).strip()
                segments.append((f"{speaker}: {content}", "speaker_segment"))
        else:
            # Dividir por tiempo o párrafos
            time_matches = re.finditer(time_pattern, text, re.DOTALL)
            
            if list(re.finditer(time_pattern, text, re.DOTALL)):
                for match in re.finditer(time_pattern, text, re.DOTALL):
                    timestamp = match.group(1)
                    content = match.group(2).strip()
                    segments.append((f"[{timestamp}] {content}", "time_segment"))
            else:
                # División por párrafos como fallback
                paragraphs = text.split('\n\n')
                for para in paragraphs:
                    if para.strip():
                        segments.append((para.strip(), "paragraph"))
        
        return segments
    
    def _extract_meeting_metadata(self, text: str, chunk_type: str, context: MeetingContext = None, position: int = 0) -> Dict[str, Any]:
        """Extrae metadata específica para reuniones"""
        words = text.split()
        
        # Detectar elementos importantes usando patrones
        detected_elements = {}
        for element_type, pattern in self.meeting_patterns.items():
            matches = pattern.findall(text)
            detected_elements[element_type] = len(matches)
        
        # Detectar speaker si está presente
        speaker = None
        speaker_match = re.match(r'^([A-Z][a-z]+):', text)
        if speaker_match:
            speaker = speaker_match.group(1)
        
        # Detectar timestamp
        timestamp_match = re.search(r'\[(\d{2}:\d{2})\]', text)
        timestamp = timestamp_match.group(1) if timestamp_match else None
        
        # Calcular importancia basada en contenido de reunión
        importance = 0.5  # base
        
        # Aumentar importancia por elementos de reunión
        if detected_elements['action_item'] > 0: importance += 0.3
        if detected_elements['decision'] > 0: importance += 0.3
        if detected_elements['question'] > 0: importance += 0.2
        if detected_elements['names'] > 0: importance += 0.1
        if speaker: importance += 0.1
        if timestamp: importance += 0.1
        
        # Metadata rica para búsquedas contextuales
        metadata = {
            "word_count": len(words),
            "char_count": len(text),
            "position": position,
            "chunk_type": chunk_type,
            "speaker": speaker,
            "timestamp": timestamp,
            "importance": min(importance, 1.0),
            "action_items": detected_elements['action_item'],
            "decisions": detected_elements['decision'],
            "questions": detected_elements['question'],
            "names_mentioned": detected_elements['names'],
            "dates_mentioned": detected_elements['dates'],
            "numbers_mentioned": detected_elements['numbers'],
            "times_mentioned": detected_elements['times'],
            "created_at": datetime.utcnow().isoformat(),
            "content_hash": hashlib.md5(text.encode()).hexdigest()[:8]
        }
        
        # Agregar contexto de reunión si está disponible
        if context:
            metadata.update({
                "meeting_id": context.meeting_id,
                "meeting_timestamp": context.timestamp,
                "attendees": ",".join(context.attendees or []),
                "topics": ",".join(context.topics or [])
            })
        
        return metadata
    
class VectorIndex:
    """ChromaDB mejorado con chunking inteligente y búsqueda avanzada"""

    def __init__(self, settings: Settings, ai: AIService) -> None:
        if not CHROMADB_AVAILABLE:
            raise RuntimeError("ChromaDB no está disponible. La búsqueda semántica estará deshabilitada.")
        
        self.settings = settings
        self.ai = ai
        self.chunker = SmartChunker(max_chars=800, overlap=100)

        # Crear directorio si no existe
        chroma_path = os.path.join(settings.data_dir, "chroma")
        os.makedirs(chroma_path, exist_ok=True)

        try:
            self.client = chromadb.PersistentClient(path=chroma_path)

            # OpenAI embeddings wrapper
            api_key = self.ai.settings.openai_api_key or os.environ.get("OPENAI_API_KEY", "")
            if not api_key:
                raise RuntimeError("Se requiere OPENAI_API_KEY para embeddings")

            self.embedding_fn = embedding_functions.OpenAIEmbeddingFunction(
                api_key=api_key,
                model_name=self.settings.embedding_model,
            )

            self.col = self.client.get_or_create_collection(
                name="notes_v2",  # Nueva versión con metadata mejorada
                embedding_function=self.embedding_fn,
            )
        except Exception as e:
            if "no such column" in str(e):
                # Base de datos incompatible
                import shutil
                try:
                    shutil.rmtree(chroma_path)
                    os.makedirs(chroma_path, exist_ok=True)
                    self.client = chromadb.PersistentClient(path=chroma_path)
                    self.col = self.client.get_or_create_collection(
                        name="notes_v2",
                        embedding_function=self.embedding_fn,
                    )
                except Exception as e2:
                    raise RuntimeError(f"Error inicializando ChromaDB después de limpiar: {e2}")
            else:
                raise RuntimeError(f"Error inicializando ChromaDB: {e}")

    def index_note(self, note_id: int, title: str, content: str, category: str = "", tags: List[str] = None, source: str = "manual") -> None:
        """Indexa nota con chunking inteligente y metadata rica"""
        if not content.strip() and not title.strip():
            return
        
        # Limpiar chunks existentes
        self.delete_note_chunks(note_id)
        
        # Combinar título y contenido para chunking
        full_text = content
        chunks = self.chunker.chunk_text(full_text, title)

        if not chunks:
            return

        ids = []
        metadatas = []
        documents = []
        
        for start, end, text, chunk_type, chunk_metadata in chunks:
            if not text.strip():
                continue
                
            chunk_id = str(uuid.uuid4())
            ids.append(chunk_id)
            
            # Metadata rica para mejores búsquedas
            metadata = {
                "note_id": note_id,
                "title": title,
                "category": category,
                "tags": ",".join(tags or []),
                "source": source,
                "start": start,
                "end": end,
                "chunk_type": chunk_type,
                **chunk_metadata  # Incluir metadata del chunk
            }
            metadatas.append(metadata)
            
            # Preparar documento con contexto
            doc_text = text
            if chunk_type != "title" and title:
                doc_text = f"Título: {title}\n\n{text}"
            
            documents.append(doc_text)

        if ids:
            try:
                self.col.add(documents=documents, metadatas=metadatas, ids=ids)
            except Exception as e:
                raise RuntimeError(f"Error indexando nota {note_id}: {e}")

    def search(self, query: str, top_k: int = 5, filters: Dict[str, Any] = None) -> List[Dict]:
        """Búsqueda semántica avanzada con filtros y re-ranking"""
        if not query.strip():
            return []
        
        # Expandir consulta para mejor matching
        expanded_query = self._expand_query(query)
        
        try:
            # Buscar más resultados para re-ranking
            search_k = min(top_k * 3, 50)
            
            where_filter = {}
            if filters:
                where_filter.update(filters)
            
            res = self.col.query(
                query_texts=[expanded_query],
                n_results=search_k,
                include=["metadatas", "documents", "distances"],
                where=where_filter if where_filter else None
            )
        except Exception as e:
            raise RuntimeError(f"Error en búsqueda semántica: {e}")

        # Procesar y re-rankear resultados
        results = self._process_search_results(res, query, top_k)
        return results
    
    def _expand_query(self, query: str) -> str:
        """Expande la consulta para mejor matching"""
        # Por ahora, simplemente limpiar y normalizar
        # Futuro: usar sinónimos, corrección ortográfica
        return query.strip()
    
    def _process_search_results(self, res: Dict, original_query: str, top_k: int) -> List[Dict]:
        """Procesa y re-rankea resultados de búsqueda"""
        results = []
        if not res or not res.get("documents") or not res["documents"][0]:
            return results

        docs = res["documents"][0]
        metas = res["metadatas"][0]
        dists = res.get("distances", [[0] * len(docs)])[0]
        
        # Agrupar por nota para evitar duplicados
        note_results = {}
        
        for doc, meta, dist in zip(docs, metas, dists):
            note_id = meta["note_id"]
            
            # Calcular score combinado
            semantic_score = 1 - float(dist)  # Convertir distancia a similaridad
            importance_score = meta.get("importance", 0.5)
            
            # Bonus por tipo de chunk
            type_bonus = {
                "title": 0.3,
                "paragraph": 0.1,
                "sentence_group": 0.0,
                "combined": 0.05
            }.get(meta.get("chunk_type", ""), 0.0)
            
            combined_score = semantic_score + (importance_score * 0.2) + type_bonus
            
            # Mantener mejor resultado por nota
            if note_id not in note_results or combined_score > note_results[note_id]["score"]:
                # Crear snippet inteligente
                snippet = self._create_smart_snippet(doc, original_query)
                
                note_results[note_id] = {
                    "note_id": note_id,
                    "title": meta["title"],
                    "category": meta.get("category", ""),
                    "snippet": snippet,
                    "score": combined_score,
                    "semantic_distance": float(dist),
                    "chunk_type": meta.get("chunk_type", ""),
                    "metadata": meta
                }
        
        # Ordenar por score combinado y tomar top_k
        sorted_results = sorted(note_results.values(), key=lambda x: x["score"], reverse=True)
        return sorted_results[:top_k]
    
    def _create_smart_snippet(self, text: str, query: str, max_length: int = 200) -> str:
        """Crea snippet inteligente destacando contexto relevante"""
        # Remover prefijo de título si existe
        if text.startswith("Título:"):
            lines = text.split("\n", 2)
            if len(lines) >= 3:
                text = lines[2]
        
        # Si el texto es corto, devolverlo completo
        if len(text) <= max_length:
            return text
        
        # Buscar términos de la consulta
        query_terms = query.lower().split()
        text_lower = text.lower()
        
        best_pos = 0
        best_score = 0
        
        # Encontrar la mejor posición para el snippet
        for i in range(0, len(text) - max_length + 1, 50):
            snippet_part = text_lower[i:i + max_length]
            score = sum(snippet_part.count(term) for term in query_terms)
            
            if score > best_score:
                best_score = score
                best_pos = i
        
        # Crear snippet
        snippet = text[best_pos:best_pos + max_length]
        
        # Limpiar bordes
        if best_pos > 0:
            snippet = "..." + snippet.lstrip()
        if best_pos + max_length < len(text):
            snippet = snippet.rstrip() + "..."
        
        return snippet

    def delete_note_chunks(self, note_id: int) -> None:
        """Elimina chunks existentes para una nota"""
        try:
            results = self.col.get(where={"note_id": note_id})
            ids = (results or {}).get("ids", []) or []
            if ids:
                self.col.delete(ids=ids)
        except Exception:
            pass

    def get_statistics(self) -> Dict[str, Any]:
        """Obtiene estadísticas del índice vectorial"""
        try:
            count = self.col.count()
            
            # Obtener muestra de metadata
            sample = self.col.get(limit=100, include=["metadatas"])
            metas = sample.get("metadatas", [])
            
            chunk_types = {}
            categories = set()
            sources = set()
            
            for meta in metas:
                chunk_type = meta.get("chunk_type", "unknown")
                chunk_types[chunk_type] = chunk_types.get(chunk_type, 0) + 1
                categories.add(meta.get("category", ""))
                sources.add(meta.get("source", ""))
            
            return {
                "total_chunks": count,
                "chunk_types": chunk_types,
                "categories": list(categories),
                "sources": list(sources),
                "index_health": "healthy" if count > 0 else "empty"
            }
        except Exception as e:
            return {"error": str(e), "index_health": "error"}