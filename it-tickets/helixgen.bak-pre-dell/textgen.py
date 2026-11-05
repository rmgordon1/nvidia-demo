"""Template text rendering and realistic noise (typos, casing, signatures, vague summaries)."""
import re

KEYBOARD_NEIGHBORS = {
    "a": "qsz", "b": "vgn", "c": "xdv", "d": "sfe", "e": "wrd", "f": "dgr", "g": "fht", "h": "gjy",
    "i": "uok", "j": "hku", "k": "jli", "l": "kop", "m": "nj", "n": "bmh", "o": "ipl", "p": "ol",
    "q": "wa", "r": "eft", "s": "adw", "t": "ryg", "u": "yij", "v": "cbf", "w": "qes", "x": "zcs",
    "y": "tuh", "z": "xa",
}
COMMON_MISSPELL = {
    "receive": "recieve", "definitely": "definately", "separate": "seperate", "necessary": "neccessary",
    "occurred": "occured", "until": "untill", "access": "acess", "password": "pasword", "computer": "comptuer",
    "printer": "priner", "doesn't": "doesnt", "can't": "cant", "won't": "wont", "I'm": "im", "it's": "its",
    "please": "pls", "because": "bc", "thanks": "thx", "connection": "conection", "error": "eror",
}
TRIAGE_NOTES = [
    "Spoke with customer and verified contact info. Basic troubleshooting (restart, re-login) did not resolve.",
    "Unable to resolve at first contact.",
    "Reproduced issue with customer on remote session. Escalating.",
    "Gathered details and screenshots from customer. Outside Tier 1 scope.",
    "Checked KB - no matching known-error article. Needs Tier 2.",
    "Customer confirmed issue still occurring after reboot.",
    "Tried standard fix per KB, no luck.",
]
VAGUE_SUMMARIES = ["help", "not working", "issue", "computer problem", "URGENT", "need help asap", "problem",
                   "question", "IT issue", "see below", "broken", "error", "Help please", "it's not working again",
                   "FW: ", "RE: issue", "call me"]
PERSONA_NOISE = {"terse": 0.07, "average": 0.02, "detailed": 0.01, "frustrated": 0.03, "polite": 0.005,
                 "nontechnical": 0.03}


def typo_word(rng, w):
    if len(w) < 4 or not w.isalpha():
        return w
    i = int(rng.integers(1, len(w) - 1))
    op = rng.integers(4)
    if op == 0:  # swap
        return w[:i] + w[i + 1] + w[i] + w[i + 2:]
    if op == 1:  # drop
        return w[:i] + w[i + 1:]
    if op == 2:  # double
        return w[:i] + w[i] + w[i:]
    c = w[i].lower()
    return w[:i] + (rng.choice(list(KEYBOARD_NEIGHBORS.get(c, c)))) + w[i + 1:]


def add_typos(rng, text, rate):
    if rate <= 0:
        return text
    out = []
    for tok in re.split(r"(\s+)", text):
        low = tok.lower().strip(".,!?")
        if low in COMMON_MISSPELL and rng.random() < rate * 6:
            tok = tok.replace(tok.strip(".,!?"), COMMON_MISSPELL[low])
        elif rng.random() < rate:
            tok = typo_word(rng, tok)
        out.append(tok)
    return "".join(out)


def persona_style(rng, text, persona, field="desc"):
    """Apply persona-driven noise to user-authored text."""
    rate = PERSONA_NOISE.get(persona, 0.02)
    t = add_typos(rng, text, rate)
    if persona == "terse":
        if rng.random() < 0.6:
            t = t.lower()
        if rng.random() < 0.5:
            t = t.replace(". ", " ").rstrip(".")
    elif persona == "frustrated" and field == "summ" and rng.random() < 0.25:
        t = t.upper() + ("!!" if rng.random() < 0.5 else "")
    elif persona == "frustrated" and field == "desc" and rng.random() < 0.3:
        t += rng.choice([" This is the third time this month.", " I have a deadline today!!",
                         " Please call me ASAP.", " Why does this keep happening?"])
    return t


def signature(rng, person, site_name):
    lines = [rng.choice(["Thanks,", "Thank you,", "Regards,", "v/r,", "Thanks!", "Respectfully,"]), person.full]
    if rng.random() < 0.8:
        lines += [person.title, person.dept, "Bureau of Currency Production"]
    if rng.random() < 0.6:
        lines.append(f"Office: {person.phone}")
    if rng.random() < 0.15:
        lines.append(site_name)
    sig = "\n\n" + "\n".join(lines)
    if rng.random() < 0.08:
        sig = "\n\nSent from my iPhone"
    if rng.random() < 0.18:
        sig += ("\n\nCONFIDENTIALITY NOTICE: This e-mail message, including any attachments, is for the sole use of the "
                "intended recipient(s) and may contain sensitive but unclassified information. Any unauthorized review, "
                "use, disclosure or distribution is prohibited.")
    return sig


def fill(tpl, ctx):
    try:
        return tpl.format(**ctx)
    except (KeyError, IndexError):
        return re.sub(r"\{(\w+)\}", lambda m: str(ctx.get(m.group(1), m.group(0))), tpl)


def greeting(rng, persona):
    if persona == "polite":
        return rng.choice(["Good morning,\n\n", "Hello Service Desk,\n\n", "Good afternoon,\n\n", "Hi team,\n\n"])
    if persona in ("average", "frustrated") and rng.random() < 0.3:
        return rng.choice(["Hi,\n\n", "Hello,\n\n", "Hi IT,\n\n"])
    return ""
