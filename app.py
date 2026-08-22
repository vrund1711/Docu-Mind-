import os
import json
from flask import Flask, render_template, request, Response, jsonify, stream_with_context
from werkzeug.utils import secure_filename
from rag_engine import ingest_document, query_stream, get_uploaded_docs, clear_history, delete_all_documents

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = "data"
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024   # 32 MB limit
ALLOWED_EXTENSIONS = {"txt", "pdf"}

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ── Pages ──────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── Upload ─────────────────────────────────────────────────────────────────────

@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "Only .txt and .pdf files are allowed"}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(filepath)

    try:
        meta = ingest_document(filepath, filename)
        return jsonify({"success": True, "doc": meta})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Streaming Query ─────────────────────────────────────────────────────────────

@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json(force=True)
    question = (data.get("question") or "").strip()

    if not question:
        return jsonify({"error": "Empty question"}), 400

    docs = get_uploaded_docs()
    if not docs:
        return jsonify({"error": "No documents uploaded yet. Please upload a document first."}), 400

    def generate():
        try:
            for token in query_stream(question):
                # SSE format
                payload = json.dumps({"token": token})
                yield f"data: {payload}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Document list ───────────────────────────────────────────────────────────────

@app.route("/docs", methods=["GET"])
def list_docs():
    return jsonify({"docs": get_uploaded_docs()})


# ── Clear history ───────────────────────────────────────────────────────────────

@app.route("/clear", methods=["POST"])
def clear():
    clear_history()
    return jsonify({"success": True})


# ── Delete all docs ─────────────────────────────────────────────────────────────

@app.route("/delete-all", methods=["POST"])
def delete_all():
    delete_all_documents()
    return jsonify({"success": True})


if __name__ == "__main__":
    app.run(debug=True, threaded=True)