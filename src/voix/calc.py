"""Spoken calculations, done exactly by the code, never by the LLM (which gets numbers wrong).

« Combien font 15 % de 80 ? » → 15/100*80 → 12; « 17 fois 23 » → 391; « racine carrée de 144 » → 12.
spoken() only accepts a sentence if, once the set phrases are removed (« combien font », « ça fait combien »),
nothing but numbers and operations remains: « quelle est la capitale de la France » is never a calculation.
evaluate() only accepts numbers, + - * / ** %, parentheses and sqrt: no name, no call, nothing else.
Tested by tests/calculations.toml (./voix route --test).
"""

import ast
import math
import operator
import re
import unicodedata

from .router import NUMBER_WORDS


class CalcError(Exception):
    pass


OPERATORS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
             ast.Pow: operator.pow, ast.Mod: operator.mod}
FUNCTIONS = {"sqrt": math.sqrt}


def evaluate(expression: str) -> float:
    """Exact value of an arithmetic expression; CalcError if it contains anything else."""
    if len(expression) > 200:
        raise CalcError("too_long")
    try:
        tree = ast.parse(expression.replace("^", "**"), mode="eval")
    except SyntaxError:
        raise CalcError("syntax")

    def value(node) -> float:
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return -value(node.operand) if isinstance(node.op, ast.USub) else value(node.operand)
        if isinstance(node, ast.BinOp) and type(node.op) in OPERATORS:
            left, right = value(node.left), value(node.right)
            if isinstance(node.op, ast.Pow) and (abs(right) > 100 or abs(left) > 10**6):
                raise CalcError("too_big")  # 9 ** 9 ** 9 would freeze the Mac
            try:
                return OPERATORS[type(node.op)](left, right)
            except ZeroDivisionError:
                raise CalcError("zero")
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS
                and len(node.args) == 1 and not node.keywords):
            try:
                return FUNCTIONS[node.func.id](value(node.args[0]))
            except ValueError:
                raise CalcError("domain")  # square root of a negative number
        raise CalcError("not_arithmetic")

    result = value(tree.body)
    if isinstance(result, complex) or math.isnan(result) or math.isinf(result) or abs(result) > 1e15:
        raise CalcError("too_big")
    return result


def has_operation(expression: str) -> bool:
    try:
        body = ast.parse(expression, mode="eval").body
    except SyntaxError:
        return False
    return isinstance(body, (ast.BinOp, ast.Call))


# Phrases around the calculation, removed before reading the numbers (folded text: no accents or capitals)
START = re.compile(
    r"^(?:(?:dis[- ]moi|tu peux me dire|tell me|est ce que tu sais|tu sais)\s+)?"
    r"(?:combien (?:font|fait|ca fait|egale?|donne|ca donne)|calcule[sz]?(?:[- ]moi)?|c'est combien|c est combien"
    r"|ca fait combien|quel est le resultat de|quelle est la valeur de|quel est|quelle est|what'?s|what is|how much is"
    r"|calculate|compute)?\s*")
END = re.compile(r"\s*(?:ca fait combien|ca fait|font combien|fait combien|egale combien|egale?|c'est combien|c est combien"
                 r"|ca donne quoi|ca donne combien|equals what|equals|is what)?\s*$")
# Operation words, longest first
WORDS = [
    (r"racine carree de|racine carree|racine de|the square root of|square root of", " sqrt "),
    (r"pour ?cents? de|percent of|% de|% of|%de", " /100* "), (r"pour ?cents?|percent|%", " /100 "),
    (r"la moitie de|half of|half", " 0.5* "), (r"le double de|double|twice", " 2* "), (r"le triple de|triple", " 3* "),
    (r"le tiers de|a third of", " 1/3* "), (r"le quart de|a quarter of", " 0.25* "),
    (r"multiplie par|multiplied by|fois|times|x|×|\*", " * "), (r"divise par|divided by|divise|over|sur|÷|/", " / "),
    (r"plus|\+", " + "), (r"moins|minus|-|−", " - "),
    (r"a la puissance|puissance|to the power of|to the power|exposant|\^", " ** "),
    (r"au carre|squared", " **2 "), (r"au cube|cubed", " **3 "),
    (r"ouvre la parenthese|open parenthesis|\(", " ( "), (r"ferme la parenthese|close parenthesis|\)", " ) "),
]
_WORDS = re.compile(r"(?<![\w])(" + "|".join(f"(?:{w})" for w, _ in WORDS) + r")(?![\w])")
BIG = {"mille": 1000, "thousand": 1000, "million": 10**6, "millions": 10**6, "milliard": 10**9, "milliards": 10**9,
       "billion": 10**9}
