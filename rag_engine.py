import os
import uuid
import json
import logging
import time
from typing import Generator

import cohere
from groq import Groq
from dotenv import load_dotenv

from llama_index.core import VectorStoreIndex, StorageContext
from llama_index.core.settings import Settings
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.vector_stores import SimpleVectorStore
from llama_index.embeddings.cohere import CohereEmbedding
from llama_index.postprocessor.cohere_rerank import CohereRerank

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

COHERE_API_KEY  = os.getenv("COHERE_API_KEY")
GROQ_API_KEY    = os.getenv("GROQ_API_KEY")
COLLECTION_NAME = "documind_store"

# ── Clients ───────────────────────────────────────────────────────────────────
co   = cohere.Client(api_key=COHERE_API_KEY)
groq = Groq(api_key=GROQ_API_KEY)

# ── Embeddings (Cohere) ───────────────────────────────────────────────────────
Settings.embed_model = CohereEmbedding(
    cohere_api_key=COHERE_API_KEY,
    model_name="embed-v4.0",
    input_type="search_document",
    timeout=45.0,
    max_retries=2,
)
Settings.llm = None   # We handle LLM calls ourselves

# ── ChromaDB ──────────────────────────────────────────────────────────────────
# Render's free service has no persistent disk; keep this demo index in memory.
vector_store      = SimpleVectorStore()
storage_context   = StorageContext.from_defaults(vector_store=vector_store)

# ── Reranker (Cohere) ─────────────────────────────────────────────────────────
reranker = CohereRerank(api_key=COHERE_API_KEY, top_n=3)

# ── Global state ──────────────────────────────────────────────────────────────
_index: VectorStoreIndex | None = None
_chat_history: list[dict] = []
_docs_meta: list[dict] = []


# ── Doc metadata persistence ──────────────────────────────────────────────────
def _load_docs_meta() -> list[dict]:
    return list(_docs_meta)


def _save_docs_meta(docs: list[dict]):
    global _docs_meta
    _docs_meta = list(docs)


# ── Index helper ──────────────────────────────────────────────────────────────
def _get_index() -> VectorStoreIndex:
    global _index
    if _index is None:
        _index = VectorStoreIndex([], storage_context=storage_context)
    return _index


def _build_messages(system: str, question: str) -> list[dict]:
    messages = [{"role": "system", "content": system}]
    for m in _chat_history[-6:]:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": question})
    return messages


# ── Public API ────────────────────────────────────────────────────────────────

def _extract_text(filepath: str, filename: str) -> list:
    """Extract clean text. Uses pdfminer for PDFs, plain read for txt."""
    from llama_index.core.schema import Document as LIDocument

    if filename.lower().endswith(".pdf"):
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LTTextContainer
        pages = []
        for page_num, page_layout in enumerate(extract_pages(filepath), start=1):
            page_text = ""
            for element in page_layout:
                if isinstance(element, LTTextContainer):
                    page_text += element.get_text()
            if page_text.strip():
                pages.append(LIDocument(
                    text=page_text,
                    metadata={"source": filename, "page": page_num, "doc_id": str(uuid.uuid4())}
                ))
        if not pages:
            raise ValueError(
                "No readable text was found in this PDF. It may be scanned; run OCR on it and upload the searchable PDF."
            )
        return pages
    else:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        if not text.strip():
            raise ValueError("This text file is empty or contains no readable text.")
        return [LIDocument(text=text, metadata={"source": filename, "doc_id": str(uuid.uuid4())})]


def ingest_document(filepath: str, filename: str) -> dict:
    """Parse, chunk, embed and persist a new document."""
    started = time.monotonic()
    logger.info("Upload %s: extracting text", filename)
    documents = _extract_text(filepath, filename)
    logger.info("Upload %s: extracted %s pages", filename, len(documents))

    for doc in documents:
        doc.metadata["source"] = filename
        doc.metadata["doc_id"] = str(uuid.uuid4())

    splitter = SentenceSplitter(chunk_size=512, chunk_overlap=64)
    nodes    = splitter.get_nodes_from_documents(documents)
    logger.info("Upload %s: created %s chunks; embedding and saving", filename, len(nodes))

    _get_index().insert_nodes(nodes)
    logger.info("Upload %s: indexing finished in %.1f seconds", filename, time.monotonic() - started)

    meta = {
        "id":      str(uuid.uuid4()),
        "name":    filename,
        "chunks":  len(nodes),
        "pages":   len(documents),
        "size_kb": round(os.path.getsize(filepath) / 1024, 1),
    }
    docs = _load_docs_meta()
    docs.append(meta)
    _save_docs_meta(docs)
    return meta


def query_stream(question: str) -> Generator[str, None, None]:
    """Retrieve → rerank → stream answer via Groq."""
    retriever = _get_index().as_retriever(similarity_top_k=8)
    nodes     = retriever.retrieve(question)
    reranked  = reranker.postprocess_nodes(nodes, query_str=question)

    context_parts, sources = [], []
    for n in reranked:
        context_parts.append(n.get_content())
        src = n.metadata.get("source", "unknown")
        if src not in sources:
            sources.append(src)

    system_prompt = (
        "You are DocuMind, a precise document intelligence assistant.\n"
        "Answer ONLY from the context below. Be concise but thorough.\n"
        "If the answer is not in the context, say exactly: "
        "'I could not find this information in the uploaded documents.'\n\n"
        "Context:\n"
        "---------------------\n"
        + "\n\n---\n\n".join(context_parts)
        + "\n---------------------"
    )

    messages = _build_messages(system_prompt, question)

    full_answer = ""
    stream = groq.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=messages,
        temperature=0.3,
        max_tokens=1024,
        stream=True,
    )

    for chunk in stream:
        token = chunk.choices[0].delta.content
        if token:
            full_answer += token
            yield token

    if sources:
        src_text = "\n\n📎 **Sources:** " + ", ".join(sources)
        yield src_text
        full_answer += src_text

    _chat_history.append({"role": "user",      "content": question})
    _chat_history.append({"role": "assistant",  "content": full_answer})


def get_uploaded_docs() -> list[dict]:
    return _load_docs_meta()


def clear_history():
    global _chat_history
    _chat_history = []


def delete_all_documents():
    global _index, _chat_history, _docs_meta, vector_store, storage_context
    vector_store      = SimpleVectorStore()
    storage_context   = StorageContext.from_defaults(vector_store=vector_store)
    _index        = None
    _chat_history = []
    _docs_meta    = []