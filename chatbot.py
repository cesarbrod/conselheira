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
    ("Saúde e SUS", "O que cada candidato propõe para a saúde e o SUS?"),
    ("Educação e alfabetização", "O que cada candidato propõe para educação e alfabetização?"),
    ("Aposentadoria e idosos", "O que cada candidato propõe para aposentadoria e idosos?"),
    ("Salário mínimo", "O que cada candidato propõe para o salário mínimo?"),
    ("Programas sociais", "O que cada candidato propõe para programas sociais?"),
    ("Trabalho (6x1, apps)", "O que cada candidato propõe sobre jornada de trabalho e trabalhadores de aplicativos?"),
    ("Meio ambiente / Amazônia", "O que cada candidato propõe para meio ambiente e Amazônia?"),
    ("Democracia e instituições", "O que cada candidato propõe sobre democracia e instituições?"),
    ("Farmácia Popular", "O que cada candidato propõe para a Farmácia Popular?"),
    ("Reforma do Estado", "O que cada candidato propõe sobre ministérios, reforma administrativa e teto de gastos?"),
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


# Famílias morfológicas: flexões do mesmo radical valem como o mesmo tema.
# (Os planos escrevem "alfabetizada/alfabetizar" onde a pergunta diz
# "alfabetização" — sem isso, o casamento exato perde conteúdo real.)
_FAMILIAS = [
    ["alfabetizacao", "alfabetizar", "alfabetizada", "alfabetizado",
     "alfabetizadas", "alfabetizados", "analfabetismo"],
    ["educacao", "educar", "educacional", "educacionais", "educador", "educadores"],
    ["trabalho", "trabalhar", "trabalhador", "trabalhadores",
     "trabalhista", "trabalhistas"],
    ["professor", "professores", "professora", "professoras",
     "docente", "docentes"],
    ["salario", "salarios", "salarial", "salariais"],
    ["escola", "escolas", "escolar", "escolares"],
]
for _fam in _FAMILIAS:
    for _t in _fam:
        SINONIMOS[_t] = sorted(set(SINONIMOS.get(_t, [])) | set(_fam))


# Ponte para tema amplo: termo específico ausente nos planos -> oferta do
# tema que os planos comprovadamente cobrem (só dispara em NÃO ENCONTRADO).
PONTES_TEMA = {
    "merenda": "a educação",
    "vestibular": "a educação",
    "sisu": "a educação",
    "enem": "a educação",
    "farmacia": "a saúde",
    "upa": "a saúde",
    "presidio": "a segurança pública",
    "presidios": "a segurança pública",
}


def _ponte_tema(pergunta: str) -> tuple[str, str] | None:
    """(palavra do usuário, tema amplo) se houver ponte; None caso contrário."""
    for original in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+", pergunta):
        if _sem_acento(original.lower()) in PONTES_TEMA:
            return original, PONTES_TEMA[_sem_acento(original.lower())]
    return None


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
    texto = _capitalizar_sentencas(texto)
    if trechos:
        texto = texto.rstrip() + "\n\n" + _linha_fontes(meta, trechos)
    return texto


def _chamar_openai(pergunta: str, contexto: str, candidato_nome: str,
                   prompt_fn=None) -> str | None:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return None
    return _openai_prompt((prompt_fn or _prompt_rag)(pergunta, contexto, candidato_nome))


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


def _chamar_ollama(pergunta: str, contexto: str, candidato_nome: str,
                   prompt_fn=None) -> str | None:
    model = ollama_modelo_ativo()
    if not model:
        _avisar_ia_uma_vez("Ollama fora do ar ou sem modelos instalados "
                           f"({_ollama_base()}; rode `ollama pull <modelo>` e `ollama serve`)")
        return None
    return _ollama_prompt((prompt_fn or _prompt_rag)(pergunta, contexto, candidato_nome))


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


