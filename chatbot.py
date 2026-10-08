"""
Núcleo do chatbot eleitoral — Eleições 2026 (Presidente do Brasil).

Fontes oficiais (TSE):
- Lula (PT): https://www.tse.jus.br/eleicoes/eleicoes-2026-content/propostas-de-governo-dos-candidatos-ao-cargo-de-presidente-da-republica-eleicoes-2026/lula-propostas-de-governo
- Flávio Bolsonaro (PL): https://www.tse.jus.br/eleicoes/eleicoes-2026-content/propostas-de-governo-dos-candidatos-ao-cargo-de-presidente-da-republica-eleicoes-2026/flavio-bolsonaro

Estratégia:
1. Busca local (TF-IDF artesanal, sem dependências, offline e grátis) — sempre funciona.
2. Se houver OPENAI_API_KEY ou GEMINI_API_KEY/GOOGLE_API_KEY, o texto recuperado
   é enviado ao LLM para redação final (RAG). Caso contrário, usa resposta extrativa.
"""

from __future__ import annotations

import json
import math
import os
import re
import unicodedata
import urllib.request
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

CANDIDATOS = {
    "lula": {
        "id": "lula",
        "nome": "Luiz Inácio Lula da Silva",
        "apelido": "Lula",
        "curto": "Lula",
        "icone": "⭐",
        "partido": "PT (coligação: PT, PSB, PCdoB, PV, PDT, PSOL/Rede)",
        "arquivo_txt": "plano-lula.txt",
        "arquivo_pdf": "plano-lula-tse.pdf",
        "paginas": 84,
        "titulo_plano": "Diretrizes para o Programa de Transformação do Brasil",
        "tse_url": "https://www.tse.jus.br/eleicoes/eleicoes-2026-content/propostas-de-governo-dos-candidatos-ao-cargo-de-presidente-da-republica-eleicoes-2026/lula-propostas-de-governo",
    },
    "flavio": {
        "id": "flavio",
        "nome": "Flávio Bolsonaro",
        "apelido": "Flávio Bolsonaro",
        "curto": "Flávio",
        "icone": "💀",
        "partido": "PL – Partido Liberal",
        "arquivo_txt": "plano-flavio-bolsonaro.txt",
        "arquivo_pdf": "plano-flavio-bolsonaro-tse.pdf",
        "paginas": 76,
        "titulo_plano": "Plano de Governo Flávio Bolsonaro (PL) — Um novo caminho",
        "tse_url": "https://www.tse.jus.br/eleicoes/eleicoes-2026-content/propostas-de-governo-dos-candidatos-ao-cargo-de-presidente-da-republica-eleicoes-2026/flavio-bolsonaro",
    },
}

TEMAS_SUGERIDOS = [
    ("Economia e impostos", "O que cada candidato propõe para economia e impostos?"),
    ("Segurança pública", "O que cada candidato propõe para segurança pública?"),
    ("Saúde", "O que cada candidato propõe para a saúde (SUS, filas, telemedicina)?"),
    ("Educação", "O que cada candidato propõe para educação e alfabetização?"),
    ("Trabalho (6x1, apps)", "O que cada candidato propõe sobre jornada de trabalho e trabalhadores de aplicativos?"),
    ("Meio ambiente / Amazônia", "O que cada candidato propõe para meio ambiente e Amazônia?"),
    ("Programas sociais", "Os candidatos vão manter os programas sociais? O que muda?"),
    ("Reforma do Estado", "O que cada candidato propõe sobre ministérios, reforma administrativa e teto de gastos?"),
    ("STF / instituições", "O que o plano de Flávio Bolsonaro propõe sobre o STF? E o de Lula sobre democracia?"),
]

def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def _norm_set(palavras: str) -> set[str]:
    return {_sem_acento(w.lower()) for w in palavras.split()}


STOPWORDS = _norm_set(
    """a ao aos aquela aquelas aquele aqueles aquilo as ate até com como da das de do dos
    e ela elas ele eles em entre era eram essa essas esse esses esta estas este estes
    foi foram há isso isto já lhe lhes mais mas me mesmo minha minhas meu meus na nas
    nao não nem no nos nossa nossas nosso nossos o os ou para pela pelas pelo pelos
    por qual quais que quem se sem ser seu seus sobre sua suas também tem têm terá
    uma umas um uns vez vão vai vou onde qual cada entre sobre sob muito mais menos
    muito pode podem deve devem vai ser são foi como quais dos das qual"""
)


@dataclass
class Trecho:
    candidato_id: str
    texto: str
    pag_inicio: int
    pag_fim: int
    score: float = 0.0


def _tokenizar(texto: str) -> list[str]:
    toks = re.findall(r"[a-z0-9]+", _sem_acento(texto.lower()))
    return [t for t in toks if t not in STOPWORDS and len(t) > 2]


