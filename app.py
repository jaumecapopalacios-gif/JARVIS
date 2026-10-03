import os
import secrets
from datetime import datetime

import psycopg2
import psycopg2.extras

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    g
)

from openai import OpenAI


DATABASE_URL = os.getenv("DATABASE_URL", "")

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))


def get_db():
    if "db" not in g:
        g.db = psycopg2.connect(DATABASE_URL, sslmode="require")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query_db(query, args=(), one=False):
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(query, args)
    rows = cur.fetchall()
    cur.close()
    if one:
        return rows[0] if rows else None
    return rows


def execute_db(query, args=()):
    db = get_db()
    cur = db.cursor()
    cur.execute(query, args)
    db.commit()
    cur.close()


def init_db():
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id SERIAL PRIMARY KEY,
            role TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    db.commit()
    cur.close()


def preguntar_jarvis(mensaje):
    api_key = os.getenv("OPENROUTER_API_KEY")

    if not api_key:
        return "No tengo configurada mi clave de OpenRouter."

    # Lista de modelos de respaldo (si uno falla, prueba el siguiente)
    modelos = [
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "google/gemma-3-12b-it:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "openrouter/free"
    ]

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )

    for modelo in modelos:
        try:
            response = client.chat.completions.create(
                model=modelo,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Eres Jarvis, un asistente personal inteligente. "
                            "Hablas en español, eres amigable, directo y útil. "
                            "Respondes de forma clara y breve (máximo 3 oraciones) "
                            "porque tus respuestas se leerán en voz alta. "
                            "Si te piden algo técnico, explica simple."
                        )
                    },
                    {"role": "user", "content": mensaje}
                ],
                temperature=0.7,
                max_tokens=300
            )
            return response.choices[0].message.content
        except Exception as e:
            continue

    return "Lo siento, no pude procesar tu mensaje. Intenta de nuevo."


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/history")
def history():
    mensajes = query_db("""
        SELECT role, message, created_at
        FROM conversations
        ORDER BY id DESC
        LIMIT 100
    """)
    return render_template("history.html", messages=mensajes)


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True) or {}
    mensaje = str(data.get("message", "")).strip()

    if not mensaje:
        return jsonify({"ok": False, "error": "Escribe algo."}), 400

    now = datetime.utcnow().isoformat()

    execute_db("""
        INSERT INTO conversations (role, message, created_at)
        VALUES (%s, %s, %s)
    """, ("user", mensaje, now))

    respuesta = preguntar_jarvis(mensaje)

    execute_db("""
        INSERT INTO conversations (role, message, created_at)
        VALUES (%s, %s, %s)
    """, ("jarvis", respuesta, datetime.utcnow().isoformat()))

    return jsonify({"ok": True, "response": respuesta})


@app.route("/api/clear", methods=["POST"])
def api_clear():
    execute_db("DELETE FROM conversations")
    return jsonify({"ok": True})


@app.route("/health")
def health():
    return jsonify({"status": "online"})


with app.app_context():
    init_db()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