def _prompt_conselheira_perfil(situacao: str, resumos: str) -> str:
    return (
        f"Você é uma conselheira eleitoral neutra. Uma pessoa comum, que não leu "
        f"os planos de governo, descreve a própria situação assim: \"{situacao}\"\n"
        f"Em EXATAMENTE 3 linhas, diga: primeira = o que cada plano oferece para o "
        f"caso dela; segunda = a diferença entre os planos que mais pesa para este "
        f"perfil; terceira = o que ela deve conferir nos documentos antes de decidir. "
        f"PROIBIDO indicar voto, dizer qual plano é melhor ou escolher por ela — "
        f"apresente os pontos e deixe a decisão com ela; PROIBIDO 'Linha 1', números "
        f"ou marcadores. Responda em português, no máximo {LIMITE_CONSELHEIRA} "
        f"caracteres, sem propaganda.\n\nRESPOSTAS DOS PLANOS:\n{resumos}"
    )


def _opniao_local_perfil(itens: list[dict], mapa: dict) -> str:
    a, b = itens[0], itens[1]
    ambos = [t for t, vs in mapa.items()
             if vs.get(a["candidato_id"]) == "encontrado"
             and vs.get(b["candidato_id"]) == "encontrado"]
    parciais = [t for t in mapa if t not in ambos]
    l1 = (f"Para o seu caso, há propostas nos dois planos sobre: {', '.join(ambos)}."
          if ambos else "Para o seu caso, os planos cobrem parcialmente os temas.")
    l2 = (f"Só um dos lados trata de: {', '.join(parciais)} — confira o plano indicado."
          if parciais else "A diferença está no detalhe: compare os trechos citados de cada lado.")
    l3 = "A decisão é sua: leia os trechos nas páginas citadas antes de escolher."
    return f"{l1}\n{l2}\n{l3}"


def _limpar_rotulo_conselheira(linha: str) -> str:
    """Remove prefixos que o LLM insiste em pôr: 'Linha 1:', '1.', '- ', etc."""
    linha = linha.strip(" \t-•*=")
    linha = re.sub(r"^\*{0,2}\s*linha\s*\d+\s*[:.\-–—_)}\]]*\s*\*{0,2}\s*",
                   "", linha, flags=re.IGNORECASE).strip()
    linha = re.sub(r"^\d+\s*[:.\-–)]\s*", "", linha).strip()
    linha = re.sub(r"^[-*•>=]+\s*", "", linha).strip()
    return linha


_ABREVIATURAS = {"p", "pp", "ex", "sr", "sra", "dr", "dra", "vs", "cf",
                 "etc", "art", "n", "no", "nos", "obs"}


