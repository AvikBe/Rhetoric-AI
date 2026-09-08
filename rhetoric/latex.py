"""Everything that turns untrusted plain text into safe LaTeX.

The rule the whole pipeline rests on: the model never emits LaTeX. It emits
prose, and this module is the only thing allowed to add backslashes. Jinja's
`finalize` hook (see render.py) routes *every* interpolated value through
`to_latex`, so escaping is opt-out rather than opt-in -- forgetting a filter
can't produce a broken build.
"""

from __future__ import annotations

import re
import unicodedata

_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

# Longest-first so multi-char keys would win; single pass so replacements can't
# escape each other's output.
_ESCAPE_RE = re.compile("|".join(re.escape(k) for k in sorted(_ESCAPES, key=len, reverse=True)))

# Applied *after* escaping, so these may emit raw LaTeX.
_UNICODE_FIXUPS = {
    "‘": "`",
    "’": "'",
    "“": "``",
    "”": "''",
    "–": "--",
    "—": "---",
    "…": r"\ldots{}",
    "\u00a0": "~",  # non-breaking space
    "−": "-",
}


class Latex(str):
    """A string that is already valid LaTeX and must not be escaped again.

    Use it for renderer-generated markup only, never for model output.
    """


def escape(text: str) -> str:
    return _ESCAPE_RE.sub(lambda m: _ESCAPES[m.group()], text)


# Greek arrives in statistics prose ("Cohen's kappa") and has no glyph in the
# Times text font, so XeTeX drops it *silently*: the PDF read "Cohen's  of 0.83"
# with nothing between the words. Mapped to math mode, where the glyph exists.
_GREEK = {
    "α": r"$\alpha$", "β": r"$\beta$", "γ": r"$\gamma$", "δ": r"$\delta$",
    "ε": r"$\epsilon$", "η": r"$\eta$", "θ": r"$\theta$", "κ": r"$\kappa$",
    "λ": r"$\lambda$", "μ": r"$\mu$", "π": r"$\pi$", "ρ": r"$\rho$",
    "σ": r"$\sigma$", "τ": r"$\tau$", "φ": r"$\phi$", "χ": r"$\chi$",
    "ω": r"$\omega$", "Δ": r"$\Delta$", "Σ": r"$\Sigma$", "Ω": r"$\Omega$",
    "×": r"$\times$", "±": r"$\pm$", "≤": r"$\leq$", "≥": r"$\geq$",
    "≈": r"$\approx$", "≠": r"$\neq$", "°": r"$^\circ$",
}


def _fold_remaining(text: str) -> str:
    """Last resort for characters with no glyph in the document font.

    Anything still non-ASCII here would be dropped by the engine without a
    warning, so fold it to its closest ASCII form and drop only what has none.
    """
    out = []
    for ch in text:
        if ord(ch) < 128:
            out.append(ch)
            continue
        folded = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
        out.append(folded)
    return "".join(out)


_OPENS_QUOTE = set(" \t\n([{-")


def _directional_quotes(text: str) -> str:
    """Straight double quotes to TeX's `` / '' forms.

    Openness is decided by the preceding character, not the following one: a
    closing quote is very often followed by punctuation ("soup".) which a
    lookahead would misread as the start of a new quotation.
    """
    out: list[str] = []
    prev = ""
    for ch in text:
        if ch != '"':
            out.append(ch)
        elif prev == "" or prev in _OPENS_QUOTE:
            out.append("``")
        else:
            out.append("''")
        prev = ch
    return "".join(out)


def typographic(text: str) -> str:
    """TeX quotes and dashes, plus the unicode a model tends to emit."""
    for bad, good in _UNICODE_FIXUPS.items():
        text = text.replace(bad, good)
    text = _directional_quotes(text)
    text = re.sub(r"(?<!\.)\.\.\.(?!\.)", r"\\ldots{}", text)
    for char, macro in _GREEK.items():
        text = text.replace(char, macro)
    return _fold_remaining(text)


def to_latex(text: str) -> Latex:
    return Latex(typographic(escape(text)))


_IDENT_OK = re.compile(r"^[A-Za-z0-9:.-]+$")


def ident(value: str) -> Latex:
    """Pass an identifier through *unescaped*.

    Labels, citation keys and graphics filenames are consumed by \\csname, where
    an escaped underscore becomes a control sequence and the build dies with a
    baffling "Missing \\endcsname". They must not be escaped -- so they are
    charset-restricted at the schema boundary instead, and this asserts that
    restriction held before letting anything through unescaped.
    """
    if not _IDENT_OK.match(value):
        raise ValueError(f"unsafe identifier for LaTeX: {value!r}")
    return Latex(value)


# Citation markers. The model writes `[[smith2019]]` in plain prose and this
# turns it into \citep{} after escaping -- so the model still never emits a
# backslash, but the bibliography can actually be cited from the body. Keys are
# alphanumeric precisely so escaping leaves them untouched.
CITE_MARKER = re.compile(r"\[\[([A-Za-z0-9]+(?:\s*,\s*[A-Za-z0-9]+)*)\]\]")


def cite_keys(text: str) -> list[str]:
    return [k.strip() for m in CITE_MARKER.finditer(text) for k in m.group(1).split(",")]


def body(text: str) -> Latex:
    """Prose destined for the document body: escape, then resolve citations."""
    out = to_latex(text)
    resolved = CITE_MARKER.sub(
        lambda m: r"\citep{" + ",".join(k.strip() for k in m.group(1).split(",")) + "}", out
    )
    return Latex(resolved)


def pdf_string(text: str) -> Latex:
    """ASCII-only, markup-free text for hyperref's PDF metadata fields.

    hyperref writes these straight into the PDF catalog, where LaTeX escapes
    would show up literally in a reader's document-properties pane.
    """
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return Latex(re.sub(r"[\\{}()]", "", text))


def finalize(value: object) -> object:
    """Jinja `finalize`: escape every str except ones already marked Latex."""
    if isinstance(value, Latex):
        return value
    if isinstance(value, str):
        return to_latex(value)
    return value