# Termos que aparecem em quase toda pergunta ("o que o plano propõe...")
# e poluiriam o ranking se contassem pontos — removidos só da consulta.
IGNORAR_NA_PERGUNTA = _norm_set(
    """candidato candidata candidatos cada plano planos governo governos proposta propostas
    propoe propoem propoem-se diz dizem fala falam preve prevê tema temas sobre quais
    qual como presidente brasil brasileiro brasileira brasileiros algum alguma alguns
    algumas nenhum nenhuma todo toda todos todas outro outra outros outras mesmo mesma
    proprios proprias tal tais algo alguem ninguem tudo nada quanto quanta quantos
    quantas menciona mencionam garante garantem traz trazem melhor melhores"""
)


# Siglas e apelidos frequentes nas perguntas → termos usados nos documentos.
SINONIMOS = {
    "stf": ["supremo", "tribunal", "constituicao", "judiciario"],
    "supremo": ["supremo", "tribunal", "constituicao"],
    "sus": ["saude", "sus", "hospital", "medico"],
    "6x1": ["jornada", "escala", "trabalho"],
    "aplicativo": ["aplicativo", "plataforma", "entregador"],
    "app": ["aplicativo", "plataforma"],
    "apps": ["aplicativo", "plataforma"],
    "imposto": ["imposto", "tributo", "tributario"],
    "impostos": ["imposto", "tributo", "tributario"],
    "teto": ["teto", "fiscal", "divida", "gasto"],
    "amazonia": ["amazonia", "ambiental", "ambiente", "floresta"],
    "escola": ["escola", "educacao", "ensino", "alfabetizacao"],
    "faccao": ["faccao", "crime", "criminoso"],
    "policia": ["policia", "seguranca", "crime"],
    "ministerio": ["ministerio", "estado", "gestao", "administracao", "servidor"],
    "ministerios": ["ministerio", "estado", "gestao", "administracao"],
    "reforma": ["reforma", "modernizacao", "modernizar"],
    "social": ["social", "sociais", "bolsa", "familia", "assistencia"],
    "sociais": ["social", "sociais", "bolsa", "familia", "assistencia"],
    "meio": ["meio", "clima", "desmatamento", "transicao", "sustentavel"],
    "ambiente": ["ambiente", "clima", "desmatamento", "sustentavel", "ecologia"],
    "amazonia": ["amazonia", "ambiental", "ambiente", "floresta", "desmatamento", "clima"],
    "educacao": ["educacao", "ensino", "escola", "alfabetizacao", "professor"],
    "saude": ["saude", "sus", "hospital", "medico", "vacina"],
}


def _tokenizar_pergunta(pergunta: str) -> list[str]:
    base = [t for t in _tokenizar(pergunta) if t not in IGNORAR_NA_PERGUNTA]
    expandidos: list[str] = list(base)
    for t in base:
        for s in SINONIMOS.get(t, []):
            if s not in expandidos:
                expandidos.append(s)
        # plural -> singular ("ministérios" também casa "ministério")
        if len(t) > 4 and t.endswith("s") and not t.endswith("ss"):
            sing = t[:-1]
            if sing not in expandidos:
                expandidos.append(sing)
    return expandidos


