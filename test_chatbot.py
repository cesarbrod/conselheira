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

import chatbot
from chatbot import (
    _capitalizar_sentencas,
    _conselheira_contradiz_vereditos,
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
        self.assertNotIn("reformul", item["resposta"].lower())
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


class TestValidacaoConselheira(unittest.TestCase):
    ITENS_OPOSTOS = [
        {"candidato_id": "lula", "veredito": "encontrado",
         "fontes": [{"paginas": "69"}]},
        {"candidato_id": "flavio", "veredito": "nao_encontrado", "fontes": []},
    ]

    def test_detecta_convergencia_falsa(self):
        self.assertTrue(_conselheira_contradiz_vereditos(
            "Convergência: Ambos mencionam leis e decretos para proteção animal.",
            self.ITENS_OPOSTOS,
        ))
        self.assertTrue(_conselheira_contradiz_vereditos(
            "Os dois planos tratam do tema com carinho.",
            self.ITENS_OPOSTOS,
        ))

    def test_nao_acusa_divergencia_legitima(self):
        legitimas = [
            "Lula menciona proteção animal, Flávio não aborda o tema.",
            "Só o plano de Lula trata do tema; o de Flávio não trata.",
            "Ponto de atenção: compare os dois planos no portal do TSE.",
        ]
        for texto in legitimas:
            with self.subTest(texto=texto):
                self.assertFalse(_conselheira_contradiz_vereditos(
                    texto, self.ITENS_OPOSTOS))

    def test_vereditos_opostos_nao_chamam_llm(self):
        """Com um lado ausente, a conselheira é determinística (sem LLM).

        Regressão: a LLM alucinava "Ambos buscam/mencionam..." mesmo com
        Flávio NÃO ENCONTRADO — nenhum verbo escaparia se a LLM nem é chamada.
        """
        chamadas_conselheira = []
        original = chatbot._ollama_prompt

        def stub(prompt: str):
            if "SITUAÇÃO APURADA" in prompt:
                chamadas_conselheira.append(prompt)
                return ("Convergência: Ambos buscam a proteção dos animais, "
                        "mas por caminhos diferentes.\n"
                        "Divergência: Nenhuma relevante.\n"
                        "Ponto de atenção: Ler os dois documentos.")
            return ("O plano de Luiz Inácio Lula da Silva prevê o ProPatinhas, "
                    "o SinPatinhas e a Lei AMAR para proteção animal.")

        chatbot._ollama_prompt = stub
        try:
            r = responder(
                "Algum dos planos fala sobre proteção aos animais?",
                modo="comparar", usar_ia=True, usar_ollama=True,
            )
        finally:
            chatbot._ollama_prompt = original
        self.assertEqual(chamadas_conselheira, [])
        cons = r["conselheira"]
        self.assertEqual(cons["via"], "busca-local (veredito)")
        self.assertNotIn("Ambos", cons["texto"])
        self.assertIn("Lula", cons["texto"])
        self.assertIn("Flávio", cons["texto"])

    def test_plano_unico_ausente_nao_chama_llm(self):
        chamadas_conselheira = []
        original = chatbot._ollama_prompt

        def stub(prompt: str):
            if "SITUAÇÃO APURADA" in prompt:
                chamadas_conselheira.append(prompt)
            return "Texto qualquer da LLM."
            # (plano já barrado pela trava; só a conselheira importa aqui)

        chatbot._ollama_prompt = stub
        try:
            r = responder(
                "Algum dos planos fala sobre proteção aos animais?",
                modo="flavio", usar_ia=True, usar_ollama=True,
            )
        finally:
            chatbot._ollama_prompt = original
        self.assertEqual(chamadas_conselheira, [])
        self.assertEqual(r["conselheira"]["via"], "busca-local (veredito)")

    def test_conteudo_dos_dois_lados_usa_llm(self):
        """Sem ausência, a LLM continua sendo usada (sem bypass excessivo)."""
        original = chatbot._ollama_prompt
        chatbot._ollama_prompt = lambda prompt: ("Linha um.\nLinha dois.\nLinha três.")
        try:
            r = responder(
                "O que cada candidato propõe para a saúde?",
                modo="comparar", usar_ia=True, usar_ollama=True,
            )
        finally:
            chatbot._ollama_prompt = original
        self.assertTrue(r["conselheira"]["via"].startswith("ollama:"))


class TestCapitalizacao(unittest.TestCase):
    def test_frase_inicia_maiuscula(self):
        self.assertEqual(
            _capitalizar_sentencas(
                "educação técnica e profissional são centrais para Flávio, "
                "enquanto Lula destaca a infraestrutura e o piso salarial docente."
            ),
            "Educação técnica e profissional são centrais para Flávio, "
            "enquanto Lula destaca a infraestrutura e o piso salarial docente.",
        )

    def test_nova_frase_maiuscula_e_abreviacao_protegida(self):
        self.assertEqual(
            _capitalizar_sentencas("ver p. 69 do plano. siga em frente. ex.: detalhe"),
            "Ver p. 69 do plano. Siga em frente. ex.: detalhe",
        )

    def test_conselheira_sem_minuscula_inicial(self):
        original = chatbot._ollama_prompt
        chatbot._ollama_prompt = lambda prompt: (
            "educação técnica é central para Flávio. lula destaca o piso docente.\n"
            "segunda linha minúscula.\n"
            "terceira linha minúscula."
        )
        try:
            r = responder(
                "O que cada candidato propõe para a educação?",
                modo="comparar", usar_ia=True, usar_ollama=True,
            )
        finally:
            chatbot._ollama_prompt = original
        for linha in r["conselheira"]["texto"].splitlines():
            primeira = re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", linha)
            self.assertTrue(
                primeira and primeira.group(0).isupper(),
                f"linha sem maiúscula inicial: {linha!r}",
            )


class TestEquivalencia(unittest.TestCase):
    def test_alfabetizacao_encontra_variacoes_morfologicas(self):
        """Alfabetização ↔ alfabetizar/alfabetizada: ambos os planos têm."""
        r = responder(
            "O que os planos dizem sobre alfabetização?",
            modo="comparar", usar_ia=False,
        )
        for item in r["respostas"]:
            with self.subTest(candidato=item["candidato_id"]):
                self.assertEqual(item["veredito"], "encontrado")
                self.assertIn("lfabetiz", item["resposta"].lower())
                self.assertNotIn("....", item["resposta"])

    def test_ponte_para_tema_amplo(self):
        r = responder(
            "O que os planos dizem sobre merenda escolar?",
            modo="comparar", usar_ia=False,
        )
        for item in r["respostas"]:
            with self.subTest(candidato=item["candidato_id"]):
                self.assertEqual(item["veredito"], "nao_encontrado")
                self.assertIn("Você gostaria", item["resposta"])
                self.assertIn("educação", item["resposta"])
                self.assertNotIn("reformul", item["resposta"].lower())

    def test_ponte_preserva_acentuacao(self):
        r = responder(
            "O que os planos dizem sobre presídio?",
            modo="flavio", usar_ia=False,
        )
        item = r["respostas"][0]
        self.assertIn("'presídio'", item["resposta"])
        self.assertIn("segurança pública", item["resposta"])

    def test_sem_ponte_sem_oferta(self):
        r = responder(
            "O que os planos propõem sobre exploração de Marte?",
            modo="comparar", usar_ia=False,
        )
        for item in r["respostas"]:
            self.assertNotIn("Você gostaria", item["resposta"])


class TestTemasSugeridos(unittest.TestCase):
    ESPERADO = {
        "Economia e impostos": ("encontrado", "encontrado"),
        "Segurança pública": ("encontrado", "encontrado"),
        "Saúde e SUS": ("encontrado", "encontrado"),
        "Educação e alfabetização": ("encontrado", "encontrado"),
        "Aposentadoria e idosos": ("encontrado", "encontrado"),
        "Salário mínimo": ("encontrado", "encontrado"),
        "Programas sociais": ("encontrado", "encontrado"),
        "Trabalho (6x1, apps)": ("encontrado", "encontrado"),
        "Meio ambiente / Amazônia": ("encontrado", "encontrado"),
        "Democracia e instituições": ("encontrado", "encontrado"),
        # Divergência honesta: Lula cobre, Flávio não (ponte p/ saúde).
        "Farmácia Popular": ("encontrado", "nao_encontrado"),
        "Reforma do Estado": ("encontrado", "encontrado"),
    }

    def test_sugestoes_devolvem_conteudo(self):
        import chatbot as _cb
        titulos = [t for t, _ in _cb.TEMAS_SUGERIDOS]
        self.assertEqual(set(titulos), set(self.ESPERADO))
        for tema, pergunta in _cb.TEMAS_SUGERIDOS:
            with self.subTest(tema=tema):
                r = responder(pergunta, modo="comparar", usar_ia=False)
                v = {i["candidato_id"]: i["veredito"] for i in r["respostas"]}
                self.assertEqual(
                    (v["lula"], v["flavio"]), self.ESPERADO[tema])


class TestModoPerfil(unittest.TestCase):
    PERGUNTAS = [
        ("Sou uma pessoa da classe trabalhadora, com 63 anos, desempregado. "
         "Qual plano de governo mais me atende?",
         ["aposentadoria e idosos", "emprego e trabalho", "programas sociais"]),
        ("Tenho filhos em idade escolar e não ganho o suficiente para poder "
         "estar em casa com eles. Qual plano leva isso em consideração?",
         ["educação", "programas sociais", "salário mínimo"]),
        ("Quero entrar na faculdade, mas não tenho dinheiro sequer para o "
         "transporte, qual dos planos pode me ajudar?",
         ["educação superior", "programas sociais"]),
    ]

    def test_detector(self):
        import chatbot as _cb
        for pergunta, _ in self.PERGUNTAS:
            with self.subTest(pergunta=pergunta[:40]):
                self.assertTrue(_cb.eh_pergunta_perfil(pergunta))
        self.assertFalse(_cb.eh_pergunta_perfil(
            "O que cada candidato propõe para a saúde?"))
        self.assertFalse(_cb.eh_pergunta_perfil(
            "Qual plano melhor garante a aposentadoria dos trabalhadores?"))

    def test_dimensoes(self):
        import chatbot as _cb
        for pergunta, esperadas in self.PERGUNTAS:
            with self.subTest(pergunta=pergunta[:40]):
                dims = _cb.dimensoes_perfil(pergunta)
                for esp in esperadas:
                    self.assertTrue(
                        any(esp in d for d in dims),
                        f"{esp!r} ausente em {dims}",
                    )

    def test_resposta_perfil_offline(self):
        pergunta = self.PERGUNTAS[0][0]
        r = responder(pergunta, modo="comparar", usar_ia=False)
        self.assertTrue(r.get("perfil"))
        self.assertIn("aposentadoria e idosos", r.get("dimensoes", []))
        for item in r["respostas"]:
            self.assertEqual(item["veredito"], "encontrado")
            self.assertIn("Sobre ", item["resposta"])
            self.assertTrue(item["fontes"])
        self.assertEqual(len(r["conselheira"]["texto"].splitlines()), 3)

    def test_endosso_cai_para_revisao(self):
        import chatbot as _cb
        original = _cb._ollama_prompt
        chamadas = []

        def stub(prompt: str):
            if "SITUAÇÃO APURADA" in prompt or "própria situação" in prompt:
                chamadas.append(prompt)
                return ("Primeira linha.\nVote no Lula, é o melhor.\nTerceira linha.")
            return ("O plano prevê ProPatinhas e Auxílio Brasil para o seu caso.")

        _cb._ollama_prompt = stub
        try:
            r = responder(self.PERGUNTAS[0][0], modo="comparar",
                          usar_ia=True, usar_ollama=True)
        finally:
            _cb._ollama_prompt = original
        cons = r["conselheira"]
        self.assertEqual(cons["via"], "busca-local (revisão)")
        self.assertNotIn("Vote", cons["texto"])


    def test_cuidadora_animais_dimensoes_e_vereditos(self):
        import chatbot as _cb
        pergunta = ("Sou uma mulher solteira, de 24 anos, protetora e cuidadora "
                    "de animais em situação de risco. Qual plano de governo é "
                    "melhor para mim?")
        self.assertTrue(_cb.eh_pergunta_perfil(pergunta))
        dims = _cb.dimensoes_perfil(pergunta)
        self.assertTrue(any("animais" in d for d in dims))
        self.assertTrue(any("mulheres" in d for d in dims))
        r = responder(pergunta, modo="comparar", usar_ia=False)
        v = {i["candidato_id"]: i["vereditos_dimensoes"] for i in r["respostas"]}
        self.assertEqual(v["lula"]["proteção aos animais"], "encontrado")
        self.assertEqual(v["flavio"]["proteção aos animais"], "nao_encontrado")
        self.assertEqual(v["lula"]["as mulheres"], "encontrado")
        self.assertEqual(v["flavio"]["as mulheres"], "encontrado")

    def test_singular_consoante_es(self):
        import chatbot as _cb
        self.assertIn("mulher", _cb._singularizar("mulheres"))
        self.assertIn("trabalhador", _cb._singularizar("trabalhadores"))


if __name__ == "__main__":
    unittest.main()
