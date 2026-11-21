"""Reescreve um texto pra soar humano, tirando os 'tells' de texto de IA.

Isto é o detoxify (um projeto à parte do Cass) trazido pro Relay. A gente manda
o texto pro Claude com um prompt de sistema que lista regras de reescrita (tirar
travessão, cortar clichê, desinchar hype, etc.). O modelo devolve só o texto
reescrito, com o mesmo sentido, tamanho, tom e idioma do original.

Usa a MESMA chave da API do Claude que o resto da interface (o OCR e o
"perguntar"), lá do vision/claude.json. Não precisa de outra config.

Em tempo de execução, só a biblioteca padrão. A chamada reaproveita o
vision._send (urllib), e o transporte é injetável pros testes não tocarem na
rede.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import ConfigError, RelayError
from .vision import Transport, VisionSettings, _post, _send


@dataclass(frozen=True)
class Rule:
    """Uma transformação de detox, combinável com as outras.

    identifier: slug estável (ex.: "em_dash"). name/instruction entram no prompt
    do sistema do jeito que estão. default_on diz se ela entra quando ninguém
    escolhe um conjunto na mão.
    """

    identifier: str
    name: str
    instruction: str
    default_on: bool = True


# A biblioteca de regras, uma por "tell" de texto de IA. É data pura: pra criar
# uma regra nova, é só acrescentar uma Rule aqui. As instruções vão em inglês de
# propósito (instruem o modelo); o texto reescrito sai no idioma do original,
# porque o prompt base manda preservar o idioma.
RULES: list[Rule] = [
    Rule(
        identifier="em_dash",
        name="Remove em-dashes",
        instruction=(
            "Eliminate every em-dash (the '—' character). Where the clauses "
            "on either side are independent, split them into two separate "
            "sentences. Otherwise replace the em-dash with a comma, a "
            "parenthetical, or a colon, whichever reads most naturally. The "
            "rewritten text must contain no em-dash characters at all."
        ),
    ),
    Rule(
        identifier="conclusion",
        name="Drop the tacked-on conclusion",
        instruction=(
            "Remove any tacked-on concluding or summary passage that merely "
            "restates points already made, especially sentences that open "
            "with 'In conclusion', 'Overall', 'Ultimately', 'In summary', 'All "
            "in all', or 'At the end of the day'. End the text on its last "
            "substantive point instead of a recap."
        ),
    ),
    Rule(
        identifier="cliches",
        name="Strip AI cliches",
        instruction=(
            "Replace overused AI-cliche vocabulary and filler with plain, "
            "direct wording. Watch for: 'delve', 'tapestry', 'navigate the "
            "complexities', 'it's important to note', 'it's worth noting', "
            "'in today's fast-paced world', 'plays a crucial/vital/pivotal "
            "role', 'a testament to', 'when it comes to', 'in the realm of', "
            "'unlock the potential', and 'serves as'. Rephrase so the meaning "
            "survives but the cliche is gone."
        ),
    ),
    Rule(
        identifier="negative_parallelism",
        name="Cut 'not just X, but Y' constructions",
        instruction=(
            "Remove the 'It's not just X, it's Y' / 'This isn't about X; it's "
            "about Y' / 'It's not only X, it's Y' construction wherever it "
            "appears. State the actual point directly without the contrast "
            "framing."
        ),
    ),
    Rule(
        identifier="hedging",
        name="Reduce hedging",
        instruction=(
            "Cut excessive hedging and empty qualifiers ('it could be argued', "
            "'arguably', 'generally speaking', 'in many cases', 'somewhat', "
            "'to some extent', 'perhaps') where they only soften a claim "
            "without adding real nuance. Keep hedging that conveys genuine "
            "uncertainty."
        ),
    ),
    Rule(
        identifier="rule_of_three",
        name="Break up triplets",
        instruction=(
            "Break up the habit of grouping things in threes for rhythm "
            "(tricolons and 'X, Y, and Z' triplets used purely for cadence). "
            "Vary the number of items and the sentence rhythm so the text does "
            "not fall into a repetitive triple beat."
        ),
    ),
    Rule(
        identifier="puffery",
        name="Deflate puffery",
        instruction=(
            "Deflate empty enthusiasm and hype. Cut or downgrade inflated "
            "adjectives applied to ordinary things: 'revolutionary', "
            "'groundbreaking', 'powerful', 'game-changing', 'cutting-edge', "
            "'world-class', 'seamless', 'robust', 'unparalleled', 'exciting', "
            "'transformative', and marketing imperatives like 'transform your "
            "business' or 'supercharge your workflow'. State plainly what the "
            "thing is and does. If a claim cannot survive without the "
            "superlative, it was not saying anything."
        ),
    ),
    Rule(
        identifier="vague_attribution",
        name="Remove vague attributions",
        instruction=(
            "Remove unsourced appeals to authority and fabricated evidence. "
            "Delete or rewrite phrases like 'studies show', 'research "
            "suggests', 'experts say', 'many believe', and 'it is widely "
            "known'. Do not invent named people, companies, or case studies "
            "(the telltale generic 'Sarah Chen'-style example) to stand in for "
            "real evidence. Either cite something concrete that is actually "
            "present in the source text, or make the point in the author's own "
            "voice without borrowed authority. Never add a citation or name "
            "that was not already there."
        ),
    ),
    Rule(
        identifier="transition_questions",
        name="Cut cringe engagement questions",
        instruction=(
            "Remove gimmicky one-line rhetorical hooks used purely for "
            "engagement: 'The catch?', 'The kicker?', 'The best part?', 'The "
            "result?', 'But here's the thing', 'Here's the kicker', 'Here's the "
            "secret', 'Sound familiar?', and 'But wait, there's more'. If the "
            "answer that follows matters, fold it into a normal declarative "
            "sentence. If you would not say it aloud in conversation, delete it."
        ),
    ),
    Rule(
        identifier="formulaic_transitions",
        name="Vary formulaic transitions",
        instruction=(
            "Replace mechanical, evenly-spaced transition words that open "
            "sentences out of habit: 'Moreover', 'Furthermore', 'Additionally', "
            "'In addition', 'Consequently', 'Notably', 'Importantly', and 'That "
            "said' when used as filler. Let sentences connect through their "
            "content instead. Vary how paragraphs begin rather than fronting "
            "each one with a connective adverb; drop the transition entirely "
            "where the logic is already clear."
        ),
    ),
    Rule(
        identifier="formal_diction",
        name="Plainen formal diction",
        instruction=(
            "Replace needlessly formal, corporate word choices with the plain "
            "words people actually speak: 'utilize' to 'use', 'leverage' to "
            "'use', 'facilitate' to 'help', 'expedite' to 'speed up', "
            "'implement' to 'start' or 'put in place', 'optimize' to 'improve', "
            "'commence' to 'start', 'endeavor' to 'try', 'ascertain' to 'find "
            "out', 'numerous' to 'many', 'in order to' to 'to'. Keep a formal "
            "term only when it is a genuine term of art with no plainer "
            "equivalent."
        ),
    ),
    Rule(
        identifier="corporate_participles",
        name="Unwind trailing -ing clauses",
        instruction=(
            "Rewrite the corporate habit of ending sentences with a tacked-on "
            "present-participle clause that adds vague significance: "
            "'...highlighting key benefits', '...emphasizing the importance "
            "of...', '...facilitating enhanced collaboration', '...underscoring "
            "the need for...', '...allowing teams to streamline workflows'. "
            "Either drop the trailing clause or turn it into its own plain, "
            "active sentence with a real subject."
        ),
    ),
    Rule(
        identifier="symbolic",
        name="Replace 'symbolizes' with the fact",
        instruction=(
            "Stop narrating what things abstractly 'mean' and state what "
            "happened instead. Cut or rewrite 'represents', 'symbolizes', "
            "'underscores', 'highlights', 'reflects', 'stands as a testament "
            "to', 'serves as a reminder that', and 'marks a turning point'. "
            "Replace each with the concrete fact, event, or consequence the "
            "sentence is dancing around."
        ),
    ),
    Rule(
        identifier="despite_challenges",
        name="Drop the 'despite challenges' section",
        instruction=(
            "Remove the formulaic 'despite the challenges' move: a paragraph "
            "that gestures at obstacles and then pivots to reassuring optimism "
            "('Despite these challenges, the future remains bright', 'While "
            "hurdles remain, X continues to thrive', 'Challenges aside, the "
            "potential is enormous'). If a real obstacle matters, state it "
            "specifically and leave it unresolved where it actually is "
            "unresolved. Do not append a hopeful coda."
        ),
    ),
    Rule(
        identifier="false_balance",
        name="Take a stance",
        instruction=(
            "Break the even-handed encyclopedic voice that weighs every side "
            "equally and concludes nothing. Where the text presents an "
            "'on one hand / on the other hand', 'it depends', or 'there are "
            "pros and cons' balance purely to stay safe, let the author commit "
            "to a position the source material already implies. Keep genuine, "
            "substantive nuance; cut reflexive both-sidesism that exists only "
            "to avoid taking a view. Do not invent an opinion the source does "
            "not support."
        ),
    ),
    Rule(
        identifier="sentence_case_headings",
        name="Sentence-case headings",
        instruction=(
            "Convert headings and titles from Title Case to sentence case: "
            "capitalize only the first word and any proper nouns, lowercasing "
            "the rest. For example, 'Five Ways To Improve Your Writing' becomes "
            "'Five ways to improve your writing'. Leave the heading text and "
            "any markup otherwise unchanged."
        ),
    ),
    Rule(
        identifier="emoji",
        name="Remove emojis",
        instruction=(
            "Remove all emojis and decorative symbols. Do not replace them with "
            "descriptions; simply delete them and fix any spacing or "
            "punctuation left behind."
        ),
    ),
    Rule(
        identifier="colons_semicolons",
        name="Avoid colons and semicolons in sentences",
        instruction=(
            "Avoid colons and semicolons inside prose sentences. Where a "
            "semicolon joins two independent clauses, split them into separate "
            "sentences or join them with a plain conjunction ('and', 'but', "
            "'so'). Where a colon introduces a clause or explanation mid-"
            "sentence, recast it as its own sentence or use a comma. Keep "
            "colons only where they are structurally necessary, such as before "
            "a genuine bulleted list, in a ratio or time, or after a heading "
            "label."
        ),
    ),
    Rule(
        identifier="overcomplicated_words",
        name="Avoid overcomplicated and polished words",
        instruction=(
            "Replace overcomplicated, ornate, or showy vocabulary with the "
            "simplest accurate word. Cut words chosen to sound polished rather "
            "than to be clear: 'myriad', 'plethora', 'cognizant', 'utilize', "
            "'elucidate', 'paradigm', 'multifaceted', 'nuanced', 'intricate', "
            "'meticulous', 'pivotal', 'profound', 'salient', 'ubiquitous', "
            "'quintessential', 'aforementioned'. Prefer the everyday word a "
            "person would actually say. Keep a longer word only when it is the "
            "precise term and no plainer word carries the same meaning."
        ),
    ),
    Rule(
        identifier="direct_phrasing",
        name="Prefer direct, simple phrasing",
        instruction=(
            "Prefer direct phrases and simple English over overpolished, "
            "roundabout wording. Replace inflated phrasings with their plain "
            "equivalents: 'in order to' to 'to', 'due to the fact that' to "
            "'because', 'at this point in time' to 'now', 'in the event that' "
            "to 'if', 'with regard to' to 'about', 'a large number of' to "
            "'many', 'has the ability to' to 'can', 'for the purpose of' to "
            "'to', 'in the process of' dropped. Say things the short, "
            "straightforward way a person would speak, without losing the "
            "meaning."
        ),
    ),
    Rule(
        identifier="over_formatting",
        name="Reduce over-formatting",
        instruction=(
            "Reduce over-formatting. Collapse unnecessary bold headers, "
            "excessive bullet lists, and heading scaffolding into flowing prose "
            "where the content does not genuinely call for a list. Preserve "
            "lists only when they carry truly itemized information."
        ),
    ),
    Rule(
        identifier="reconnect_lines",
        name="Reconnect line-split sentences",
        instruction=(
            "Rejoin sentences that have been split across multiple lines. Where "
            "a single sentence is broken by hard line breaks mid-clause (a line "
            "that ends without sentence-ending punctuation and continues on the "
            "next line), remove the interior line breaks and join the fragments "
            "back into one continuous sentence, collapsing the resulting double "
            "spaces into single spaces. Do not merge across genuine sentence "
            "boundaries and do not join separate paragraphs; only stitch the "
            "pieces of one sentence back together."
        ),
        default_on=False,
    ),
    Rule(
        identifier="merge_paragraphs",
        name="Merge paragraphs into one",
        instruction=(
            "Reconnect text that has been broken into multiple paragraphs into "
            "a single, continuous paragraph. Remove the blank lines between "
            "paragraphs and join them into one block of flowing prose, adding "
            "ordinary sentence spacing where paragraphs meet. Collapse every "
            "paragraph into one; the result must be a single paragraph with no "
            "internal blank lines. Keep the wording and sentence order intact; "
            "only remove the paragraph breaks."
        ),
        default_on=False,
    ),
    Rule(
        identifier="humanize",
        name="Make it not read as AI-written",
        instruction=(
            "As a final pass, read the whole text the way a skeptical human "
            "would and remove anything that still gives away machine authorship. "
            "Vary sentence length and rhythm so it does not march in an even, "
            "predictable cadence. Break up overly tidy, balanced structures and "
            "the relentlessly smooth flow that reads as generated. Cut leftover "
            "filler, hollow signposting, and over-explanation; let a plain, "
            "slightly uneven human voice come through. Make only the light "
            "touch-ups needed for this. Do not add new facts, change the "
            "meaning, or alter the author's intent."
        ),
    ),
]


# O prompt base: diz o que o editor faz e as regras duras (preservar sentido,
# idioma, tamanho e tom; não responder o conteúdo; só aplicar as regras; devolver
# só o texto). As regras escolhidas são grudadas depois, numeradas.
BASE_SYSTEM_PROMPT = (
    "You are an expert editor who rewrites text so it reads as if written by a "
    "thoughtful human, stripping out the stylistic 'tells' that mark writing as "
    "AI-generated.\n\n"
    "Hard constraints:\n"
    "- Preserve the original meaning, facts, intent, and the language it is "
    "written in.\n"
    "- Keep roughly the same length and the same overall tone and register.\n"
    "- Do not invent new information, add commentary, or answer the content of "
    "the text. You are editing it, not responding to it.\n"
    "- Apply ONLY the transformation rules listed below. Do not make other "
    "stylistic changes.\n"
    "- Output ONLY the rewritten text: no preamble, no explanation, no notes, "
    "and no surrounding markdown code fences."
)


def default_rules() -> list[Rule]:
    """As regras que entram quando ninguém escolhe um conjunto na mão."""
    return [r for r in RULES if r.default_on]


def build_system_prompt(rules: list[Rule]) -> str:
    """Monta o prompt de sistema: o texto base mais as regras escolhidas, numeradas."""
    if not rules:
        raise RelayError("nenhuma regra de detox foi escolhida")
    linhas = [f"{i}. {r.name}: {r.instruction}" for i, r in enumerate(rules, 1)]
    return f"{BASE_SYSTEM_PROMPT}\n\nApply these transformation rules:\n" + "\n".join(linhas)


def build_request(settings: VisionSettings, system: str, text: str) -> dict:
    """Pedido com prompt de sistema (as regras) e o texto do usuário pra reescrever."""
    return {
        "model": settings.model,
        "max_tokens": settings.max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": [{"type": "text", "text": text}]}],
    }


def detoxify_text(
    text: str,
    *,
    settings: VisionSettings,
    rules: list[Rule] | None = None,
    transport: Transport = _post,
) -> str:
    """Reescreve `text` pra soar humano e devolve só o texto reescrito.

    Usa a mesma chave/modelo do vision (o config do Claude). Sem `rules`, aplica
    o conjunto padrão. O transporte é injetável pros testes.
    """
    if not settings.configured:
        raise ConfigError("falta a chave da API do Claude. Ponha ela nas Configurações")
    if not text.strip():
        raise RelayError("não tem texto pra limpar")
    rules = default_rules() if rules is None else rules
    system = build_system_prompt(rules)
    out = _send(settings, build_request(settings, system, text), transport).strip()
    if not out:
        raise RelayError("o Claude não devolveu texto nenhum")
    return out