class BaseConhecimento:
    """Carrega os .txt extraídos dos PDFs e indexa em chunks com página."""

    def __init__(self, data_dir: Path = DATA_DIR):
        self.data_dir = data_dir
        self.chunks: list[Trecho] = []
        self._idf: dict[str, float] = {}
        self._carregar()

    def _carregar(self):
        for cid, meta in CANDIDATOS.items():
            path = self.data_dir / meta["arquivo_txt"]
            if not path.exists():
                continue
            texto = path.read_text(encoding="utf-8")
            # Divide por marcadores de página
            partes = re.split(r"===== PÁGINA (\d+) =====", texto)
            # partes[0] = preâmbulo, depois pares (num, conteúdo)
            paginas: list[tuple[int, str]] = []
            for i in range(1, len(partes), 2):
                try:
                    num = int(partes[i])
                except ValueError:
                    continue
                conteudo = partes[i + 1] if i + 1 < len(partes) else ""
                conteudo = re.sub(r"\n{3,}", "\n\n", conteudo.strip())
                if len(conteudo) > 50:
                    paginas.append((num, conteudo))
            # Agrupa ~1-2 páginas por chunk (alvo ~1400 chars)
            buf, p_ini, p_fim = "", 0, 0
            for num, conteudo in paginas:
                if not buf:
                    p_ini = num
                buf += f"\n{conteudo}"
                p_fim = num
                if len(buf) >= 1400:
                    self.chunks.append(Trecho(cid, buf.strip()[:6000], p_ini, p_fim))
                    buf, p_ini, p_fim = "", 0, 0
            if buf.strip():
                self.chunks.append(Trecho(cid, buf.strip()[:6000], p_ini, p_fim or p_ini))
        self._calcular_idf()

    def _calcular_idf(self):
        df: dict[str, int] = {}
        for ch in self.chunks:
            for tok in set(_tokenizar(ch.texto)):
                df[tok] = df.get(tok, 0) + 1
        n = max(len(self.chunks), 1)
        self._idf = {t: math.log((n + 1) / (f + 1)) + 1.0 for t, f in df.items()}

    def buscar(self, pergunta: str, candidato_id: str | None = None, top_k: int = 3) -> list[Trecho]:
        qtoks = _tokenizar_pergunta(pergunta)
        if not qtoks:
            return []
        resultados: list[Trecho] = []
        for ch in self.chunks:
            if candidato_id and ch.candidato_id != candidato_id:
                continue
            toks = _tokenizar(ch.texto)
            if not toks:
                continue
            tf: dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            score = sum(tf.get(q, 0) * self._idf.get(q, 1.0) for q in qtoks)
            # bônus por frase literal / cobertura de termos
            cobertura = sum(1 for q in set(qtoks) if q in tf) / max(len(set(qtoks)), 1)
            score *= 0.5 + cobertura
            # normalização por tamanho (evita que sumários/índices longos dominem)
            score /= (1.0 + math.log(1 + len(toks)) / 4.0)
            # penaliza páginas de índice/sumário (cheias de ".....")
            if ch.texto.count("....") >= 5:
                score *= 0.15
            # bônus por expressões exatas da pergunta ("segurança pública",
            # "reforma administrativa" valem mais que os termos isolados)
            norm = re.sub(r"\s+", " ", _sem_acento(ch.texto.lower()))
            for a, b in zip(qtoks, qtoks[1:]):
                if f"{a} {b}" in norm:
                    score += 6.0
            low = ch.texto.lower()
            if len(pergunta.strip()) > 12 and pergunta.lower().strip() in low:
                score += 5.0
            if score > 0:
                resultados.append(Trecho(ch.candidato_id, ch.texto, ch.pag_inicio, ch.pag_fim, score))
        resultados.sort(key=lambda r: r.score, reverse=True)
        return resultados[:top_k]


BASE = None


def get_base() -> BaseConhecimento:
    global BASE
    if BASE is None:
        BASE = BaseConhecimento()
    return BASE


# ---------------- LLM (opcional) ----------------

# Modelos Gemini testados em ordem, caso o padrão tenha sido descontinuado
# ou não exista no projeto da chave. Defina GEMINI_MODEL para forçar um só.
MODELOS_GEMINI_PADRAO = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]

# Ollama local. OLLAMA_MODEL vazio = usa o 1º modelo instalado detectado via /api/tags.
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "180"))

_aviso_ia_exibido = False


def _avisar_ia_uma_vez(mensagem: str):
    """A falha da IA é esperada sem chave válida — avisa 1x e segue no modo local."""
    global _aviso_ia_exibido
    if not _aviso_ia_exibido:
        _aviso_ia_exibido = True
        print(f"[aviso] {mensagem} — usando busca local com citações. "
              f"Use --sem-ia para silenciar ou verifique a chave/modelo.")


def _post_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 60) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


LIMITE_RESPOSTA = 1500  # teto de caracteres do texto conclusivo (linha de fontes à parte)


def _prompt_rag(pergunta: str, contexto: str, candidato_nome: str) -> str:
    return (
        f"Você é um assistente eleitoral neutro e imparcial. Responda ESTRITAMENTE com base "
        f"no contexto oficial do plano de governo de {candidato_nome} (TSE, Eleições 2026).\n"
        f"Regras: responda em português, em texto único, consolidado e conclusivo "
        f"(sintetize as principais propostas, sem transcrever trechos longos e sem listas extensas); "
        f"no MÁXIMO {LIMITE_RESPOSTA} caracteres; se o contexto não contiver a resposta, "
        f"diga isso claramente e não invente; não faça propaganda nem juízo de valor; "
        f"fale sempre como 'o plano de {candidato_nome}' — NUNCA mencione 'trechos', "
        f"'contexto fornecido/enviado' ou outra mecânica interna, e nunca diga 'os planos' "
        f"(responda apenas por este plano); "
        f"não inclua seção de fontes (ela será acrescentada depois).\n\n"
        f"CONTEXTO:\n{contexto}\n\nPERGUNTA: {pergunta}"
    )


def _linha_fontes(meta: dict, trechos: list["Trecho"]) -> str:
    pags: list[str] = []
    for t in trechos:
        rotulo = f"{t.pag_inicio}-{t.pag_fim}" if t.pag_fim != t.pag_inicio else f"{t.pag_inicio}"
        if rotulo not in pags:
            pags.append(rotulo)
    return f"Plano de Governo, páginas {', '.join(pags)}"


