"""Hybrid retrieval: Nemotron dense embeddings + BM25, fused with RRF, then cross-encoder rerank."""
import hashlib
import math
import os
import re
import threading

import numpy as np
import torch
from scipy import sparse
from sklearn.feature_extraction.text import CountVectorizer

from . import config

STOP = set(("a an and are as at be been but by can cant could did do does dont for from had has have he her his i im "
            "in into is it its ive just me my no not of on or our please said says she so that the their them then "
            "there they this to too up us was we were what when which who will with would you your hi hello thanks "
            "thank regards user customer issue problem help need needs also still again today now get got any some "
            "like").split())
TOKEN = re.compile(r"[a-z0-9]+")
EVIDENCE_STATUSES = {"Closed", "Resolved"}


def tokens(text):
    return [w for w in TOKEN.findall((text or "").lower().replace("'", "")) if len(w) > 1 and w not in STOP]


def problem_text(t):
    """What a new ticket looks like: symptoms, product, CI, category. Used for embedding and BM25."""
    cat = " / ".join(x for x in (t.get("cat") or []) if x)
    return "\n".join(x for x in [t.get("summary"), (t.get("details") or "")[:1500],
                                 f"Product: {t.get('product')}" if t.get("product") else "",
                                 f"CI: {t.get('ci')}" if t.get("ci") else "", f"Category: {cat}" if cat else ""] if x)


def passage(t):
    """What the reranker and the LLM see for a candidate ticket."""
    return (f"{t.get('summary') or ''}\n{(t.get('details') or '')[:600]}\n"
            f"Resolution: {(t.get('resolution') or '')[:400]}")


def query_text(q):
    if isinstance(q, str):
        return q
    cat = " / ".join(x for x in (q.get("cat") or []) if x)
    return "\n".join(x for x in [q.get("summary"), q.get("details"), q.get("product"), q.get("ci"), cat] if x)


class Encoder:
    _lock = threading.Lock()

    def __init__(self):
        from sentence_transformers import SentenceTransformer
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        dt = torch.bfloat16
        self.embed = SentenceTransformer(config.EMBED_MODEL, trust_remote_code=True, device=config.DEVICE,
                                         model_kwargs={"torch_dtype": dt})
        self.rtok = AutoTokenizer.from_pretrained(config.RERANK_MODEL, trust_remote_code=True, padding_side="left")
        if self.rtok.pad_token is None:
            self.rtok.pad_token = self.rtok.eos_token
        self.rmodel = AutoModelForSequenceClassification.from_pretrained(
            config.RERANK_MODEL, trust_remote_code=True, torch_dtype=dt).eval().to(config.DEVICE)
        if self.rmodel.config.pad_token_id is None:
            self.rmodel.config.pad_token_id = self.rtok.eos_token_id

    def docs(self, texts, batch_size=64):
        with self._lock:
            e = self.embed.encode_document(texts, batch_size=batch_size, normalize_embeddings=True,
                                           convert_to_numpy=True, show_progress_bar=False)
        return e.astype(np.float32)

    def query(self, text):
        with self._lock:
            e = self.embed.encode_query([text], normalize_embeddings=True, convert_to_numpy=True)
        return e[0].astype(np.float32)

    def rerank(self, q, passages, batch_size=16):
        scores = []
        with self._lock, torch.inference_mode():
            for i in range(0, len(passages), batch_size):
                texts = [f"question:{q} \n \n passage:{p}" for p in passages[i:i + batch_size]]
                b = self.rtok(texts, padding=True, truncation=True, return_tensors="pt", max_length=512)
                b = {k: v.to(config.DEVICE) for k, v in b.items()}
                scores += self.rmodel(**b).logits.view(-1).float().cpu().tolist()
        return scores