NUMBER = {**NUMBER_WORDS, "cents": 100, "hundreds": 100, "six": 6, "zero": 0}


def _words_number(words: list[str]) -> int:
    """« deux mille trois cent quatre vingt dix » → 2390."""
    total, current = 0, 0
    for w in words:
        if w in BIG:
            total += (current or 1) * BIG[w]
            current = 0
        elif NUMBER[w] == 100:
            current = (current or 1) * 100
        elif NUMBER[w] == 20 and current % 100 == 4:  # quatre vingt
            current += 76
        else:
            current += NUMBER[w]
    return total + current


def _normalize(text: str) -> str:
    """Lowercase without accents, operation symbols kept (router.fold removes them): "10-3", "3×4", "(2+3)*4"."""
    s = unicodedata.normalize("NFD", text.lower().replace("’", "'").replace(" ", " ").replace(" ", " "))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"(?<=\d) (?=\d{3}\b)", "", s)  # « 1 000 » → 1000
    s = re.sub(r"(?<=\d),(?=\d)", ".", s)  # « 1,5 » → 1.5
    s = re.sub(r"(?<=\d)\s*[x×]\s*(?=\d)", " * ", s)  # « 3x4 »
    s = re.sub(r"\*\s*\*", " ^ ", s)  # « 2**10 »
    s = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", s)  # « dis-moi », « quatre-vingt »
    s = re.sub(r"[-−]", " - ", s)  # minus
    s = re.sub(r"([+*/()×÷^%])", r" \1 ", s)
    s = re.sub(r"(?<!\d)\.|\.(?!\d)|[?!;:,]", " ", s)  # punctuation, except the decimal point
    return re.sub(r"\s+", " ", s).strip()


def spoken(text: str) -> str | None:
    """Spoken sentence → expression (« combien font 15 % de 80 » → « 15 /100* 80 »), or None if not a calculation."""
    s = _normalize(text)
    s = re.sub(r"^(?:\S+\s+){1,2}(?=combien (?:font|fait)\b|calcule\b|how much is\b)", "", s)  # « comment f combien font… »
    s = END.sub("", START.sub("", s, count=1), count=1).strip()
    if not s:
        return None
    s = _WORDS.sub(lambda m: next(sym for w, sym in WORDS if re.fullmatch(w, m[1])), s)
    tokens, out, run, sqrt_open = s.split(), [], [], False

    def flush() -> None:
        nonlocal sqrt_open
        if run:
            out.append(str(_words_number(run)))
            run.clear()
            if sqrt_open:
                out.append(")")
                sqrt_open = False

    for i, tok in enumerate(tokens):
        if tok in NUMBER or tok in BIG:
            run.append(tok)
        elif tok in ("et", "and") and run and i + 1 < len(tokens) and tokens[i + 1] in ("un", "une", "onze"):
            continue  # vingt et un, soixante et onze
        elif re.fullmatch(r"\d+(?:\.\d+)?", tok):
            flush()
            out.append(tok)
            if sqrt_open:
                out.append(")")
                sqrt_open = False
        elif tok == "sqrt":
            flush()
            out.append("sqrt(")
            sqrt_open = True
        elif re.fullmatch(r"\*\*\d|/100\*?|0\.5\*|2\*|3\*|1/3\*|0\.25\*|\*\*|[-+*/()]", tok):
            flush()
            out.append(tok)
        elif tok in ("le", "la", "de", "of", "the", "et", "and", "ca", "egal", "egale"):
            continue  # « le double de », « 3 et demi » never get here: linking words only
        else:
            return None  # a word that is neither a number nor an operation: not a calculation
    flush()
    if sqrt_open:
        return None
    expression = " ".join(out)
    # « 80 plus 15 % »: 80 increased by 15% (92), not 80 + 0.15
    expression = re.sub(r"^(\d+(?:\.\d+)?) ([-+]) (\d+(?:\.\d+)?) /100$", r"\1 * (1 \2 \3 /100)", expression)
    return expression if has_operation(expression) else None


def result(value: float) -> tuple[str, bool]:
    """(number to say, rounded?): exact integer, otherwise 2 decimals (or 3 significant digits below 1)."""
    from .i18n import decimal

    if abs(value - round(value)) < 1e-9:
        return str(int(round(value))), False
    shown = round(value, 2) if abs(value) >= 1 else float(f"{value:.3g}")
    return decimal(shown), abs(shown - value) > 1e-9  # 0.1 + 0.2: exactly 0.3, not "about"