def _capitalizar_sentencas(texto: str) -> str:
    """Cada frase começa com maiúscula (regra do português).

    Protege abreviações ("p. 69", "ex.:", "Sr.") e decimais ("3.450"),
    que não abrem sentença nova.
    """
    texto = texto.strip()
    if not texto:
        return texto
    texto = re.sub(
        r"^([^A-Za-zÀ-ÖØ-öø-ÿ]*)([a-zà-ú])",
        lambda m: m.group(1) + m.group(2).upper(), texto, count=1,
    )

    def _rep(m: "re.Match") -> str:
        ant = m.group("ant") or ""
        if ant.lower() in _ABREVIATURAS:
            return m.group(0)
        proxima = re.match(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", texto[m.start("letra"):])
        if proxima and proxima.group(0).lower() in _ABREVIATURAS:
            return m.group(0)
        return ant + m.group("sep") + m.group("letra").upper()

    return re.sub(
        r"(?P<ant>[A-Za-zÀ-ÖØ-öø-ÿ]+)?(?P<sep>[.!?…]+[\"”'’)\]]*\s+)(?P<letra>[a-zà-ú])",
        _rep, texto,
    )


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


def _opniao_conselheira(itens: list[dict], usar_ia: bool, usar_ollama: bool,
                        perfil: dict | None = None) -> dict:
    """Leitura em 3 linhas: via LLM quando há IA, heurística honesta senão."""
    comparativo = len(itens) > 1
    resumos = "\n\n".join(
        f"{CANDIDATOS[i['candidato_id']]['curto']}: "
        f"{i['resposta'].rsplit('Plano de Governo, páginas', 1)[0].strip()}"
        for i in itens
    )
    local_fn = (_opniao_local_perfil(itens, perfil["dimensoes"])
                if perfil else None)
    texto, via = None, "busca-local"
    if any(i.get("veredito") == "nao_encontrado" for i in itens):
        # Ausência de conteúdo é fato apurado, não interpretação (vale para
        # modo temático e modo perfil): a LLM só sintetiza quando há conteúdo.
        texto, via = (local_fn if perfil is not None else _opniao_local(itens)), "busca-local (veredito)"
    elif usar_ia:
        if perfil is not None:
            prompt = _prompt_conselheira_perfil(perfil["situacao"], resumos)
        else:
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
        texto = local_fn if perfil is not None else _opniao_local(itens)
    elif _PADRAO_ENDOSSO.search(texto):
        # Endosso de candidato ("vote em..."): nunca vai ao ar.
        texto, via = (local_fn if perfil is not None else _opniao_local(itens)), "busca-local (revisão)"
    elif perfil is None and _conselheira_contradiz_vereditos(texto, itens):
        # A LLM afirmou conteúdo comum com um lado NÃO ENCONTRADO
        # (ex.: "Ambos mencionam leis...") -> descarta e usa leitura local,
        # que segue os vereditos deterministicamente.
        texto, via = _opniao_local(itens), "busca-local (revisão)"
    linhas = [_capitalizar_sentencas(_limpar_rotulo_conselheira(l))
             for l in texto.strip().splitlines()]
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
        if re.search(r"[bcdfghjklmnpqrstvwxz]es$", token):
            sem_es = token[:-2]  # mulheres -> mulher; meses -> mes
            if sem_es not in saidas:
                saidas.append(sem_es)
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


def _resposta_negativa(meta: dict, ponte: tuple[str, str] | None = None) -> str:
    if ponte:
        termo, tema = ponte
        return (f"O plano de governo de {meta['nome']} não menciona '{termo}' diretamente. "
                f"Você gostaria de saber mais sobre o que o plano propõe para {tema}?")
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
            "resposta": _resposta_negativa(meta, _ponte_tema(pergunta)),
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
            "resposta": _resposta_negativa(meta, _ponte_tema(pergunta)),
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


# ---------------- Modo perfil (perguntas pessoais abertas) ----------------

# Perguntas de quem descreve a própria situação ("sou..., tenho..., quero...")
# e pede qual plano atende: exigem decomposição em temas, não âncora única.
_MARCAS_PESSOA = _norm_set(
    "sou tenho quero preciso moro ganho trabalho estou fui era me meu minha "
    "meus minhas comigo desempregado desempregada aposentado aposentada"
)


def eh_pergunta_perfil(pergunta: str) -> bool:
    low = _sem_acento(pergunta.lower())
    if not re.search(r"qual (plano|dos planos)", low):
        return False
    toks = set(re.findall(r"[a-z0-9]+", low))
    if toks & _MARCAS_PESSOA:
        return True
    return bool(re.search(r"\b\d{2,3}\s*anos\b", low))


def dimensoes_perfil(pergunta: str) -> list[str]:
    """Decompõe a situação de vida em sub-perguntas temáticas (com ordem)."""
    low = _sem_acento(pergunta.lower())

    def tem(padrao: str) -> bool:
        return bool(re.search(padrao, low))

    dims: list[str] = []
    if tem(r"aposent|idoso|terceira idade|\b[6-9]\d\s*anos|60\s*\+"):
        dims.append("O que o plano propõe para aposentadoria e idosos?")
    if tem(r"desempreg|sem emprego|sem trabalho|procuro|desocupad"):
        dims += ["O que o plano propõe sobre emprego e trabalho?",
                 "O que o plano propõe para programas sociais?"]
    if tem(r"filh|crianca|escola|creche|adolescente"):
        dims += ["O que o plano propõe para educação?",
                 "O que o plano propõe para programas sociais?"]
    if tem(r"faculdade|universidade|ensino superior|enem|vestibular|estudar|estudo"):
        dims.append("O que o plano propõe para educação superior?")
    if tem(r"salari|ganh|pagam|renda|sustent|contas|despesa"):
        dims.append("O que o plano propõe para o salário mínimo?")
    if tem(r"saude|doen|hospital|remedio|medico|\bsus\b"):
        dims.append("O que o plano propõe para a saúde?")
    if tem(r"dinheiro|sem condicoes|pobre|pobreza|fome|comida"):
        dims.append("O que o plano propõe para programas sociais?")
    if tem(r"aluguel|morad|habitacao|minha casa|casa propria"):
        dims.append("O que o plano propõe para habitação e moradia?")
    if tem(r"animal|animais|\bpet\b|cao\b|gato|cachorro|protetor|cuidador|castracao|maus.tratos"):
        dims.append("O que o plano propõe para proteção aos animais?")
    if tem(r"mulher|mae\b|matern|genero|femin|solteira"):
        dims.append("O que o plano propõe para as mulheres?")
    dims = list(dict.fromkeys(dims))
    return dims or ["O que o plano propõe para programas sociais?"]


def _tema_curto(subq: str) -> str:
    t = re.sub(r"^O que o plano propõe (para|sobre) ", "", subq).rstrip("?")
    return t


def _prompt_perfil(pergunta: str, contexto: str, candidato_nome: str) -> str:
    return (
        f"Você é uma conselheira eleitoral neutra e imparcial. Uma pessoa comum, "
        f"que não leu os planos de governo, descreve a própria situação assim: "
        f"\"{pergunta}\"\n"
        f"Com base ESTRITAMENTE no contexto oficial do plano de governo de "
        f"{candidato_nome} (TSE, Eleições 2026) abaixo, explique em português, em "
        f"texto único de no MÁXIMO {LIMITE_RESPOSTA} caracteres, quais propostas "
        f"ATÊNDEM à situação dela, dimensão por dimensão, citando as páginas entre "
        f"parênteses. Se alguma dimensão não tiver proposta correspondente, diga "
        f"isso claramente e não invente; sem propaganda; NUNCA diga em quem votar "
        f"nem qual plano é melhor — a decisão é dela; fale como 'o plano de "
        f"{candidato_nome}'; nunca mencione trechos ou contexto.\n\n"
        f"CONTEXTO:\n{contexto}"
    )


# Endosso de candidato: a conselheira mapeia relevância, nunca escolhe.
_PADRAO_ENDOSSO = re.compile(
    r"(?i)\b(vote|votem|votar (em|no)|escolha (o|este|esse|no) plano|"
    r"recomendo|o melhor plano [ée]|deve votar|apoie o plano)\b"
)


def responder_candidato_perfil(pergunta: str, candidato_id: str,
                               dimensoes: list[str], usar_ia: bool = True,
                               usar_ollama: bool = False) -> dict:
    base = get_base()
    meta = CANDIDATOS[candidato_id]
    blocos: list[tuple[str, list[Trecho]]] = []
    for d in dimensoes:
        trechos = base.buscar(d, candidato_id=candidato_id, top_k=2)
        essenciais = _termos_essenciais(d, base)
        blocos.append((d, [t for t in trechos if _cobre_essencial(t.texto, essenciais)]))
    achados = [(d, tr) for d, tr in blocos if tr]
    vereditos_dim = {_tema_curto(d): ("encontrado" if tr else "nao_encontrado")
                     for d, tr in blocos}
    veredito = "encontrado" if achados else "nao_encontrado"
    todos_trechos = [t for _, tr in achados for t in tr]
    if not achados:
        return {
            "candidato_id": candidato_id,
            "candidato": meta["nome"],
            "partido": meta["partido"],
            "resposta": (f"Nenhum trecho dos temas relacionados à sua situação foi "
                         f"localizado no plano de governo de {meta['nome']}, documento "
                         f"oficial registrado no TSE."),
            "via": "busca-local",
            "veredito": veredito,
            "vereditos_dimensoes": vereditos_dim,
            "fontes": [],
        }
    contexto = "\n\n".join(
        f"### {_tema_curto(d)}\n" + "\n---\n".join(
            f"[p. {t.pag_inicio}-{t.pag_fim}]\n{t.texto[:1500]}" for t in tr)
        for d, tr in achados
    )
    texto, via = None, "busca-local"
    if usar_ollama and usar_ia:
        texto = _chamar_ollama(pergunta, contexto, meta["nome"],
                               prompt_fn=_prompt_perfil)
        if texto:
            via = f"ollama:{ollama_modelo_ativo()}"
    elif usar_ia:
        prompt = _prompt_perfil(pergunta, contexto, meta["nome"])
        texto = _chamar_openai(pergunta, contexto, meta["nome"],
                               prompt_fn=_prompt_perfil)
        if texto:
            via = "openai"
        else:
            texto = _gemini_prompt(prompt)
            if texto:
                via = "gemini"
    if texto and _PADRAO_ENDOSSO.search(texto):
        texto, via = None, "busca-local"  # cai para a extrativa abaixo
    if not texto:
        qtoks = _tokenizar_pergunta(pergunta)
        partes = []
        for d, tr in achados:
            rec = _melhor_recorte(" ".join(t.texto for t in tr), qtoks, 500)
            partes.append(f"Sobre {_tema_curto(d)}: {rec}")
        texto = _limitar_texto(" ".join(partes))
    texto = _finalizar_resposta(texto, meta, todos_trechos)
    vistos: set[str] = set()
    fontes = []
    for t in todos_trechos:
        rotulo = f"{t.pag_inicio}-{t.pag_fim}"
        if rotulo in vistos:
            continue
        vistos.add(rotulo)
        fontes.append({"paginas": rotulo,
                       "trecho": t.texto[:500] + ("…" if len(t.texto) > 500 else ""),
                       "pdf": meta["arquivo_pdf"],
                       "tse_url": meta["tse_url"]})
    return {
        "candidato_id": candidato_id,
        "candidato": meta["nome"],
        "partido": meta["partido"],
        "resposta": texto,
        "via": via,
        "veredito": veredito,
        "vereditos_dimensoes": vereditos_dim,
        "fontes": fontes,
    }


def responder(pergunta: str, modo: str = "comparar", usar_ia: bool = True,
              usar_ollama: bool = False) -> dict:
    """modo: 'comparar' | 'lula' | 'flavio'"""
    modo = (modo or "comparar").lower()
    if modo in ("ambos", "comparativo", "comparacao", "comparação"):
        modo = "comparar"
    itens = []
    perfil_info = None
    if modo == "comparar":
        if eh_pergunta_perfil(pergunta):
            dims = dimensoes_perfil(pergunta)
            itens = [responder_candidato_perfil(pergunta, "lula", dims, usar_ia,
                                                usar_ollama=usar_ollama),
                     responder_candidato_perfil(pergunta, "flavio", dims, usar_ia,
                                                usar_ollama=usar_ollama)]
            mapa: dict[str, dict[str, str]] = {}
            for i in itens:
                for tema, v in i.get("vereditos_dimensoes", {}).items():
                    mapa.setdefault(tema, {})[i["candidato_id"]] = v
            perfil_info = {"situacao": pergunta, "dimensoes": mapa}
        else:
            itens = [responder_candidato(pergunta, "lula", usar_ia, usar_ollama=usar_ollama),
                     responder_candidato(pergunta, "flavio", usar_ia, usar_ollama=usar_ollama)]
    elif modo in CANDIDATOS:
        itens = [responder_candidato(pergunta, modo, usar_ia, usar_ollama=usar_ollama)]
    else:
        itens = [responder_candidato(pergunta, "lula", usar_ia, usar_ollama=usar_ollama),
                 responder_candidato(pergunta, "flavio", usar_ia, usar_ollama=usar_ollama)]
        modo = "comparar"
    conselheira = _opniao_conselheira(itens, usar_ia, usar_ollama, perfil=perfil_info)
    resultado = {"pergunta": pergunta, "modo": modo, "respostas": itens,
                 "conselheira": conselheira}
    if perfil_info is not None:
        resultado["perfil"] = True
        resultado["dimensoes"] = list(perfil_info["dimensoes"])
    return resultado


def detectar_modo(pergunta: str) -> str:
    p = pergunta.lower()
    if any(k in p for k in ("lula", "pt ", "governo atual", "reeleição")) \
            and not any(k in p for k in ("flávio", "flavio", "bolsonaro", "compare", "diferença", "diferenca", "ambos")):
        return "lula"
    if any(k in p for k in ("flávio", "flavio", " bolsonaro", " pl ")) \
            and not any(k in p for k in ("lula", "compare", "diferença", "diferenca", "ambos")):
        return "flavio"
    return "comparar"
