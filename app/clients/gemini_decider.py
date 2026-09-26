"""Fallback decider: answers TypeSafe-style Noul/Choice/Score questions with gemini-3.8-flash.

Same questions in, same answer dataclasses out (typesafe_sdk NoulAnswer / ChoiceAnswer / ScoreAnswer), so
callers cannot tell which decider ran except via `source`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from typesafe_sdk import ChoiceAnswer, NoulAnswer, ScoreAnswer

from app.clients._common import is_mock, load_mock
from app.clients.gemini import GeminiClient
from app.config import Config

log = logging.getLogger(__name__)


@dataclass
class DeciderResponse:
    model: str
    answers: dict[str, Any]
    source: str = "gemini_fallback"
    usage: dict[str, Any] = field(default_factory=dict)


def _labels(q: Any) -> list[str]:
    crit = q.criteria
    return list(crit.keys()) if isinstance(crit, dict) else [str(c) for c in crit]


def response_schema(questions: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for key, q in questions.items():
        if q.type == "noul":
            props[key] = {"type": "object", "properties": {"p_yes": {"type": "number"}}, "required": ["p_yes"]}
        else:
            labels = _labels(q)
            props[key] = {"type": "object", "properties": {lbl: {"type": "number"} for lbl in labels},
                          "required": labels}
    return {"type": "object", "properties": props, "required": list(props)}


def build_prompt(state: Any, questions: dict[str, Any]) -> str:
    lines = ["You are a careful, calibrated judge. Read STATE and answer every QUESTION with probabilities. "
             "Use intermediate probabilities when the evidence is uncertain; never round to 0 or 1 unless certain.",
             f"STATE:\n{json.dumps(state, ensure_ascii=False)}", "QUESTIONS:"]
    for key, q in questions.items():
        if q.type == "noul":
            lines.append(f"- {key} (yes/no): {q.instructions}\n  Return p_yes = probability the answer is yes.")
        elif q.type == "choice":
            opts = "\n".join(f"    - {k}: {v}" for k, v in q.criteria.items())
            lines.append(f"- {key} (pick one): {q.instructions}\n  Options:\n{opts}\n"
                         "  Return a probability for every option; they must sum to 1.")
        else:
            levels = ", ".join(f"{i} = {c}" for i, c in enumerate(_labels(q)))
            lines.append(f"- {key} (rate on an ordered scale {levels}): {q.instructions}\n"
                         "  Return a probability for every level (keyed by its name); they must sum to 1.")
    return "\n".join(lines)


def _norm(probs: dict[str, float]) -> dict[str, float]:
    clean = {k: max(0.0, float(v)) for k, v in probs.items()}
    total = sum(clean.values())
    if total <= 0:
        return {k: 1.0 / len(clean) for k in clean}
    return {k: v / total for k, v in clean.items()}


def to_answers(raw: dict[str, Any], questions: dict[str, Any]) -> dict[str, Any]:
    answers: dict[str, Any] = {}
    for key, q in questions.items():
        r = raw[key]
        if q.type == "noul":
            answers[key] = NoulAnswer(noul=min(1.0, max(0.0, float(r["p_yes"]))))
        elif q.type == "choice":
            probs = _norm({lbl: r.get(lbl, 0.0) for lbl in _labels(q)})
            best = max(probs, key=probs.get)
            answers[key] = ChoiceAnswer(choice=best, confidence=probs[best], probabilities=probs)
        else:
            labels = _labels(q)
            probs = _norm({lbl: r.get(lbl, 0.0) for lbl in labels})
            by_idx = {i: probs[lbl] for i, lbl in enumerate(labels)}
            answers[key] = ScoreAnswer(score=sum(i * p for i, p in by_idx.items()), confidence=max(by_idx.values()),
                                       legend=dict(enumerate(labels)), probabilities=by_idx)
    return answers


class GeminiDecider:
    source = "gemini_fallback"

    def __init__(self, cfg: Config, raw_dir: Path | None = None):
        self.cfg = cfg
        self.gemini = GeminiClient(cfg, raw_dir=raw_dir)

    def _ask(self, name: str, state: Any, questions: dict[str, Any], media: list[dict] | None) -> dict[str, Any]:
        if is_mock():
            return load_mock(f"decider_{name.split(':')[0]}")
        content: list[dict] = [*(media or []), {"type": "text", "text": build_prompt(state, questions)}]
        it = self.gemini.create(
            f"decider_{name}", self.cfg.timeouts.gemini_video_s if media else self.cfg.timeouts.gemini_text_s,
            model=self.cfg.models.text, input=content,
            response_format={"type": "text", "mime_type": "application/json",
                             "schema": response_schema(questions)},
            generation_config={"thinking_level": "low"})
        return {"model": it.model, "raw": json.loads(it.output_text)}

    async def system_one(self, name: str, state: Any, questions: dict[str, Any],
                         media: list[dict] | None = None) -> DeciderResponse:
        out = await asyncio.to_thread(self._ask, name, state, questions, media)
        return DeciderResponse(model=out["model"], answers=to_answers(out["raw"], questions))
