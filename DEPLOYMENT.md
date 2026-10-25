# DocuMind deployment guide

## How the app works

1. Flask in `app.py` serves the interface and accepts uploads and questions.
2. `rag_engine.py` extracts text, divides it into chunks, and asks Cohere for embeddings.
3. ChromaDB stores vectors in `chroma_store/` so the app can find relevant passages.
4. Cohere reranks those passages and Groq streams an answer back to the browser.

Cohere and Groq API keys are required. Keep them in Render's environment settings. Never commit keys to GitHub or put them in browser JavaScript.

## Render configuration

Create a Python web service from this repository and the `main` branch.

- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT app:app`
- Environment variables: `COHERE_API_KEY` and `GROQ_API_KEY`

## Free tier limits and document privacy

Render free web services sleep after 15 minutes without incoming traffic. Their local filesystem is temporary, so uploaded files, Chroma vectors, and metadata can disappear after a restart, redeploy, or sleep. This is suitable for a learning demo, not durable document storage.

This app has no login. Anyone with its public URL can upload documents and clear the shared collection. Do not upload private documents or share the URL broadly until authentication and per-user storage are added.