def _finalizar_resposta(texto: str, meta: dict, trechos: list["Trecho"]) -> str:
    """Garante teto de 1500 chars e linha de fontes padronizada ao final."""
    # remove eventual linha de fontes que o modelo tenha incluído (será padronizada abaixo)
    texto = re.sub(r"(?im)^\s*(fontes?\s*:|plano de governo\s*,?\s*páginas?\s*).*$", "", texto).strip()
    texto = _limitar_texto(texto)
    if trechos:
        texto = texto.rstrip() + "\n\n" + _linha_fontes(meta, trechos)
    return texto


def _chamar_openai(pergunta: str, contexto: str, candidato_nome: str) -> str | None:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return None
    return _openai_prompt(_prompt_rag(pergunta, contexto, candidato_nome))


def _openai_prompt(prompt: str) -> str | None:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return None
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    try:
        out = _post_json(
            "https://api.openai.com/v1/chat/completions",
            {"model": model, "temperature": 0.2,
             "messages": [{"role": "user", "content": prompt}]},
            headers={"Authorization": f"Bearer {key}"},
        )
        return out["choices"][0]["message"]["content"].strip()
    except Exception as e:
        _avisar_ia_uma_vez(f"OpenAI indisponível ({e})")
        return None


def _chamar_gemini(pergunta: str, contexto: str, candidato_nome: str) -> str | None:
    key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    if not key:
        return None
    return _gemini_prompt(_prompt_rag(pergunta, contexto, candidato_nome))


