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


class SemanticChunker:
    """Chunking que preserva coherencia semántica usando análisis de texto"""
    
    def __init__(self, max_chars: int = 800, overlap: int = 100, min_chunk_size: int = 200):
        self.max_chars = max_chars
        self.overlap = overlap
        self.min_chunk_size = min_chunk_size
    
    def chunk_text(self, text: str, title: str = "") -> List[Tuple[int, int, str, str, Dict[str, Any]]]:
        """Chunking semánticamente coherente"""
        chunks = []
        
        # Chunk especial para título con contexto
        if title.strip():
            title_context = self._create_title_context(title, text[:500])
            title_keywords = self._extract_keywords(title_context)
            chunks.append((0, 0, title_context, "title", {
                "importance": 1.0,
                "is_title": True,
                "semantic_density": self._calculate_semantic_density(title_context),
                "keywords": ",".join(list(title_keywords)[:10])  # CAMBIO: convertir a string
            }))
            
        if not text.strip():
            return chunks
        
        # Análisis de estructura semántica
        semantic_boundaries = self._find_semantic_boundaries(text)
        chunks.extend(self._create_semantic_chunks(text, semantic_boundaries))
        
        # Post-procesamiento: optimizar chunks
        chunks = self._optimize_chunks(chunks)
        
        return chunks
    
    def _create_title_context(self, title: str, preview: str) -> str:
        """Crea contexto rico para el título"""
        # Extraer keywords del título
        title_keywords = self._extract_keywords(title)
        
        # Encontrar contexto relevante en el preview
        relevant_context = self._find_relevant_context(title_keywords, preview)
        
        return f"{title}\n\nContexto: {relevant_context}"
    
    def _find_relevant_context(self, keywords: set, text: str) -> str:
        """Encuentra contexto relevante basado en keywords"""
        if not keywords or not text:
            return text[:200]
        
        sentences = self._split_sentences(text)
        scored_sentences = []
        
        for sentence in sentences:
            sentence_keywords = self._extract_keywords(sentence)
            overlap = len(keywords & sentence_keywords)
            score = overlap / len(keywords) if keywords else 0
            scored_sentences.append((sentence, score))
        
        # Tomar las mejores oraciones
        scored_sentences.sort(key=lambda x: x[1], reverse=True)
        best_sentences = [s[0] for s in scored_sentences[:3]]
        
        return " ".join(best_sentences)[:200]
    
    def _find_semantic_boundaries(self, text: str) -> List[int]:
        """Encuentra límites semánticos naturales en el texto"""
        boundaries = [0]
        
        # Dividir por párrafos primero
        paragraphs = re.split(r'\n\s*\n', text)
        current_pos = 0
        
        for para in paragraphs:
            if not para.strip():
                current_pos += len(para)
                continue
            
            para_start = current_pos
            para_end = current_pos + len(para)
            
            # Si el párrafo es muy largo, encontrar sub-límites
            if len(para) > self.max_chars:
                sub_boundaries = self._find_paragraph_boundaries(para, para_start)
                boundaries.extend(sub_boundaries)
            else:
                boundaries.append(para_end)
            
            current_pos = para_end
        
        return sorted(set(boundaries))
    
    def _find_paragraph_boundaries(self, paragraph: str, base_offset: int) -> List[int]:
        """Encuentra límites dentro de párrafos largos"""
        boundaries = []
        sentences = self._split_sentences(paragraph)
        
        current_chunk = ""
        current_pos = base_offset
        sentence_start = 0
        
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            
            # Calcular coherencia semántica con chunk actual
            coherence = self._calculate_coherence(current_chunk, sentence)
            potential_chunk = current_chunk + (" " if current_chunk else "") + sentence
            
            # Decidir si cortar aquí
            should_break = (
                len(potential_chunk) > self.max_chars and 
                current_chunk and 
                coherence < 0.7  # Umbral de coherencia
            )
            
            if should_break:
                boundaries.append(current_pos + len(current_chunk))
                # Iniciar nuevo chunk con overlap semántico
                overlap_text = self._get_semantic_overlap(current_chunk)
                current_chunk = overlap_text + sentence
            else:
                current_chunk = potential_chunk
            
            sentence_start += len(sentence) + 1
        
        return boundaries
    
    def _calculate_coherence(self, chunk: str, sentence: str) -> float:
        """Calcula coherencia semántica entre chunk y nueva oración"""
        if not chunk or not sentence:
            return 1.0
        
        # Extraer keywords de ambos
        chunk_keywords = self._extract_keywords(chunk)
        sentence_keywords = self._extract_keywords(sentence)
        
        if not chunk_keywords or not sentence_keywords:
            return 0.5
        
        # Calcular overlap de keywords
        overlap = len(chunk_keywords & sentence_keywords)
        total = len(chunk_keywords | sentence_keywords)
        
        return overlap / total if total > 0 else 0.0
    
    def _get_semantic_overlap(self, text: str) -> str:
        """Obtiene overlap semánticamente relevante"""
        if not text:
            return ""
        
        sentences = self._split_sentences(text)
        if not sentences:
            return text[-self.overlap:] + " "
        
        # Tomar última oración completa si es corta
        last_sentence = sentences[-1].strip()
        if len(last_sentence) <= self.overlap:
            return last_sentence + " "
        
        return text[-self.overlap:] + " "
    
    def _split_sentences(self, text: str) -> List[str]:
        """Divide texto en oraciones"""
        # Regex mejorado para español
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ])', text)
        return [s.strip() for s in sentences if s.strip()]
    
    def _extract_keywords(self, text: str) -> set:
        """Extrae keywords significativas del texto"""
        # Normalizar texto
        text = re.sub(r'[^\w\s]', ' ', text.lower())
        words = text.split()
        
        # Filtrar stopwords básicas y palabras muy cortas
        stopwords = {
            'el', 'la', 'de', 'que', 'y', 'a', 'en', 'un', 'es', 'se', 'no', 'te', 'lo', 'le', 
            'da', 'su', 'por', 'son', 'con', 'para', 'al', 'del', 'las', 'una', 'como', 'pero',
            'sus', 'han', 'fue', 'ser', 'más', 'muy', 'todo', 'está', 'tiene', 'puede', 'este',
            'esta', 'estos', 'estas', 'ese', 'esa', 'esos', 'esas', 'aquel', 'aquella', 'aquellos',
            'aquellas', 'yo', 'tú', 'él', 'ella', 'nosotros', 'nosotras', 'vosotros', 'vosotras',
            'ellos', 'ellas', 'me', 'te', 'se', 'nos', 'os', 'mi', 'tu', 'su', 'nuestro', 'vuestro',
            'también', 'sino', 'hasta', 'desde', 'cuando', 'donde', 'porque', 'si', 'aunque'
        }
        
        keywords = {
            word for word in words 
            if len(word) > 2 and word not in stopwords
        }
        
        # Detectar entidades (palabras que empiezan con mayúscula en texto original)
        entities = set(re.findall(r'\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\b', text))
        keywords.update(word.lower() for word in entities)
        
        return keywords
    
    def _create_semantic_chunks(self, text: str, boundaries: List[int]) -> List[Tuple]:
        """Crea chunks basados en límites semánticos"""
        chunks = []
        skip_next = False
        
        for i in range(len(boundaries) - 1):
            if skip_next:
                skip_next = False
                continue
                
            start = boundaries[i]
            end = boundaries[i + 1]
            chunk_text = text[start:end].strip()
            
            # Si chunk es muy pequeño, intentar combinar con el siguiente
            if len(chunk_text) < self.min_chunk_size and i < len(boundaries) - 2:
                next_end = boundaries[i + 2]
                extended_text = text[start:next_end].strip()
                
                if len(extended_text) <= self.max_chars:
                    chunk_text = extended_text
                    end = next_end
                    skip_next = True
            
            if chunk_text:
                metadata = self._extract_chunk_metadata(chunk_text, start)
                chunks.append((start, end, chunk_text, "semantic", metadata))
        
        return chunks
    
    def _optimize_chunks(self, chunks: List[Tuple]) -> List[Tuple]:
        """Optimiza chunks finales"""
        if len(chunks) <= 1:
            return chunks
        
        optimized = []
        i = 0
        
        while i < len(chunks):
            current = chunks[i]
            current_text = current[2]
            
            # Si chunk es muy pequeño y no es título, intentar fusionar
            if (len(current_text) < self.min_chunk_size and 
                current[3] != "title" and 
                i < len(chunks) - 1):
                
                next_chunk = chunks[i + 1]
                
                # Verificar si se pueden fusionar semánticamente
                if self._can_merge_chunks(current, next_chunk):
                    merged = self._merge_chunks(current, next_chunk)
                    optimized.append(merged)
                    i += 2
                    continue
            
            optimized.append(current)
            i += 1
        
        return optimized
    
    def _can_merge_chunks(self, chunk1: Tuple, chunk2: Tuple) -> bool:
        """Determina si dos chunks se pueden fusionar"""
        combined_length = len(chunk1[2]) + len(chunk2[2])
        if combined_length > self.max_chars:
            return False
        
        # Calcular coherencia semántica
        coherence = self._calculate_coherence(chunk1[2], chunk2[2])
        return coherence > 0.5
    
    def _merge_chunks(self, chunk1: Tuple, chunk2: Tuple) -> Tuple:
        """Fusiona dos chunks"""
        combined_text = chunk1[2] + " " + chunk2[2]
        combined_metadata = chunk1[4].copy()
        
        # Actualizar metadata combinada
        combined_keywords = self._extract_keywords(combined_text)
        combined_metadata.update({
            "word_count": len(combined_text.split()),
            "char_count": len(combined_text),
            "keywords": ",".join(list(combined_keywords)[:10]),  # CAMBIO: convertir a string
            "semantic_density": self._calculate_semantic_density(combined_text),
            "importance": max(chunk1[4].get("importance", 0.5), chunk2[4].get("importance", 0.5)),
            "merged": True
        })
        
        return (
            chunk1[0],  # start del primer chunk
            chunk2[1],  # end del segundo chunk
            combined_text,
            "merged",
            combined_metadata
    )
    
    def _extract_chunk_metadata(self, text: str, position: int) -> Dict[str, Any]:
        """Extrae metadata rica del chunk"""
        keywords = self._extract_keywords(text)
        
        return {
            "word_count": len(text.split()),
            "char_count": len(text),
            "position": position,
            "keyword_count": len(keywords),
            "keywords": ",".join(list(keywords)[:10]),  # CAMBIO: convertir a string
            "semantic_density": self._calculate_semantic_density(text),
            "has_entities": bool(re.search(r'\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\b', text)),
            "has_numbers": bool(re.search(r'\d+', text)),
            "has_dates": bool(re.search(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b', text)),
            "importance": self._calculate_importance(text, keywords),
            "created_at": datetime.utcnow().isoformat()
        }
    
    def _calculate_semantic_density(self, text: str) -> float:
        """Calcula densidad semántica del texto"""
        words = text.split()
        if len(words) == 0:
            return 0.0
        
        keywords = self._extract_keywords(text)
        return len(keywords) / len(words)
    
    def _calculate_importance(self, text: str, keywords: set) -> float:
        """Calcula importancia del chunk"""
        base_score = 0.5
        
        # Factores que aumentan importancia
        if len(keywords) > 5:
            base_score += 0.1
        
        # Presencia de números o fechas
        if re.search(r'\d+', text):
            base_score += 0.1
        
        # Entidades nombradas
        if re.search(r'\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\b', text):
            base_score += 0.1
        
        # Longitud óptima
        if 300 <= len(text) <= 600:
            base_score += 0.1
        
        # Densidad semántica alta
        density = self._calculate_semantic_density(text)
        if density > 0.3:
            base_score += 0.1
        
        return min(base_score, 1.0)


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
        speaker_matches = list(re.finditer(speaker_pattern, text, re.MULTILINE | re.DOTALL))
        
        if speaker_matches:
            for match in speaker_matches:
                speaker = match.group(1)
                content = match.group(2).strip()
                segments.append((f"{speaker}: {content}", "speaker_segment"))
        else:
            # Dividir por tiempo o párrafos
            time_matches = list(re.finditer(time_pattern, text, re.DOTALL))
            
            if time_matches:
                for match in time_matches:
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
    
    def _smart_split_segment(self, segment: str, base_offset: int, segment_type: str, context: MeetingContext) -> List[Tuple]:
        """Divide segmento largo inteligentemente"""
        chunks = []
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', segment)
        
        current_chunk = ""
        chunk_start = base_offset
        
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            
            potential_chunk = current_chunk + (" " if current_chunk else "") + sentence
            
            if len(potential_chunk) > self.max_chars and current_chunk:
                # Guardar chunk actual
                metadata = self._extract_meeting_metadata(current_chunk, segment_type, context, chunk_start)
                chunks.append((
                    chunk_start,
                    chunk_start + len(current_chunk),
                    current_chunk,
                    segment_type,
                    metadata
                ))
                
                # Iniciar nuevo chunk
                current_chunk = sentence
                chunk_start = chunk_start + len(current_chunk) - len(sentence)
            else:
                current_chunk = potential_chunk
        
        # Agregar último chunk
        if current_chunk:
            metadata = self._extract_meeting_metadata(current_chunk, segment_type, context, chunk_start)
            chunks.append((
                chunk_start,
                chunk_start + len(current_chunk),
                current_chunk,
                segment_type,
                metadata
            ))
        
        return chunks
    
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
    
    def _enrich_with_conversation_context(self, chunks: List[Tuple]) -> List[Tuple]:
        """Enriquece chunks con contexto conversacional"""
        # Para futuras mejoras: analizar flujo de conversación
        return chunks


class VectorIndex:
    """ChromaDB mejorado con chunking inteligente y búsqueda avanzada"""

    def __init__(self, settings: Settings, ai: AIService) -> None:
        if not CHROMADB_AVAILABLE:
            raise RuntimeError("ChromaDB no está disponible. La búsqueda semántica estará deshabilitada.")
        
        self.settings = settings
        self.ai = ai
        self.chunker = SemanticChunker(max_chars=800, overlap=100)

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
                name="notes_v3",  # Nueva versión con mejoras semánticas
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
                        name="notes_v3",
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
        
        # OBTENER FECHA DE CREACIÓN DESDE SQLite (solo para metadatos)
        created_at = datetime.utcnow().isoformat()
        try:
            # Si tenemos acceso a la nota original, usar su fecha
            from .db import NotesDB
            # Esto es solo para obtener la fecha, no para contenido
            pass
        except:
            pass
        
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
                "created_at": created_at,  # AGREGAR FECHA DE CREACIÓN
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
                print(f"✅ Nota {note_id} indexada con {len(ids)} chunks")
            except Exception as e:
                raise RuntimeError(f"Error indexando nota {note_id}: {e}")

    def search(self, query: str, top_k: int = 5, filters: Dict[str, Any] = None) -> List[Dict]:
        """Búsqueda híbrida semántica + keyword con re-ranking adaptativo"""
        if not query.strip():
            return []
        
        # Análisis de la consulta
        query_analysis = self._analyze_query(query)
        
        # Búsqueda semántica expandida
        semantic_results = self._semantic_search(query, query_analysis, top_k * 3, filters)
        
        # Búsqueda por keywords
        keyword_results = self._keyword_search(query, query_analysis, top_k * 2, filters)
        
        # Combinar y re-rankear resultados
        combined_results = self._hybrid_ranking(semantic_results, keyword_results, query_analysis)
        
        return combined_results[:top_k]

    def search_optimized(self, query: str, top_k: int = 5, filters: Dict[str, Any] = None) -> List[Dict]:
        """Búsqueda optimizada que reduce latencia significativamente"""
        if not query.strip():
            return []
        
        # Análisis de consulta simplificado
        query_keywords = self.chunker._extract_keywords(query)
        
        try:
            # 1. BÚSQUEDA SEMÁNTICA DIRECTA (reducida)
            where_filter = filters or {}
            
            res = self.col.query(
                query_texts=[query],
                n_results=min(top_k, 15),  # Reducir búsqueda inicial
                include=["metadatas", "documents", "distances"],
                where=where_filter if where_filter else None
            )
            
            semantic_results = self._process_semantic_results_fast(res, query_keywords)
            
            # 2. BÚSQUEDA POR KEYWORDS OPTIMIZADA
            keyword_results = self._keyword_search_optimized(query_keywords, top_k, filters)
            
            # 3. RANKING HÍBRIDO SIMPLIFICADO
            final_results = self._fast_hybrid_ranking(semantic_results, keyword_results)
            
            return final_results[:top_k]
            
        except Exception as e:
            print(f"Error en búsqueda optimizada: {e}")
            return self._fallback_search(query, top_k, filters)

    def _keyword_search_optimized(self, query_keywords: set, search_k: int, filters: Dict) -> List[Dict]:
        """Búsqueda por keywords optimizada - UNA SOLA consulta a ChromaDB"""
        if not query_keywords:
            return []
        
        try:
            # OPTIMIZACIÓN CLAVE: Una sola llamada get() para toda la colección
            all_data = self.col.get(include=["metadatas", "documents"])
            
            if not all_data or not all_data.get("metadatas"):
                return []
            
            results = []
            keyword_list = list(query_keywords)
            
            # Procesar todos los chunks de una vez
            for doc, meta in zip(all_data["documents"], all_data["metadatas"]):
                note_id = meta["note_id"]
                
                # Convertir keywords del chunk
                keywords_str = meta.get("keywords", "")
                chunk_keywords = self._keywords_to_set(keywords_str)
                
                # Calcular overlap de keywords
                if chunk_keywords:
                    matched = query_keywords & chunk_keywords
                    if matched:
                        score = len(matched) / len(query_keywords)
                        results.append({
                            "note_id": note_id,
                            "title": meta["title"],
                            "snippet": doc[:200],
                            "score": score,
                            "matched_keywords": matched,
                            "metadata": meta,
                            "search_type": "keyword"
                        })
            
            # Agrupar por nota_id y tomar mejor score
            note_results = {}
            for result in results:
                note_id = result["note_id"]
                if note_id not in note_results or result["score"] > note_results[note_id]["score"]:
                    note_results[note_id] = result
            
            return sorted(note_results.values(), key=lambda x: x["score"], reverse=True)[:search_k]
            
        except Exception as e:
            print(f"Error en keyword search optimizada: {e}")
            return []

    def _process_semantic_results_fast(self, res: Dict, query_keywords: set) -> List[Dict]:
        """Procesamiento rápido de resultados semánticos"""
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
            semantic_score = 1 - float(dist)
            
            # Bonus simple por importancia
            importance = meta.get("importance", 0.5)
            combined_score = semantic_score + (importance * 0.1)
            
            if note_id not in note_results or combined_score > note_results[note_id]["score"]:
                # Snippet simplificado
                snippet = doc[:200] if len(doc) > 200 else doc
                if snippet.startswith("Título:"):
                    lines = snippet.split("\n", 2)
                    if len(lines) >= 3:
                        snippet = lines[2][:200]
                
                note_results[note_id] = {
                    "note_id": note_id,
                    "title": meta["title"],
                    "snippet": snippet,
                    "score": combined_score,
                    "metadata": meta,
                    "search_type": "semantic"
                }
        
        return list(note_results.values())

    def _fast_hybrid_ranking(self, semantic_results: List, keyword_results: List) -> List[Dict]:
        """Ranking híbrido simplificado para mayor velocidad"""
        combined = {}
        
        # Procesar semánticos
        for result in semantic_results:
            note_id = result["note_id"]
            combined[note_id] = result
            combined[note_id]["semantic_score"] = result["score"]
            combined[note_id]["keyword_score"] = 0
        
        # Agregar/mejorar con keywords
        for result in keyword_results:
            note_id = result["note_id"]
            if note_id in combined:
                combined[note_id]["keyword_score"] = result["score"]
            else:
                combined[note_id] = result
                combined[note_id]["semantic_score"] = 0
                combined[note_id]["keyword_score"] = result["score"]
        
        # Score final simplificado
        for note_id, result in combined.items():
            semantic_weight = 0.7
            keyword_weight = 0.3
            
            final_score = (
                result.get("semantic_score", 0) * semantic_weight + 
                result.get("keyword_score", 0) * keyword_weight
            )
            result["final_score"] = final_score
        
        return sorted(combined.values(), key=lambda x: x["final_score"], reverse=True)

    def _fallback_search(self, query: str, top_k: int, filters: Dict) -> List[Dict]:
        """Búsqueda de emergencia si falla la optimizada"""
        try:
            res = self.col.query(
                query_texts=[query],
                n_results=top_k,
                include=["metadatas", "documents", "distances"],
                where=filters if filters else None
            )
            return self._process_semantic_results_fast(res, set())
        except Exception:
            return []
    def _analyze_query(self, query: str) -> Dict[str, Any]:
        """Analiza la consulta para optimizar búsqueda"""
        keywords = self.chunker._extract_keywords(query)
        
        return {
            "original": query,
            "keywords": keywords,
            "has_entities": bool(re.search(r'\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\b', query)),
            "has_numbers": bool(re.search(r'\d+', query)),
            "word_count": len(query.split()),
            "query_type": self._classify_query_type(query, keywords)
        }

    def _classify_query_type(self, query: str, keywords: set) -> str:
        """Clasifica el tipo de consulta"""
        query_lower = query.lower()
        
        if any(word in query_lower for word in ['estado', 'situación', 'progreso', 'avance']):
            return "status"
        elif any(word in query_lower for word in ['proyecto', 'implementación', 'desarrollo']):
            return "project"
        elif any(word in query_lower for word in ['quién', 'quien', 'responsable', 'encargado']):
            return "person"
        elif any(word in query_lower for word in ['cuándo', 'cuando', 'fecha', 'plazo']):
            return "temporal"
        else:
            return "general"

    def _semantic_search(self, query: str, analysis: Dict, search_k: int, filters: Dict) -> List[Dict]:
        """Búsqueda semántica con contexto expandido"""
        try:
            # Crear consulta contextual
            contextual_query = self._create_contextual_query(query, analysis)
            
            where_filter = filters or {}
            
            res = self.col.query(
                query_texts=[contextual_query],
                n_results=search_k,
                include=["metadatas", "documents", "distances"],
                where=where_filter if where_filter else None
            )
            
            return self._process_semantic_results(res, analysis)
        except Exception as e:
            print(f"Error en búsqueda semántica: {e}")
            return []

    def _create_contextual_query(self, query: str, analysis: Dict) -> str:
        """Crea consulta contextual adaptativa"""
        base_query = query
        
        # Expandir basado en keywords encontradas
        if analysis["keywords"]:
            # Usar keywords para crear contexto adicional
            keyword_context = " ".join(list(analysis["keywords"])[:5])
            base_query = f"{query} {keyword_context}"
        
        return base_query
    def _keywords_to_set(self, keywords_str: str) -> set:
        """Convierte string de keywords de vuelta a set"""
        if not keywords_str or not isinstance(keywords_str, str):
            return set()
        return set(k.strip() for k in keywords_str.split(",") if k.strip())
    def _keyword_search(self, query: str, analysis: Dict, search_k: int, filters: Dict) -> List[Dict]:
        """Búsqueda por keywords en metadata"""
        results = []
        query_keywords = analysis["keywords"]
        
        if not query_keywords:
            return results
        
        try:
            # Buscar por cada keyword
            all_results = {}
            
            for keyword in query_keywords:
                # Buscar en metadata de keywords usando patrón de texto
                res = self.col.get(
                    include=["metadatas", "documents"]
                )
                
                if res and res.get("metadatas"):
                    for doc, meta in zip(res["documents"], res["metadatas"]):
                        note_id = meta["note_id"]
                        
                        # CORRECCIÓN: usar self en lugar de self.chunker
                        keywords_str = meta.get("keywords", "")
                        chunk_keywords = self._keywords_to_set(keywords_str)
                        
                        # Verificar si contiene la keyword
                        if keyword in chunk_keywords:
                            if note_id not in all_results:
                                all_results[note_id] = {
                                    "document": doc,
                                    "metadata": meta,
                                    "matched_keywords": set(),
                                    "search_type": "keyword"
                                }
                            all_results[note_id]["matched_keywords"].add(keyword)
            
            # Calcular scores basado en coincidencias
            for note_id, result in all_results.items():
                overlap = len(result["matched_keywords"])
                total = len(query_keywords)
                score = overlap / total if total > 0 else 0
                result["score"] = score
                results.append(result)
                
        except Exception as e:
            print(f"Error en búsqueda por keywords: {e}")
        
        return sorted(results, key=lambda x: x["score"], reverse=True)[:search_k]
    def _hybrid_ranking(self, semantic_results: List, keyword_results: List, analysis: Dict) -> List[Dict]:
        """Combina y re-rankea resultados usando múltiples señales"""
        combined = {}
        
        # Procesar resultados semánticos
        for result in semantic_results:
            note_id = result["note_id"]
            combined[note_id] = result
            combined[note_id]["semantic_score"] = result.get("score", 0)
            combined[note_id]["keyword_score"] = 0
        
        # Agregar/mejorar con resultados de keywords
        for result in keyword_results:
            note_id = result["metadata"]["note_id"]
            if note_id in combined:
                combined[note_id]["keyword_score"] = result["score"]
            else:
                # Crear entrada nueva desde keyword search
                combined[note_id] = {
                    "note_id": note_id,
                    "title": result["metadata"]["title"],
                    "category": result["metadata"].get("category", ""),
                    "snippet": result["document"][:200],
                    "semantic_score": 0,
                    "keyword_score": result["score"],
                    "metadata": result["metadata"]
                }
        
        # Calcular score final adaptativo
        final_results = []
        for note_id, result in combined.items():
            semantic_weight = 0.7
            keyword_weight = 0.3
            
            # Ajustar pesos basado en tipo de consulta
            if analysis["query_type"] == "project":
                keyword_weight = 0.4  # Dar más peso a keywords para proyectos
                semantic_weight = 0.6
            elif analysis["query_type"] == "status":
                keyword_weight = 0.5  # Balance para consultas de estado
                semantic_weight = 0.5
            
            final_score = (
                result["semantic_score"] * semantic_weight + 
                result["keyword_score"] * keyword_weight
            )
            
            # Bonus por metadata relevante
            metadata = result.get("metadata", {})
            if analysis["has_entities"] and metadata.get("has_entities"):
                final_score += 0.1
            
            # Bonus por importancia del chunk
            chunk_importance = metadata.get("importance", 0.5)
            final_score += chunk_importance * 0.1
            
            result["final_score"] = final_score
            final_results.append(result)
        
        return sorted(final_results, key=lambda x: x["final_score"], reverse=True)

    def _process_semantic_results(self, res: Dict, analysis: Dict) -> List[Dict]:
        """Procesa resultados de búsqueda semántica"""
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
                "semantic": 0.1,
                "merged": 0.05
            }.get(meta.get("chunk_type", ""), 0.0)
            
            combined_score = semantic_score + (importance_score * 0.2) + type_bonus
            
            # Mantener mejor resultado por nota
            if note_id not in note_results or combined_score > note_results[note_id]["score"]:
                # Crear snippet inteligente
                snippet = self._create_smart_snippet(doc, analysis["original"])
                
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
        
        return list(note_results.values())

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
        query_keywords = self.chunker._extract_keywords(query)
        text_lower = text.lower()
        
        best_pos = 0
        best_score = 0
        
        # Encontrar la mejor posición para el snippet
        for i in range(0, len(text) - max_length + 1, 50):
            snippet_part = text_lower[i:i + max_length]
            score = sum(snippet_part.count(keyword) for keyword in query_keywords)
            
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
            avg_importance = 0
            keyword_diversity = set()
            
            for meta in metas:
                chunk_type = meta.get("chunk_type", "unknown")
                chunk_types[chunk_type] = chunk_types.get(chunk_type, 0) + 1
                categories.add(meta.get("category", ""))
                sources.add(meta.get("source", ""))
                avg_importance += meta.get("importance", 0.5)
                
                # Agregar keywords para diversidad
                keywords = meta.get("keywords", [])
                keyword_diversity.update(keywords[:5])  # Top 5 keywords por chunk
            
            avg_importance = avg_importance / len(metas) if metas else 0
            
            return {
                "total_chunks": count,
                "chunk_types": chunk_types,
                "categories": list(categories),
                "sources": list(sources),
                "avg_importance": round(avg_importance, 2),
                "keyword_diversity": len(keyword_diversity),
                "index_health": "healthy" if count > 0 else "empty"
            }
        except Exception as e:
            return {"error": str(e), "index_health": "error"}

    def debug_search(self, query: str) -> Dict:
        """Debug para analizar búsquedas problemáticas"""
        try:
            analysis = self._analyze_query(query)
            
            # Búsqueda semántica raw
            res = self.col.query(
                query_texts=[query],
                n_results=10,
                include=["metadatas", "documents", "distances"]
            )
            
            debug_info = {
                "query_analysis": analysis,
                "total_chunks": self.col.count(),
                "found_results": len(res["documents"][0]) if res["documents"] else 0
            }
            
            if res["documents"] and res["documents"][0]:
                debug_info["top_results"] = []
                for i in range(min(5, len(res["documents"][0]))):
                    # CAMBIO: convertir keywords string a lista para debug
                    keywords_str = res["metadatas"][0][i].get("keywords", "")
                    keywords_list = keywords_str.split(",") if keywords_str else []
                    
                    debug_info["top_results"].append({
                        "title": res["metadatas"][0][i].get("title", ""),
                        "chunk_type": res["metadatas"][0][i].get("chunk_type", ""),
                        "distance": res["distances"][0][i],
                        "keywords": keywords_list,
                        "snippet": res["documents"][0][i][:100] + "..."
                    })
            
            return debug_info
            
        except Exception as e:
            return {"error": str(e)}