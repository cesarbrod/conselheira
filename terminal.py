#!/usr/bin/env python3
"""Interface de terminal do chatbot eleitoral 2026.

Uso:
    python terminal.py                    # modo interativo
    python terminal.py --modo lula        # filtra um candidato
    python terminal.py --sem-ia           # só busca local, sem chamar nenhuma IA
    python terminal.py --local            # usa seu Ollama local (nada vai p/ nuvem)
    OLLAMA_MODEL=qwen2.5:7b python terminal.py --local   # escolhe o modelo
"""

from __future__ import annotations

import argparse
import os
import shutil
import textwrap

from chatbot import CANDIDATOS, TEMAS_SUGERIDOS, detectar_modo, ollama_modelo_ativo, ollama_modelos, responder

BANNER = """
================================================================
  CONSELHEIRA 2026 — Chatbot dos Planos de Governo (Presidente)
  Fontes oficiais: TSE — Lula (PT, 84 p.) x Flávio Bolsonaro (PL, 76 p.)
  Digite 'temas' p/ sugestões | 'modo X' p/ trocar | 'sair' p/ encerrar
================================================================
"""

AJUDA_MODO = "Modos: comparar | lula | flavio"


def mostrar_temas():
    print("\n--- Temas sugeridos (digite o número ou a pergunta) ---")
    for i, (tema, pergunta) in enumerate(TEMAS_SUGERIDOS, 1):
        print(f"  {i}. [{tema}] {pergunta}")
    print()


def largura_util() -> int:
    """Largura efetiva do terminal: respeita a janela real, com teto p/ legibilidade."""
    try:
        colunas = shutil.get_terminal_size((80, 20)).columns
    except OSError:
        colunas = 80
    return max(40, min(colunas - 2, 100))


def imprimir_quebrado(texto: str, largura: int | None = None):
    """Imprime respeitando a largura da tela sem cortar palavras no meio.

    break_long_words=False garante que uma palavra nunca seja partida;
    só uma palavra maior que a linha inteira estoura (caso raro e inevitável).
    """
    largura = largura or largura_util()
    for linha in texto.split("\n"):
        if not linha.strip():
            print()
            continue
        for pedaço in textwrap.wrap(
            linha.strip(),
            width=largura,
            break_long_words=False,
            break_on_hyphens=False,
            replace_whitespace=False,
            drop_whitespace=True,
        ) or [""]:
            print(pedaço)


def mostrar_resposta(resultado: dict):
    largura = largura_util()
    if resultado.get("perfil") and resultado.get("dimensoes"):
        print()
        imprimir_quebrado("(perfil detectado — dimensões analisadas: "
                          + "; ".join(resultado["dimensoes"]) + ")", largura)
    for item in resultado["respostas"]:
        meta = CANDIDATOS[item["candidato_id"]]
        print(f"\n{meta['icone']} {meta['curto']}\n")
        imprimir_quebrado(item["resposta"], largura)
    conselheira = resultado.get("conselheira") or {}
    if conselheira.get("texto"):
        print("\n🔮 Conselheira\n")
        imprimir_quebrado(conselheira["texto"], largura)
    print()
    imprimir_quebrado("⚠️  Respostas geradas a partir dos PDFs oficiais do TSE. Confira sempre o documento original.", largura)


def main():
    ap = argparse.ArgumentParser(description="Chatbot eleitoral 2026 (terminal)")
    ap.add_argument("--modo", default="comparar", choices=["comparar", "lula", "flavio"])
    ap.add_argument("--sem-ia", action="store_true", help="desativa qualquer IA, usa só busca local")
    ap.add_argument("--local", action="store_true",
                    help="usa o Ollama da sua máquina (OLLAMA_URL/OLLAMA_MODEL); nada vai para a nuvem")
    args = ap.parse_args()

    usar_ollama = args.local
    usar_ia = not args.sem_ia
    if usar_ollama:
        modelo = ollama_modelo_ativo()
        if modelo:
            print(f"(Ollama local ativo — modelo: {modelo}. Instalados: {', '.join(ollama_modelos())})")
        else:
            print("(Ollama não detectado — as respostas sairão em modo busca local. "
                  "Suba com `ollama serve` e `ollama pull <modelo>`.)")
    elif usar_ia and not (os.getenv("OPENAI_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        print("(sem chave de IA detectada — usando busca local com citações literais. "
              "Defina OPENAI_API_KEY ou GEMINI_API_KEY, ou use --local com Ollama.)")

    modo = args.modo
    historico: list[dict] = []
    print(BANNER)
    print(AJUDA_MODO + f" | modo atual: {modo}\n")
    mostrar_temas()

    while True:
        try:
            entrada = input(f"[{modo}] você> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAté logo!")
            break
        if not entrada:
            continue
        low = entrada.lower()
        if low in ("sair", "exit", "quit", "q"):
            print("Até logo! Confira os planos no portal do TSE.")
            break
        if low in ("temas", "ajuda", "help", "?"):
            mostrar_temas()
            continue
        if low.startswith("modo "):
            novo = low.split("modo ", 1)[1].strip()
            if novo in ("comparar", "lula", "flavio"):
                modo = novo
                print(f"Modo alterado para: {modo}")
            else:
                print(AJUDA_MODO)
            continue
        if low == "historico":
            print(f"\n--- Histórico ({len(historico)} perguntas) ---")
            for h in historico:
                print(f"  • [{h['modo']}] {h['pergunta']}")
            print()
            continue
        # atalho numérico para temas
        if entrada.isdigit() and 1 <= int(entrada) <= len(TEMAS_SUGERIDOS):
            entrada = TEMAS_SUGERIDOS[int(entrada) - 1][1]
            print(f"(tema selecionado) você> {entrada}")

        modo_efetivo = modo
        if modo == "comparar" and low.startswith(("e o lula", "só lula", "so lula", "e lula")):
            modo_efetivo = "lula"
        resultado = responder(entrada, modo=modo_efetivo, usar_ia=usar_ia, usar_ollama=usar_ollama)
        historico.append({"pergunta": entrada, "modo": resultado["modo"]})
        mostrar_resposta(resultado)


if __name__ == "__main__":
    main()
