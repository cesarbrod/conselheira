#!/usr/bin/env python3
"""Testes dourados da Conselheira 2026 (offline: só busca local, sem LLM).

Garante as travas de grounding:
- termo-âncora exigido por chunk (sem falso positivo de palavra genérica);
- veredito por plano (encontrado | nao_encontrado);
- negativa padronizada, sem fontes-fantasma e sem vazar mecânica do RAG;
- conselheira sem rótulos "Linha N" e sem generalizar entre planos.

Uso:  ./.venv/bin/python -m unittest test_chatbot -v
"""

import re
import unittest

from chatbot import (
    _limpar_rotulo_conselheira,
    responder,
)


def vereditos(resultado):
    return {i["candidato_id"]: i["veredito"] for i in resultado["respostas"]}


class TestTravaTermoAncora(unittest.TestCase):
    def test_animais_lula_sim_flavio_nao(self):
        r = responder(
            "Algum dos planos fala sobre proteção aos animais?",
            modo="comparar", usar_ia=False,
        )
        v = vereditos(r)
        self.assertEqual(v["lula"], "encontrado")
        self.assertEqual(v["flavio"], "nao_encontrado")

    def test_tema_inexistente_ninguem(self):
        r = responder(
            "O que os planos propõem sobre exploração de Marte?",
            modo="comparar", usar_ia=False,
        )
        v = vereditos(r)
        self.assertEqual(v["lula"], "nao_encontrado")
        self.assertEqual(v["flavio"], "nao_encontrado")

    def test_temas_centrais_ambos(self):
        for pergunta in [
            "O que cada candidato propõe para a saúde?",
            "O que cada candidato propõe para a educação?",
            "O que cada candidato propõe para economia e impostos?",
            "O que cada candidato propõe para segurança pública?",
        ]:
            with self.subTest(pergunta=pergunta):
                r = responder(pergunta, modo="comparar", usar_ia=False)
                v = vereditos(r)
                self.assertEqual(v["lula"], "encontrado", pergunta)
                self.assertEqual(v["flavio"], "encontrado", pergunta)


class TestRespostaNegativa(unittest.TestCase):
    def test_sem_fontes_e_sem_vazamento(self):
        r = responder(
            "Algum dos planos fala sobre proteção aos animais?",
            modo="flavio", usar_ia=False,
        )
        item = r["respostas"][0]
        self.assertEqual(item["veredito"], "nao_encontrado")
        self.assertEqual(item["fontes"], [])
        self.assertIn("não trata desse tema", item["resposta"])
        for vazamento in ("trechos fornecidos", "contexto fornecido",
                          "contexto enviado", "documento enviado"):
            self.assertNotIn(vazamento, item["resposta"].lower())
        self.assertNotIn("Plano de Governo, páginas", item["resposta"])

    def test_positiva_tem_fontes_padrao(self):
        r = responder(
            "Algum dos planos fala sobre proteção aos animais?",
            modo="lula", usar_ia=False,
        )
        item = r["respostas"][0]
        self.assertTrue(item["fontes"])
        self.assertRegex(item["resposta"], r"Plano de Governo, páginas \d+")


class TestConselheira(unittest.TestCase):
    def test_divergencia_nao_vira_convergencia(self):
        r = responder(
            "Algum dos planos fala sobre proteção aos animais?",
            modo="comparar", usar_ia=False,
        )
        texto = r["conselheira"]["texto"]
        self.assertIn("Lula", texto)
        self.assertIn("Flávio", texto)
        self.assertNotIn("os dois planos tratam", texto)

    def test_sem_rotulos_linha(self):
        for pergunta in [
            "Algum dos planos fala sobre proteção aos animais?",
            "O que cada candidato propõe para a saúde?",
        ]:
            r = responder(pergunta, modo="comparar", usar_ia=False)
            for linha in r["conselheira"]["texto"].splitlines():
                self.assertFalse(
                    re.match(r"(?i)^\s*linha\s*\d+", linha),
                    f"rótulo proibido em: {linha!r}",
                )

    def test_limpa_rotulos_variados(self):
        casos = {
            "Linha 1: Ambos convergem": "Ambos convergem",
            "**Linha 2:** diverge": "diverge",
            "1. ponto de atenção": "ponto de atenção",
            "- terceira linha": "terceira linha",
            "= Lula menciona, Flávio não": "Lula menciona, Flávio não",
        }
        for entrada, esperado in casos.items():
            self.assertEqual(_limpar_rotulo_conselheira(entrada), esperado)


class TestLimites(unittest.TestCase):
    def test_teto_1500_sem_corte_brusco(self):
        r = responder(
            "O que cada candidato propõe para a saúde?",
            modo="comparar", usar_ia=False,
        )
        for item in r["respostas"]:
            corpo = item["resposta"].split("Plano de Governo, páginas")[0]
            self.assertLessEqual(len(corpo.strip()), 1500)
            if corpo.rstrip().endswith("…"):
                # antes da reticência há palavra completa (sem fragmento)
                self.assertRegex(corpo.rstrip(), r"\w…$")


if __name__ == "__main__":
    unittest.main()