def _gemini_prompt(prompt: str) -> str | None:
    key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    if not key:
        return None
    fixo = os.getenv("GEMINI_MODEL", "").strip()
    modelos = [fixo] if fixo else list(MODELOS_GEMINI_PADRAO)
    ultimo_erro: Exception | None = None
    for model in modelos:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            out = _post_json(url, {"contents": [{"parts": [{"text": prompt}]}],
                                   "generationConfig": {"temperature": 0.2}},
                             headers={"x-goog-api-key": key})
            return out["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception as e:
            ultimo_erro = e
            # 404 = modelo inexistente/descontinuado -> tenta o próximo da lista
            if "404" not in str(e) or fixo:
                break
    _avisar_ia_uma_vez(f"Gemini indisponível ({ultimo_erro})")
    return None


def _ollama_base() -> str:
    return os.getenv("OLLAMA_URL", "http://localhost:11434").strip().rstrip("/") or "http://localhost:11434"


_cache_modelos_ollama: list[str] | None = None


def ollama_modelos() -> list[str]:
    """Modelos instalados no Ollama local (via GET /api/tags). [] se fora do ar."""
    global _cache_modelos_ollama
    if _cache_modelos_ollama is not None:
        return _cache_modelos_ollama
    try:
        req = urllib.request.Request(f"{_ollama_base()}/api/tags")
        with urllib.request.urlopen(req, timeout=10) as resp:
            dados = json.loads(resp.read().decode("utf-8"))
        _cache_modelos_ollama = [m.get("name", "") for m in dados.get("models", []) if m.get("name")]
    except Exception:
        _cache_modelos_ollama = []
    return _cache_modelos_ollama


def ollama_modelo_ativo() -> str | None:
    """Modelo que será usado: OLLAMA_MODEL, ou o 1º instalado. None se nada disponível."""
    fixo = os.getenv("OLLAMA_MODEL", "").strip()
    if fixo:
        return fixo
    modelos = ollama_modelos()
    return modelos[0] if modelos else None


def _chamar_ollama(pergunta: str, contexto: str, candidato_nome: str) -> str | None:
    model = ollama_modelo_ativo()
    if not model:
        _avisar_ia_uma_vez("Ollama fora do ar ou sem modelos instalados "
                           f"({_ollama_base()}; rode `ollama pull <modelo>` e `ollama serve`)")
        return None
    return _ollama_prompt(_prompt_rag(pergunta, contexto, candidato_nome))


def _ollama_prompt(prompt: str) -> str | None:
    model = ollama_modelo_ativo()
    if not model:
        return None
    try:
        out = _post_json(
            f"{_ollama_base()}/api/chat",
            {"model": model, "stream": False,
             "messages": [{"role": "user", "content": prompt}],
             "options": {"temperature": 0.2}},
            timeout=OLLAMA_TIMEOUT,
        )
        return out["message"]["content"].strip()
    except Exception as e:
        _avisar_ia_uma_vez(f"Ollama indisponível ({e})")
        return None


def _melhor_recorte(texto: str, qtokens: list[str], limite: int = 850) -> str:
    """Janela mais relevante: grupo de frases com maior densidade dos termos da pergunta."""
    frases = [f.strip() for f in re.split(r"(?<=[.!?…])\s+|\n+", texto) if len(f.strip()) > 30]
    if not frases:
        recorte = texto.strip()
        if len(recorte) <= limite:
            return recorte
        return _cortar_sem_quebrar_palavra(recorte, limite, max(0, limite - 200))
    qset = set(qtokens)
    scored = []
    for i, f in enumerate(frases):
        ftoks = set(_tokenizar(f))
        scored.append((len(qset & ftoks), i))
    # janela deslizante de até ~limite chars a partir da melhor frase
    scored.sort(key=lambda x: (-x[0], x[1]))
    if scored[0][0] == 0:
        recorte = texto.strip().replace("\n\n", "\n")
        if len(recorte) <= limite:
            return recorte
        return _cortar_sem_quebrar_palavra(recorte, limite, max(0, limite - 200))
    inicio = scored[0][1]
    partes, total = [], 0
    for j in range(inicio, len(frases)):
        if total + len(frases[j]) > limite and partes:
            break
        partes.append(frases[j])
        total += len(frases[j])
    return " ".join(partes)


def _resposta_extrativa(pergunta: str, trechos: list[Trecho], candidato_nome: str) -> str:
    if not trechos:
        return (f"Não encontrei esse tema no plano de governo de {candidato_nome} "
                f"(documento oficial do TSE). Tente reformular — ex.: 'o que o plano diz "
                f"sobre saúde/educação/segurança?'.")
    # consolida os melhores recortes dos principais trechos num texto único
    # (índices/sumários valem como fonte, mas não como texto da resposta)
    uteis = [t for t in trechos if t.texto.count("....") < 5] or trechos
    qtoks = _tokenizar_pergunta(pergunta)
    partes = [_melhor_recorte(t.texto, qtoks, 550) for t in uteis[:3]]
    texto = " ".join(partes)
    texto = re.sub(r"\s+", " ", texto).strip()
    return _limitar_texto(texto)


def _cortar_sem_quebrar_palavra(texto: str, limite: int, min_corte: int) -> str:
    """Corta em no máx. `limite` chars sem quebrar palavra no meio.

    Prefere fim de frase (". "); senão, último espaço; em último caso,
    corta seco e adiciona reticência. Nunca devolve palavra cortada.
    """
    texto = texto.strip()
    if len(texto) <= limite:
        return texto
    corte = texto.rfind(". ", 0, limite)
    if corte > min_corte:
        return texto[: corte + 1].strip()
    corte = texto.rfind(" ", 0, limite)
    if corte > min_corte:
        return texto[:corte].rstrip() + "…"
    return texto[:limite].rstrip() + "…"


def _limitar_texto(texto: str) -> str:
    if len(texto) > LIMITE_RESPOSTA:
        texto = _cortar_sem_quebrar_palavra(texto, LIMITE_RESPOSTA, 400)
    return texto


LIMITE_CONSELHEIRA = 600  # teto de caracteres da leitura em 3 linhas


def _bloco_vereditos(itens: list[dict]) -> str:
    """Situação apurada nos documentos: fato determinístico, não opinião da LLM."""
    linhas = []
    for i in itens:
        curto = CANDIDATOS[i["candidato_id"]]["curto"]
        if i.get("veredito") == "nao_encontrado":
            linhas.append(f"- {curto}: TEMA NÃO ENCONTRADO no plano")
        else:
            pags = ", ".join(f["paginas"] for f in i.get("fontes", [])) or "—"
            linhas.append(f"- {curto}: TEMA ENCONTRADO (páginas {pags})")
    return "\n".join(linhas)


def _prompt_conselheira(resumos: str, comparativo: bool, situacao: str = "") -> str:
    if comparativo:
        tarefa = ("Em EXATAMENTE 3 linhas, como uma conselheira experiente em política brasileira, "
                  "dê sua leitura comparativa das respostas: primeira linha = onde os dois planos convergem; "
                  "segunda linha = a divergência central entre eles; "
                  "terceira linha = o ponto de atenção para o eleitor. ")
    else:
        tarefa = ("Em EXATAMENTE 3 linhas, como uma conselheira experiente em política brasileira, "
                  "dê sua leitura da resposta: primeira linha = o essencial da proposta; "
                  "segunda linha = o ponto forte; "
                  "terceira linha = o ponto de atenção para o eleitor. ")
    return (
        f"SITUAÇÃO APURADA NOS DOCUMENTOS (fato — prevalece sobre qualquer impressão "
        f"dos resumos, respeite estritamente):\n{situacao}\n\n"
        + tarefa +
        f"IMPORTANTE: entregue apenas o texto das 3 linhas, uma por linha. "
        f"NÃO escreva 'Linha 1', 'Linha 2', números, marcadores ou negrito antes delas. "
        f"REGRAS DE FIDELIDADE (obrigatórias): se um lado está como TEMA NÃO ENCONTRADO, "
        f"é PROIBIDO dizer que 'ambos' ou 'os dois' mencionam, tratam, propõem ou apresentam "
        f"qualquer conteúdo sobre o tema — exemplo de ERRO PROIBIDO: 'Ambos mencionam leis "
        f"e decretos' quando a situação diz que um plano NÃO ENCONTRADO; vereditos opostos "
        f"SÃO a divergência central (diga qual plano trata do tema e qual não trata); cada "
        f"frase deve decorrer diretamente de um dos resumos. "
        f"Responda em português, no máximo {LIMITE_CONSELHEIRA} caracteres no total, "
        f"sem propaganda e sem juízo partidário.\n\nRESPOSTAS DOS PLANOS:\n{resumos}"
    )


# Afirmação de conteúdo compartilhado ("ambos mencionam/tratam/..."):
# com vereditos opostos, isso é alucinação — a saída da LLM é descartada.
_VERBOS_CONTEUDO = (
    r"mencionam|tratam|trazem|prop[õo]em|apresentam|defendem|citam|"
    r"prev[eê]em|incluem|abordam|contemplam|destacam|prev[eê]|traz"
)
_PADRAO_CONVERGENCIA_FALSA = re.compile(
    rf"(?i)\b(ambos|os dois|as duas)\b[^.\n]{{0,80}}\b({_VERBOS_CONTEUDO})\b"
    rf"|\b({_VERBOS_CONTEUDO})\b[^.\n]{{0,80}}\b(ambos|os dois|as duas)\b"
)


def _conselheira_contradiz_vereditos(texto: str, itens: list[dict]) -> bool:
    """True se o texto afirma conteúdo comum com um lado NÃO ENCONTRADO."""
    if len(itens) < 2:
        return False
    opostos = {i.get("veredito") for i in itens[:2]} == {"encontrado", "nao_encontrado"}
    return bool(opostos and _PADRAO_CONVERGENCIA_FALSA.search(texto))


def _limpar_rotulo_conselheira(linha: str) -> str:
    """Remove prefixos que o LLM insiste em pôr: 'Linha 1:', '1.', '- ', etc."""
    linha = linha.strip(" \t-•*=")
    linha = re.sub(r"^\*{0,2}\s*linha\s*\d+\s*[:.\-–—_)}\]]*\s*\*{0,2}\s*",
                   "", linha, flags=re.IGNORECASE).strip()
    linha = re.sub(r"^\d+\s*[:.\-–)]\s*", "", linha).strip()
    linha = re.sub(r"^[-*•>=]+\s*", "", linha).strip()
    return linha


def _termos_distintivos(fontes_a: list[dict], fontes_b: list[dict], k: int = 3) -> list[str]:
    """Termos de maior peso em A ausentes em B (leitura honesta sem IA)."""
    from collections import Counter
    base = get_base()
    cont_a: Counter = Counter()
    for f in fontes_a:
        cont_a.update(_tokenizar(f.get("trecho", "")))
    vocab_b: set[str] = set()
    for f in fontes_b:
        vocab_b.update(_tokenizar(f.get("trecho", "")))
    ranked = sorted(
        ((c * base._idf.get(t, 1.0), t) for t, c in cont_a.items() if t not in vocab_b and len(t) > 4),
        reverse=True,
    )
    return [t for _, t in ranked[:k]]


def _opniao_local(itens: list[dict]) -> str:
    if len(itens) > 1:
        a, b = itens[0], itens[1]
        ca = CANDIDATOS[a["candidato_id"]]["curto"]
        cb = CANDIDATOS[b["candidato_id"]]["curto"]
        va = a.get("veredito") == "nao_encontrado"
        vb = b.get("veredito") == "nao_encontrado"
        if not va and not vb:
            l1 = "Leitura automática: os dois planos tratam do tema — compare as páginas citadas de cada lado."
        elif va and not vb:
            l1 = f"Leitura automática: só o plano de {cb} traz conteúdo direto sobre o tema; o de {ca} não trata."
        elif vb and not va:
            l1 = f"Leitura automática: só o plano de {ca} traz conteúdo direto sobre o tema; o de {cb} não trata."
        else:
            l1 = "Leitura automática: nenhum dos planos trata do tema de forma direta."
        ta = ", ".join(_termos_distintivos(a["fontes"], b["fontes"])) if a["fontes"] else ""
        tb = ", ".join(_termos_distintivos(b["fontes"], a["fontes"])) if b["fontes"] else ""
        if ta and tb:
            l2 = f"O vocabulário entrega a ênfase de cada lado — {ca}: {ta}; {cb}: {tb}."
        elif ta:
            l2 = f"O vocabulário entrega a ênfase do plano — {ca}: {ta}."
        elif tb:
            l2 = f"O vocabulário entrega a ênfase do plano — {cb}: {tb}."
        else:
            l2 = "Sem trechos correspondentes, não há vocabulário a comparar."
        l3 = "Para decidir, leia os trechos originais nas páginas citadas acima."
        return f"{l1}\n{l2}\n{l3}"
    item = itens[0]
    c = CANDIDATOS[item["candidato_id"]]["curto"]
    if item.get("veredito") == "nao_encontrado" or not item["fontes"]:
        return (f"Leitura automática: o plano de {c} não trata do tema.\n"
                f"Não há vocabulário a comparar para este plano.\n"
                f"Para conferir, consulte o PDF oficial no portal do TSE.")
    pags = item["fontes"][0]["paginas"] if item["fontes"] else "—"
    l1 = f"Leitura automática: o plano de {c} trata do tema (p. {pags})."
    termos = ", ".join(_termos_distintivos(item["fontes"], [])) or "—"
    l2 = f"Os termos de maior peso na proposta são: {termos}."
    l3 = "Para formar juízo, leia o trecho original na página citada acima."
    return f"{l1}\n{l2}\n{l3}"


def _opniao_conselheira(itens: list[dict], usar_ia: bool, usar_ollama: bool) -> dict:
    """Leitura política em 3 linhas: via LLM quando há IA, heurística honesta senão."""
    comparativo = len(itens) > 1
    resumos = "\n\n".join(
        f"{CANDIDATOS[i['candidato_id']]['curto']}: "
        f"{i['resposta'].rsplit('Plano de Governo, páginas', 1)[0].strip()}"
        for i in itens
    )
    texto, via = None, "busca-local"
    if any(i.get("veredito") == "nao_encontrado" for i in itens):
        # Ausência de conteúdo é fato apurado, não interpretação: a LLM só
        # sintetiza quando há conteúdo (dos dois lados, ou do lado pedido).
        # Sem isso, qualquer moldura "convergem/divergem" vira alucinação
        # ("Ambos buscam/mencionam..." com um plano omisso).
        texto, via = _opniao_local(itens), "busca-local (veredito)"
    elif usar_ia:
        prompt = _prompt_conselheira(resumos, comparativo, _bloco_vereditos(itens))
        if usar_ollama:
            texto = _ollama_prompt(prompt)
            if texto:
                via = f"ollama:{ollama_modelo_ativo()}"
        else:
            texto = _openai_prompt(prompt)
            if texto:
                via = "openai"
            else:
                texto = _gemini_prompt(prompt)
                if texto:
                    via = "gemini"
    if not texto:
        texto = _opniao_local(itens)
    elif _conselheira_contradiz_vereditos(texto, itens):
        # A LLM afirmou conteúdo comum com um lado NÃO ENCONTRADO
        # (ex.: "Ambos mencionam leis...") -> descarta e usa leitura local,
        # que segue os vereditos deterministicamente.
        texto, via = _opniao_local(itens), "busca-local (revisão)"
    linhas = [_limpar_rotulo_conselheira(l) for l in texto.strip().splitlines()]
    linhas = [l for l in linhas if l]
    texto = "\n".join(linhas[:3])
    if len(texto) > LIMITE_CONSELHEIRA:
        texto = _cortar_sem_quebrar_palavra(texto, LIMITE_CONSELHEIRA, 200)
    return {"texto": texto, "via": via}


def _singularizar(token: str) -> list[str]:
    """Variações de singular p/ o português (impostos→imposto, animais→animal)."""
    saidas = []
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        if token.endswith(("ais", "eis", "ois", "uis")):
            saidas.append(token[:-2] + "l")  # animais -> animal
        else:
            saidas.append(token[:-1])
    return saidas


def _termos_essenciais(pergunta: str, base: "BaseConhecimento | None" = None, k: int = 1) -> set[str]:
    """Termo-âncora da pergunta (+sinônimos/singulares) que um trecho precisa conter.

    Só o termo mais raro vale como trava: palavras genéricas ("proteção")
    sozinhas não validam chunk de outro assunto ("proteger a sociedade"
    ≠ proteção animal). k=1 por padrão; aumente só se um tema legítimo exigir.
    """
    toks = [t for t in _tokenizar(pergunta) if t not in IGNORAR_NA_PERGUNTA]
    if not toks:
        return set()
    if base is not None:
        # termo ausente nos docs (nome próprio, neologismo) é o mais distintivo
        toks = sorted(set(toks), key=lambda t: -base._idf.get(t, 99.0))
    essenciais: set[str] = set(toks[:k] if len(toks) > k else toks)
    essenciais.update(*[_singularizar(t) for t in list(essenciais)])
    for t in list(essenciais):
        essenciais.update(SINONIMOS.get(t, []))
    return essenciais


def _cobre_essencial(texto: str, essenciais: set[str]) -> bool:
    if not essenciais:
        return False
    return bool(set(_tokenizar(texto)) & essenciais)


def _resposta_negativa(meta: dict) -> str:
    return (f"O plano de governo de {meta['nome']} não trata desse tema no "
            f"documento oficial registrado no TSE.")


# A LLM declinou ("não há nos trechos") -> vale como NÃO ENCONTRADO.
_PADRAO_NEGACAO = re.compile(
    r"(n[ãa]o\s+(menciona|mencionam|trata|tratam|h[áa]|existe|existem|aborda|abordam|"
    r"cont[ée]m|prev[êe]|apresenta|detalha|traz|cobre|inclui|contempla|encontrei|"
    r"localizei|consta|h[áa]\s+men[çc][ãa]o)|nenhum\s+(dos\s+)?trechos?|"
    r"sem\s+men[çc][ãa]o|nada\s+(sobre|consta))",
    re.IGNORECASE,
)


def responder_candidato(pergunta: str, candidato_id: str, usar_ia: bool = True,
                        top_k: int = 3, usar_ollama: bool = False) -> dict:
    base = get_base()
    meta = CANDIDATOS[candidato_id]
    trechos = base.buscar(pergunta, candidato_id=candidato_id, top_k=top_k)
    # Trava de termo essencial: só valem chunks com o termo raro da pergunta
    # ("proteção" sozinha não valida chunk sobre "proteger a sociedade").
    essenciais = _termos_essenciais(pergunta, base)
    trechos = [t for t in trechos if _cobre_essencial(t.texto, essenciais)]
    veredito = "encontrado" if trechos else "nao_encontrado"
    if not trechos:
        return {
            "candidato_id": candidato_id,
            "candidato": meta["nome"],
            "partido": meta["partido"],
            "resposta": _resposta_negativa(meta),
            "via": "busca-local",
            "veredito": veredito,
            "fontes": [],
        }
    contexto = "\n\n---\n\n".join(
        f"[p. {t.pag_inicio}-{t.pag_fim}]\n{t.texto[:2000]}" for t in trechos
    )
    texto, via = None, "busca-local"
    if usar_ollama and usar_ia and trechos:
        # --local: só o Ollama da máquina, nada vai para a nuvem
        texto = _chamar_ollama(pergunta, contexto, meta["nome"])
        if texto:
            via = f"ollama:{ollama_modelo_ativo()}"
    elif usar_ia and trechos:
        texto = _chamar_openai(pergunta, contexto, meta["nome"])
        if texto:
            via = "openai"
        else:
            texto = _chamar_gemini(pergunta, contexto, meta["nome"])
            if texto:
                via = "gemini"
    if texto and _PADRAO_NEGACAO.search(texto):
        # A própria LLM declarou que o contexto não contém a resposta:
        # padroniza (sem vazar "trechos fornecidos") e sem citar páginas.
        return {
            "candidato_id": candidato_id,
            "candidato": meta["nome"],
            "partido": meta["partido"],
            "resposta": _resposta_negativa(meta),
            "via": via,
            "veredito": "nao_encontrado",
            "fontes": [],
        }
    if not texto:
        texto = _resposta_extrativa(pergunta, trechos, meta["nome"])
    texto = _finalizar_resposta(texto, meta, trechos)
    return {
        "candidato_id": candidato_id,
        "candidato": meta["nome"],
        "partido": meta["partido"],
        "resposta": texto,
        "via": via,
        "veredito": veredito,
        "fontes": [
            {"paginas": f"{t.pag_inicio}-{t.pag_fim}",
             "trecho": t.texto[:500] + ("…" if len(t.texto) > 500 else ""),
             "pdf": meta["arquivo_pdf"],
             "tse_url": meta["tse_url"]}
            for t in trechos
        ],
    }


def responder(pergunta: str, modo: str = "comparar", usar_ia: bool = True,
              usar_ollama: bool = False) -> dict:
    """modo: 'comparar' | 'lula' | 'flavio'"""
    modo = (modo or "comparar").lower()
    if modo in ("ambos", "comparativo", "comparacao", "comparação"):
        modo = "comparar"
    itens = []
    if modo == "comparar":
        itens = [responder_candidato(pergunta, "lula", usar_ia, usar_ollama=usar_ollama),
                 responder_candidato(pergunta, "flavio", usar_ia, usar_ollama=usar_ollama)]
    elif modo in CANDIDATOS:
        itens = [responder_candidato(pergunta, modo, usar_ia, usar_ollama=usar_ollama)]
    else:
        itens = [responder_candidato(pergunta, "lula", usar_ia, usar_ollama=usar_ollama),
                 responder_candidato(pergunta, "flavio", usar_ia, usar_ollama=usar_ollama)]
        modo = "comparar"
    conselheira = _opniao_conselheira(itens, usar_ia, usar_ollama)
    return {"pergunta": pergunta, "modo": modo, "respostas": itens, "conselheira": conselheira}


def detectar_modo(pergunta: str) -> str:
    p = pergunta.lower()
    if any(k in p for k in ("lula", "pt ", "governo atual", "reeleição")) \
            and not any(k in p for k in ("flávio", "flavio", "bolsonaro", "compare", "diferença", "diferenca", "ambos")):
        return "lula"
    if any(k in p for k in ("flávio", "flavio", " bolsonaro", " pl ")) \
            and not any(k in p for k in ("lula", "compare", "diferença", "diferenca", "ambos")):
        return "flavio"
    return "comparar"
