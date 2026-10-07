#!/usr/bin/env python3
"""Interface web (Flask) do chatbot eleitoral 2026.

Uso:
    pip install -r requirements.txt
    python app.py            # abre http://127.0.0.1:5000
"""

from __future__ import annotations

import os

from flask import Flask, jsonify, render_template, request, session

from chatbot import CANDIDATOS, TEMAS_SUGERIDOS, ollama_modelo_ativo, ollama_modelos, responder

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "conselheira-2026-dev")


@app.get("/")
def index():
    modelos = ollama_modelos()
    return render_template(
        "index.html",
        candidatos=CANDIDATOS,
        temas=TEMAS_SUGERIDOS,
        ia_ativa=bool(os.getenv("OPENAI_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")),
        ollama_modelos=modelos,
        ollama_ativo=ollama_modelo_ativo() if modelos else None,
    )


@app.post("/api/perguntar")
def perguntar():
    dados = request.get_json(force=True, silent=True) or {}
    pergunta = (dados.get("pergunta") or "").strip()
    modo = (dados.get("modo") or "comparar").strip().lower()
    usar_ia = bool(dados.get("usar_ia", True))
    usar_ollama = bool(dados.get("usar_ollama", False))
    if not pergunta:
        return jsonify({"erro": "Digite uma pergunta."}), 400
    if modo not in ("comparar", "lula", "flavio"):
        modo = "comparar"
    resultado = responder(pergunta, modo=modo, usar_ia=usar_ia, usar_ollama=usar_ollama)
    hist = session.get("historico", [])
    hist.append({"pergunta": pergunta, "modo": resultado["modo"]})
    session["historico"] = hist[-50:]
    return jsonify(resultado)


@app.get("/api/ollama")
def ollama_status():
    modelos = ollama_modelos()
    return jsonify({"disponivel": bool(modelos), "modelos": modelos,
                    "ativo": ollama_modelo_ativo() if modelos else None})


@app.get("/api/temas")
def temas():
    return jsonify([{"tema": t, "pergunta": p} for t, p in TEMAS_SUGERIDOS])


@app.get("/api/historico")
def historico():
    return jsonify(session.get("historico", []))


@app.post("/api/limpar")
def limpar():
    session.pop("historico", None)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
