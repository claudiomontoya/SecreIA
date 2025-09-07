import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple
import pytz

@dataclass
class Note:
    """Representation of a note in the database."""

    id: Optional[int]
    title: str
    content: str
    category: str
    tags: List[str]
    source: str  # 'manual', 'transcript', 'import', etc.
    audio_path: Optional[str]
    created_at: str
    updated_at: str


class NotesDB:
    """SQLite-backed storage for notes and categories."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        # Ensure directory exists
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        """Initialize tables if they don't exist."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    category TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    source TEXT NOT NULL,
                    audio_path TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL
                );
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_notes_category ON notes(category);
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_notes_created_at ON notes(created_at);
                """
            )
            conn.commit()

    def add_category(self, name: str) -> None:
        """Insert a category if it does not exist."""
        with self._connect() as conn:
            cur = conn.cursor()
            try:
                cur.execute("INSERT INTO categories(name) VALUES (?)", (name,))
                conn.commit()
            except sqlite3.IntegrityError:
                # Category already exists
                pass

    def list_categories(self) -> List[str]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM categories ORDER BY name ASC")
            rows = cur.fetchall()
            return [r[0] for r in rows]
    def rename_category(self, old_name: str, new_name: str):
        """Renombra una categoría en todas las notas"""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE notes SET category = ? WHERE category = ?", (new_name, old_name))
            # También actualizar en la tabla categories
            cursor.execute("UPDATE categories SET name = ? WHERE name = ?", (new_name, old_name))
            conn.commit()

    def delete_category_and_reassign(self, category_to_delete: str, target_category: str):
        """Elimina categoría y reasigna notas a otra categoría"""
        with self._connect() as conn:
            cursor = conn.cursor()
            # Reasignar notas
            cursor.execute("UPDATE notes SET category = ? WHERE category = ?", (target_category, category_to_delete))
            # Eliminar categoría de la tabla categories
            cursor.execute("DELETE FROM categories WHERE name = ?", (category_to_delete,))
            conn.commit()

    def delete_category(self, category_name: str):
        """Elimina una categoría de la tabla categories"""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM categories WHERE name = ?", (category_name,))
            conn.commit()

    def merge_categories(self, categories_to_merge: list, target_name: str):
        """Fusiona múltiples categorías en una"""
        with self._connect() as conn:
            cursor = conn.cursor()
            
            # Primero, agregar la categoría objetivo si no existe
            cursor.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (target_name,))
            
            # Actualizar todas las notas
            placeholders = ','.join(['?' for _ in categories_to_merge])
            cursor.execute(f"UPDATE notes SET category = ? WHERE category IN ({placeholders})", 
                        [target_name] + categories_to_merge)
            
            # Eliminar las categorías fusionadas de la tabla categories
            for category in categories_to_merge:
                if category != target_name:  # No eliminar la categoría objetivo
                    cursor.execute("DELETE FROM categories WHERE name = ?", (category,))
            
            conn.commit()
    def upsert_note(self, note: Note) -> int:
        """Insert or update a note and return its ID."""
        # Usar hora de Chile en lugar de UTC
        chile_tz = pytz.timezone('America/Santiago')
        now = datetime.now(chile_tz).isoformat()
        
        with self._connect() as conn:
            cur = conn.cursor()
            if note.id is None:
                cur.execute(
                    """
                    INSERT INTO notes(title, content, category, tags, source, audio_path, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        note.title,
                        note.content,
                        note.category,
                        ",".join(note.tags),
                        note.source,
                        note.audio_path,
                        now,
                        now,
                    ),
                )
                note_id = cur.lastrowid
            else:
                cur.execute(
                    """
                    UPDATE notes
                    SET title=?, content=?, category=?, tags=?, source=?, audio_path=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        note.title,
                        note.content,
                        note.category,
                        ",".join(note.tags),
                        note.source,
                        note.audio_path,
                        now,
                        note.id,
                    ),
                )
                note_id = note.id
            conn.commit()
            return int(note_id)
    def get_note(self, note_id: int) -> Optional[Note]:
        """Retrieve a note by its ID."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, title, content, category, tags, source, audio_path, created_at, updated_at FROM notes WHERE id=?",
                (note_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return Note(
                id=row[0],
                title=row[1],
                content=row[2],
                category=row[3],
                tags=row[4].split(",") if row[4] else [],
                source=row[5],
                audio_path=row[6],
                created_at=row[7],
                updated_at=row[8],
            )

    def delete_note(self, note_id: int) -> None:
        """Delete a note by ID."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM notes WHERE id=?", (note_id,))
            conn.commit()

    def search_notes(
        self,
        query: str,
        category: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> List[Note]:
        """Keyword-based search for notes with optional filters."""
        with self._connect() as conn:
            cur = conn.cursor()
            sql = "SELECT id, title, content, category, tags, source, audio_path, created_at, updated_at FROM notes WHERE (title LIKE ? OR content LIKE ?)"
            params: Tuple[str, ...] = (f"%{query}%", f"%{query}%")
            if category:
                sql += " AND category=?"
                params += (category,)
            if tag:
                sql += " AND instr(tags, ?) > 0"
                params += (tag,)
            sql += " ORDER BY updated_at DESC"
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [
                Note(
                    id=r[0],
                    title=r[1],
                    content=r[2],
                    category=r[3],
                    tags=r[4].split(",") if r[4] else [],
                    source=r[5],
                    audio_path=r[6],
                    created_at=r[7],
                    updated_at=r[8],
                )
                for r in rows
            ]

    def list_notes(self, limit: int = 100) -> List[Note]:
        """List most recent notes up to a limit."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, title, content, category, tags, source, audio_path, created_at, updated_at FROM notes ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            )
            rows = cur.fetchall()
            return [
                Note(
                    id=r[0],
                    title=r[1],
                    content=r[2],
                    category=r[3],
                    tags=r[4].split(",") if r[4] else [],
                    source=r[5],
                    audio_path=r[6],
                    created_at=r[7],
                    updated_at=r[8],
                )
                for r in rows
            ]
            