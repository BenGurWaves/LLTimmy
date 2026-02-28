"""
Memory Manager for LLTimmy
Manages conscious, subconscious, and long-term memory systems.

- Conscious: full current-day chat (RAM + raw_chats/)
- Subconscious: ChromaDB with Ollama nomic-embed-text embeddings
- Long-term: compressed daily summaries + archived raw chats
- Graph: entity/relationship memory for connected knowledge
- Profile: auto-updated user profile from interactions
"""
import json
import shutil
import gzip
import hashlib
import logging
import threading
import requests
import time
from datetime import datetime, date
from typing import List, Dict, Optional
from pathlib import Path
from functools import lru_cache

import chromadb
from chromadb.config import Settings as ChromaSettings

# Suppress ChromaDB telemetry and noisy logs
import os
os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["CHROMA_TELEMETRY"] = "False"
logging.getLogger("chromadb").setLevel(logging.WARNING)
logging.getLogger("chromadb.telemetry").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)

# Use __file__ relative path so it works regardless of project location
MEMORY_BASE = Path(__file__).resolve().parent / "memory"


# ---------------------------------------------------------------------------
# Ollama embedding function for ChromaDB (FIXED: added embed_query method)
# ---------------------------------------------------------------------------
class OllamaEmbeddingFunction:
    """ChromaDB-compatible embedding function backed by Ollama.
    Supports both __call__ (batch) and embed_query/embed_documents
    for full compatibility with ChromaDB and LangChain interfaces.
    """

    def __init__(
        self,
        host: str = "http://localhost:11434",
        model: str = "nomic-embed-text",
    ):
        self.host = host
        self.model = model
        self._dim = 768  # nomic-embed-text default
        self._cache: Dict[str, List[float]] = {}
        self._cache_max = 500

    # ChromaDB >= 1.5 requires these methods on custom embedding functions
    def name(self) -> str:
        return "ollama-nomic-embed-text"

    def build_from_config(self, config):
        return self

    def get_config(self):
        return {}

    def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Internal batch embedding with caching."""
        if not texts:
            return []

        # Defensive: ensure every element is a string (fixes 'list has no attribute encode')
        sanitized = []
        for t in texts:
            if isinstance(t, str):
                sanitized.append(t)
            elif isinstance(t, (list, tuple)):
                sanitized.append(" ".join(str(x) for x in t))
            elif t is None:
                sanitized.append("")
            else:
                sanitized.append(str(t))
        texts = sanitized

        results = []
        uncached_texts = []
        uncached_indices = []

        for i, text in enumerate(texts):
            key = hashlib.md5(text.encode()).hexdigest()
            if key in self._cache:
                results.append(self._cache[key])
            else:
                results.append(None)
                uncached_texts.append(text)
                uncached_indices.append(i)

        if uncached_texts:
            try:
                resp = requests.post(
                    f"{self.host}/api/embed",
                    json={"model": self.model, "input": uncached_texts},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                # Handle both "embeddings" (batch) and "embedding" (single) response keys
                embeddings = data.get("embeddings") or data.get("embedding")
                if not embeddings:
                    raise ValueError("No embeddings in Ollama response")
                # Normalize: if response is a flat list of floats, wrap it
                if embeddings and isinstance(embeddings[0], (int, float)):
                    embeddings = [embeddings]
                for idx, emb in zip(uncached_indices, embeddings):
                    results[idx] = emb
                    key = hashlib.md5(texts[idx].encode()).hexdigest()
                    self._cache[key] = emb
                    if len(self._cache) > self._cache_max:
                        oldest = next(iter(self._cache))
                        del self._cache[oldest]
            except Exception as e:
                logger.warning(f"Ollama embedding failed ({e}), using zero vectors")
                for idx in uncached_indices:
                    results[idx] = [0.0] * self._dim

        return results

    def __call__(self, input: list[str]) -> list[list[float]]:
        """Embed a batch of texts via Ollama /api/embed.
        Returns List[List[float]] — ChromaDB requires each embedding
        to be a list/sequence of floats, never a bare float."""
        results = self._embed_batch(input)
        # Defensive: ensure every element is a list of floats
        sanitized = []
        for emb in results:
            if isinstance(emb, (int, float)):
                sanitized.append([0.0] * self._dim)
            elif not isinstance(emb, (list, tuple)):
                sanitized.append([0.0] * self._dim)
            else:
                sanitized.append(list(emb))
        return sanitized

    def embed_query(self, input: str = None, text: str = None, **kwargs) -> List[float]:
        """Embed a single query string. Required by some ChromaDB/LangChain interfaces.
        Accepts both `input` (ChromaDB style) and `text` (LangChain style) parameter names."""
        query = input or text or kwargs.get("input") or kwargs.get("text") or ""
        if isinstance(query, (list, tuple)):
            query = " ".join(str(x) for x in query)
        if not isinstance(query, str):
            query = str(query) if query is not None else ""
        if not query:
            return [0.0] * self._dim
        results = self._embed_batch([query])
        return results[0] if results else [0.0] * self._dim

    def embed_documents(self, input: List[str] = None, documents: List[str] = None, **kwargs) -> List[List[float]]:
        """Embed a list of documents. Required by some ChromaDB/LangChain interfaces.
        Accepts both `input` (ChromaDB style) and `documents` (LangChain style) parameter names."""
        docs = input or documents or kwargs.get("input") or kwargs.get("documents") or []
        if not docs:
            return []
        return self._embed_batch(docs)


# ---------------------------------------------------------------------------
# Conscious Memory - current-day chat
# ---------------------------------------------------------------------------
class ConsciousMemory:
    def __init__(self, base_dir: Path = None):
        self.base_dir = base_dir or MEMORY_BASE / "raw_chats"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._day_cache: Optional[List[Dict]] = None
        self._day_cache_date: Optional[str] = None
        self._write_lock = threading.Lock()

    def _daily_path(self) -> Path:
        return self.base_dir / f"{date.today().isoformat()}.json"

    def _invalidate_cache(self):
        self._day_cache = None

    def save_message(self, role: str, content: str, metadata: Dict = None):
        with self._write_lock:
            path = self._daily_path()
            messages = self._load(path)
            messages.append({
                "role": role,
                "content": content,
                "timestamp": datetime.now().isoformat(),
                "metadata": metadata or {},
            })
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(messages, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
            self._invalidate_cache()

    def load_current_day(self) -> List[Dict]:
        with self._write_lock:
            today = date.today().isoformat()
            if self._day_cache is not None and self._day_cache_date == today:
                return list(self._day_cache)
            data = self._load(self._daily_path())
            self._day_cache = data
            self._day_cache_date = today
            return data

    def clear_current_day(self):
        path = self._daily_path()
        if path.exists():
            path.unlink()
        self._invalidate_cache()

    def count_current_day(self) -> int:
        return len(self.load_current_day())

    @staticmethod
    def _load(path: Path) -> list:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return []
        return []


# ---------------------------------------------------------------------------
# Subconscious Memory - ChromaDB vector store (FIXED: faster search)
# ---------------------------------------------------------------------------
class SubconsciousMemory:
    def __init__(
        self,
        base_dir: Path = None,
        ollama_host: str = "http://localhost:11434",
    ):
        self.base_dir = base_dir or MEMORY_BASE / "subconscious"
        self.base_dir.mkdir(parents=True, exist_ok=True)

        self.embed_fn = OllamaEmbeddingFunction(host=ollama_host)
        self.client = chromadb.PersistentClient(path=str(self.base_dir))
        self.collection = self.client.get_or_create_collection(
            name="timmy_subconscious",
            embedding_function=self.embed_fn,
            metadata={"description": "LLTimmy subconscious memory"},
        )

    def add_message(self, content: str, metadata: Dict = None):
        if not isinstance(content, str):
            content = str(content) if content is not None else ""
        if not content or len(content.strip()) < 5:
            return
        metadata = dict(metadata or {})
        metadata["timestamp"] = datetime.now().isoformat()
        doc_id = hashlib.md5(content.encode()).hexdigest()
        try:
            self.collection.upsert(
                documents=[content],
                metadatas=[metadata],
                ids=[doc_id],
            )
        except Exception as e:
            logger.warning(f"Subconscious add failed: {e}")

    def search(self, query: str, n_results: int = 7) -> List[Dict]:
        if not isinstance(query, str):
            query = str(query) if query is not None else ""
        if not query or len(query.strip()) < 3:
            return []
        count = self.get_count()
        if count == 0:
            return []
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=min(n_results, count),
            )
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            return [
                {"content": docs[i], "metadata": metas[i]}
                for i in range(len(docs))
            ]
        except Exception as e:
            logger.warning(f"Subconscious search failed: {e}")
            return []

    def delete_by_content(self, content: str) -> bool:
        doc_id = hashlib.md5(content.encode()).hexdigest()
        try:
            self.collection.delete(ids=[doc_id])
            return True
        except Exception as e:
            logger.warning(f"Memory delete failed: {e}")
            return False

    def update_memory(self, old_content: str, new_content: str, metadata: Dict = None) -> bool:
        self.delete_by_content(old_content)
        self.add_message(new_content, metadata)
        return True

    def get_count(self) -> int:
        try:
            return self.collection.count()
        except Exception:
            return 0

    def clear_all(self):
        self.client.delete_collection("timmy_subconscious")
        self.collection = self.client.create_collection(
            name="timmy_subconscious",
            embedding_function=self.embed_fn,
            metadata={"description": "LLTimmy subconscious memory"},
        )


# ---------------------------------------------------------------------------
# Graph Memory - entity-relationship knowledge
# ---------------------------------------------------------------------------
class GraphMemory:
    """Simple entity-relationship graph persisted as JSON."""

    def __init__(self):
        self.graph_file = MEMORY_BASE / "graph_memory.json"
        MEMORY_BASE.mkdir(parents=True, exist_ok=True)
        self.nodes: Dict[str, Dict] = {}
        self.edges: List[Dict] = []
        self._load()

    def _load(self):
        if self.graph_file.exists():
            try:
                data = json.loads(self.graph_file.read_text(encoding="utf-8"))
                self.nodes = data.get("nodes", {})
                self.edges = data.get("edges", [])
            except Exception:
                pass

    def _save(self):
        data = {"nodes": self.nodes, "edges": self.edges}
        tmp = self.graph_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.graph_file)  # Atomic on POSIX

    def add_entity(self, name: str, entity_type: str = "general", properties: Dict = None):
        key = name.lower().strip()
        self.nodes[key] = {
            "name": name,
            "type": entity_type,
            "properties": properties or {},
            "updated_at": datetime.now().isoformat(),
        }
        self._save()

    def add_relationship(self, from_entity: str, to_entity: str, relation: str):
        edge = {
            "from": from_entity.lower().strip(),
            "to": to_entity.lower().strip(),
            "relation": relation,
            "created_at": datetime.now().isoformat(),
        }
        for e in self.edges:
            if e["from"] == edge["from"] and e["to"] == edge["to"] and e["relation"] == edge["relation"]:
                return
        self.edges.append(edge)
        self._save()

    def get_entity(self, name: str) -> Optional[Dict]:
        return self.nodes.get(name.lower().strip())

    def get_relationships(self, entity_name: str) -> List[Dict]:
        key = entity_name.lower().strip()
        return [
            e for e in self.edges
            if e["from"] == key or e["to"] == key
        ]

    def search_entities(self, query: str) -> List[Dict]:
        q = query.lower()
        return [
            node for key, node in self.nodes.items()
            if q in key or q in node.get("type", "")
        ]

    def get_summary(self) -> str:
        return f"Graph: {len(self.nodes)} entities, {len(self.edges)} relationships"


# ---------------------------------------------------------------------------
# Long-term Memory - daily summaries & archiving
# ---------------------------------------------------------------------------
class LongTermMemory:
    def __init__(self):
        self.compressed_dir = MEMORY_BASE / "compressed"
        self.archive_dir = MEMORY_BASE / "archive"
        self.compressed_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    def create_daily_summary(
        self, chat_file: Path, summary_count: int = 15
    ) -> str:
        messages = json.loads(chat_file.read_text(encoding="utf-8"))
        user_msgs = [m for m in messages if m["role"] == "user"]
        asst_msgs = [m for m in messages if m["role"] == "assistant"]

        lines: list[str] = []
        for i in range(min(len(user_msgs), summary_count)):
            lines.append(f"- {user_msgs[i]['content'][:120]}")
            if i < len(asst_msgs):
                lines.append(f"  -> {asst_msgs[i]['content'][:120]}")

        return "\n".join(lines)

    def save_daily_summary(self, summary: str, date_str: str = None):
        date_str = date_str or date.today().isoformat()
        path = self.compressed_dir / f"{date_str}.md"
        path.write_text(
            f"# Daily Summary -- {date_str}\n\n{summary}", encoding="utf-8"
        )

    def load_daily_summary(self, date_str: str) -> Optional[str]:
        path = self.compressed_dir / f"{date_str}.md"
        return path.read_text(encoding="utf-8") if path.exists() else None

    def archive_raw_chat(self, date_str: str):
        raw_file = MEMORY_BASE / "raw_chats" / f"{date_str}.json"
        if not raw_file.exists():
            return
        zip_path = self.archive_dir / f"{date_str}.json.gz"
        with open(raw_file, "rb") as f_in, gzip.open(zip_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        raw_file.unlink()

    def check_and_archive(self, archive_days: int = 30):
        raw_dir = MEMORY_BASE / "raw_chats"
        if not raw_dir.exists():
            return
        for f in raw_dir.glob("*.json"):
            try:
                file_date = datetime.strptime(f.stem, "%Y-%m-%d").date()
                if (date.today() - file_date).days >= archive_days:
                    self.archive_raw_chat(f.stem)
                    logger.info(f"Archived {f.stem}")
            except ValueError:
                continue

    def list_all_summaries(self) -> List[str]:
        return sorted(p.stem for p in self.compressed_dir.glob("*.md"))


# ---------------------------------------------------------------------------
# Profile Manager - auto-updated from interactions
# ---------------------------------------------------------------------------
class ProfileManager:
    def __init__(self):
        self.profile_path = Path.home() / "LLTimmy" / "ben_profile.json"
        self.profile = self._load()

    def _load(self) -> Dict:
        if self.profile_path.exists():
            try:
                return json.loads(self.profile_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "name": "Ben",
            "workflows": [],
            "preferences": {},
            "timmy_view": {},
            "active_projects": [],
            "tools_used": [],
            "frequent_commands": [],
            "last_interaction": None,
        }

    def save(self):
        self.profile["last_interaction"] = datetime.now().isoformat()
        self.profile_path.write_text(
            json.dumps(self.profile, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def update_from_message(self, user_message: str):
        msg_lower = user_message.lower()
        for keyword in ["project", "build", "create", "develop", "working on"]:
            if keyword in msg_lower:
                snippet = user_message[:80]
                if snippet not in self.profile.get("active_projects", []):
                    self.profile.setdefault("active_projects", []).append(snippet)
                    if len(self.profile["active_projects"]) > 20:
                        self.profile["active_projects"] = self.profile["active_projects"][-20:]
                break
        for tool in ["blender", "davinci", "comfyui", "github", "terminal"]:
            if tool in msg_lower:
                tools_used = self.profile.setdefault("tools_used", [])
                if tool not in tools_used:
                    tools_used.append(tool)
        self.save()

    def get_profile(self) -> Dict:
        return self.profile

    def update_preference(self, key: str, value):
        self.profile.setdefault("preferences", {})[key] = value
        self.save()


# ---------------------------------------------------------------------------
# Unified MemoryManager
# ---------------------------------------------------------------------------
class MemoryManager:
    def __init__(self, ollama_host: str = "http://localhost:11434"):
        self.conscious = ConsciousMemory()
        self.subconscious = SubconsciousMemory(ollama_host=ollama_host)
        self.long_term = LongTermMemory()
        self.graph = GraphMemory()
        self.profile = ProfileManager()

    def save_message(self, role: str, content: str, metadata: Dict = None):
        self.conscious.save_message(role, content, metadata)
        self.subconscious.add_message(content, metadata)

    def load_current_day(self) -> List[Dict]:
        return self.conscious.load_current_day()

    def get_subconscious_context(self, query: str) -> List[Dict]:
        return self.subconscious.search(query)

    def create_daily_summary(self, summary_count: int = 15):
        daily_file = self.conscious._daily_path()
        if not daily_file.exists():
            return None
        summary = self.long_term.create_daily_summary(daily_file, summary_count)
        self.long_term.save_daily_summary(summary)
        self.long_term.archive_raw_chat(date.today().isoformat())
        self.long_term.check_and_archive()
        return summary

    def clear_day(self):
        self.conscious.clear_current_day()

    def count_messages(self) -> int:
        return self.conscious.count_current_day()

    def get_all_summaries(self) -> List[str]:
        return self.long_term.list_all_summaries()

    def load_summary(self, date_str: str) -> Optional[str]:
        return self.long_term.load_daily_summary(date_str)

    def search_memory(self, query: str, n: int = 7) -> List[Dict]:
        """Search across all memory layers: subconscious (vector) + conscious (keyword fallback).
        Returns combined results, deduplicated, up to n items."""
        results = self.subconscious.search(query, n)

        # Keyword fallback: search today's conscious memory for exact matches
        # This catches recent messages that may not be embedded yet or when embeddings fail
        query_lower = query.lower().strip()
        query_words = [w for w in query_lower.split() if len(w) >= 3]
        if query_words:
            today_msgs = self.conscious.load_current_day()
            existing_snippets = {r["content"][:80] for r in results}
            for msg in today_msgs:
                content = msg.get("content", "")
                content_lower = content.lower()
                # Match if any query word appears in the message
                if any(word in content_lower for word in query_words):
                    snippet = content[:80]
                    if snippet not in existing_snippets:
                        results.append({
                            "content": content,
                            "metadata": {
                                "timestamp": msg.get("timestamp", ""),
                                "role": msg.get("role", ""),
                                "source": "conscious",
                            },
                        })
                        existing_snippets.add(snippet)
                        if len(results) >= n:
                            break

        return results[:n]

    def delete_memory(self, content: str) -> bool:
        return self.subconscious.delete_by_content(content)

    def update_memory(self, old_content: str, new_content: str) -> bool:
        return self.subconscious.update_memory(old_content, new_content)

    def get_memory_stats(self) -> Dict:
        return {
            "today_messages": self.conscious.count_current_day(),
            "subconscious_entries": self.subconscious.get_count(),
            "graph_entities": len(self.graph.nodes),
            "graph_relationships": len(self.graph.edges),
            "daily_summaries": len(self.long_term.list_all_summaries()),
        }
