"""Two-tier model selection: a small model for easy cases, the big model for hard ones.

"Easy" is decided from measurable signals, not from the text itself:
  - retrieval: the best past incidents match strongly and agree with each other
  - routing: the classifier is confident about the owning group
  - conversation: short, and not about security or production (OT) equipment
Agent copilot chat, LLM routing fallback and manager text-to-SQL always use the big model.
An explicit `model` from the client (the Admin model picker) always wins.
"""
import re

from . import config, llm

RISKY = re.compile(r"phish|password|credential|clicked|malware|virus|ransom|hack|breach|suspicious|"
                   r"press|plc|hmi|scada|mes\b|planttrack|inspection|intaglio|offset|vault", re.I)


def _strong_evidence(hits):
    if len(hits) < 3:
        return False, f"only {len(hits)} similar incidents"
    top = [h["sim"] for h in hits[:3]]
    if top[0] < config.TIER_MIN_SIM:
        return False, f"best match {top[0]:.2f} < {config.TIER_MIN_SIM}"
    if sum(top) / 3 < config.TIER_MIN_SIM - 0.05:
        return False, "top matches disagree"
    groups = {h["ticket"].get("group") for h in hits[:3]}
    if len(groups) > 2:
        return False, "similar incidents went to different groups"
    return True, f"strong evidence (best match {top[0]:.2f})"


def pick(task, explicit=None, hits=None, routing=None, messages=None):
    """Returns (model_name, tier, reason)."""
    if explicit:
        return explicit, "pinned", "model chosen by the user"
    big, small = config.BIG_LLM, config.SMALL_LLM
    if not small or small == big or task not in ("recommend", "selfservice"):
        return big, "big", f"{task} always uses the big model"
    ok, why = _strong_evidence(hits or [])
    if not ok:
        return big, "big", why
    if task == "recommend":
        conf = ((routing or {}).get("group") or {}).get("confidence", 0)
        if conf < config.TIER_MIN_ROUTE_CONF:
            return big, "big", f"routing confidence {conf:.2f} < {config.TIER_MIN_ROUTE_CONF}"
        return small, "small", why
    users = [m.get("content", "") for m in (messages or []) if m.get("role") == "user"]
    if len(users) > 2:
        return big, "big", "longer conversation"
    if RISKY.search(" ".join(users)):
        return big, "big", "security or production equipment"
    return small, "small", why


def call(fn, task, explicit=None, **signals):
    """Run fn(model) with the picked tier; if the small model fails, retry once on the big model."""
    model, tier, reason = pick(task, explicit, **signals)
    try:
        out = fn(model)
        return out, dict(tier=tier, model=model, reason=reason)
    except llm.LLMError as e:
        if tier != "small":
            raise
        out = fn(config.BIG_LLM)
        return out, dict(tier="big", model=config.BIG_LLM, reason=f"small model failed ({str(e)[:80]}), escalated")