class Index:
    def __init__(self, tickets, encoder):
        self.enc = encoder
        self.tickets = tickets
        self.by_id = {t["id"]: t for t in tickets}
        texts = [problem_text(t) for t in tickets]
        self.emb = self._cached_embeddings(texts)
        self.vec = CountVectorizer(analyzer=tokens)
        tf = self.vec.fit_transform(texts).tocsc().astype(np.float32)
        n = tf.shape[0]
        df = np.diff(tf.indptr)
        self.idf = np.log(1 + (n - df + 0.5) / (df + 0.5)).astype(np.float32)
        dl = np.asarray(tf.sum(axis=1)).ravel()
        self.k1, self.b = 1.4, 0.75
        self.norm = (self.k1 * (1 - self.b + self.b * dl / dl.mean())).astype(np.float32)
        self.tf = tf
        self.evidence = np.array([t["status"] in EVIDENCE_STATUSES and bool(t.get("resolution")) for t in tickets])
        self._extra = {}

    def _cached_embeddings(self, texts):
        h = hashlib.sha1((config.EMBED_MODEL + "\x00" + "\x00".join(texts)).encode()).hexdigest()[:16]
        os.makedirs(config.CACHE_DIR, exist_ok=True)
        path = os.path.join(config.CACHE_DIR, f"emb-{h}.npy")
        if os.path.exists(path):
            return np.load(path)
        e = self.enc.docs(texts)
        np.save(path, e)
        return e

    def bm25(self, qtoks):
        scores = np.zeros(self.tf.shape[0], dtype=np.float32)
        voc = self.vec.vocabulary_
        for w in set(qtoks):
            j = voc.get(w)
            if j is None:
                continue
            col = self.tf.getcol(j)
            rows, f = col.indices, col.data
            scores[rows] += self.idf[j] * f * (self.k1 + 1) / (f + self.norm[rows])
        return scores

    def extra_embeddings(self, extra):
        """Embed client-side tickets (e.g. ServiceNow incidents accepted in the browser), cached by content."""
        todo = [t for t in extra if (t["id"], hash(problem_text(t))) not in self._extra]
        if todo:
            e = self.enc.docs([problem_text(t) for t in todo])
            for t, v in zip(todo, e):
                self._extra[(t["id"], hash(problem_text(t)))] = v
        return np.stack([self._extra[(t["id"], hash(problem_text(t)))] for t in extra]) if extra else None

    def search(self, query, k=6, sources=("servicenow", "helix"), exclude_id=None, extra=None, evidence_only=True,
               candidates=40, rerank=True, qvec=None, allowed=None):
        qt = query_text(query)
        qtoks = tokens(qt)
        qv = qvec if qvec is not None else self.enc.query(qt)
        pool = []  # (ticket, dense_score, bm25_score)
        if "helix" in sources:
            dense = self.emb @ qv
            bm = self.bm25(qtoks)
            mask = self.evidence if evidence_only else np.ones(len(self.tickets), bool)
            if allowed is not None:
                mask = mask & allowed
            if exclude_id and exclude_id in self.by_id:
                mask = mask.copy()
                mask[self.tickets.index(self.by_id[exclude_id])] = False
            d_rank = np.argsort(-np.where(mask, dense, -9))[:150]
            b_rank = np.argsort(-np.where(mask, bm, -9))[:150]
            rrf = {}
            for r, i in enumerate(d_rank):
                rrf[i] = rrf.get(i, 0) + 1 / (60 + r)
            for r, i in enumerate(b_rank):
                if bm[i] > 0:
                    rrf[i] = rrf.get(i, 0) + 1 / (60 + r)
            for i in sorted(rrf, key=rrf.get, reverse=True)[:candidates]:
                pool.append((self.tickets[i], float(dense[i]), rrf[i]))
        extra = [t for t in (extra or []) if t.get("id") != exclude_id and t.get("resolution")]
        if extra and "servicenow" in sources:
            ev = self.extra_embeddings(extra)
            for t, s in zip(extra, ev @ qv):
                pool.append((t, float(s), 1 / 61))
        if not pool:
            return []
        if rerank:
            scores = self.enc.rerank(qt[:2000], [passage(t) for t, _, _ in pool])
        else:
            scores = [d * 10 for _, d, _ in pool]
        order = sorted(range(len(pool)), key=lambda i: -scores[i])[:k]
        qset = set(qtoks)
        hits = []
        for i in order:
            t = pool[i][0]
            matched = sorted(qset & set(tokens(problem_text(t) + " " + (t.get("resolution") or ""))))
            hits.append(dict(ticket=t, score=round(scores[i], 4), sim=round(1 / (1 + math.exp(-scores[i] / 2)), 3),
                             dense=round(pool[i][1], 4), matchedTerms=matched[:20]))
        return hits
