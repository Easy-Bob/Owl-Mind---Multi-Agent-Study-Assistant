# The evaluation corpus

Read this before quoting any number computed from `corpus.jsonl`.

## The caveat that governs everything

**Every case in this corpus was written by a Claude model, and the classifier
under test is a Claude model.**

That makes the current numbers a measure of **self-consistency, not
correctness**. The corpus will systematically over-represent phrasings the
model family finds natural and under-represent how students actually write —
which is the only population that matters. A high score here is evidence the
pipeline works end to end. It is not evidence the classifier is good.

This is why `source` is a field on every case and why every report breaks
metrics down by it. `source: "human"` cases are the trustworthy ones. There are
currently **none**, so the honest reading of any headline figure today is
"nothing is obviously broken".

The single highest-value contribution to this project is not more code. It is
fifty cases written by someone who has actually taken the course.

## What is in here

| | |
| --- | --- |
| Cases | 106 |
| Split | 63 train / 43 test, seeded and deterministic |
| Out of scope (`other`) | 20 |
| Composite (two intents, different groups) | 10 |
| Provenance | 106 seed, 0 human |

**The corpus is below its own target.** ISSUE-009 FR1 asks for ~10 cases per
intent; most classes have 4–5. `summarise()` reports every class under 8 as
"thin", and a test asserts that list is non-empty so the shortfall cannot be
quietly forgotten. At n=5 a single case flipping moves F1 by 0.1–0.2, so
**per-class numbers are directional, not decisive**, and the regression margin
is set to 0.05 to match.

Padding to 190 by paraphrasing existing cases would narrow the confidence
intervals on paper without adding information. Don't.

## Contamination

`_TEMPLATES` in `intent_recognizer.py` **is** the embedding index. A corpus case
that also appears there means the 0.35-weighted signal is scored against its own
material, so the number measures memorisation.

Two layers guard this, and both are enforced — `harness.run` refuses to score a
contaminated corpus rather than warning about it:

- **Lexical** (default suite, no dependencies): normalised equality, token
  Jaccard ≥ 0.8, character ratio ≥ 0.9.
- **Semantic** (needs the cached MiniLM model): cosine ≥ 0.92.

This is not theoretical. On its first run against the corpus its own author had
just written, the check found **14 collisions**:

| Layer | Found | Example |
| --- | --- | --- |
| exact | 6 | `tell me a joke` was verbatim a template |
| jaccard | 2 | `how much extra space does this recursion use` vs `how much space does this recursion use` |
| ratio | 2 | `is my answer to question three correct` vs `...question 3 correct` |
| cosine | 4 | `your hints are too vague to be useful` vs `your hints are too vague` |

The `ratio` layer was added *because* Jaccard missed the inflection case —
adding an "s" is a smaller edit than any word swap, and must not be the way
through.

## Adding cases

1. Write it. Prefer real phrasing over tidy phrasing; typos and run-on
   sentences are signal, not noise.
2. Set `source: "human"` if a person wrote it. This matters more than the case.
3. `python -m owl_mind.evaluation check` — must report clean.
4. The split is computed from a hash of the message, so adding cases never
   moves existing ones across the train/test line. Append freely.

## Fields

| Field | Required | Notes |
| --- | --- | --- |
| `message` | yes | the utterance |
| `expected_intent` | yes | must be in the taxonomy; `load()` rejects anything else |
| `source` | yes | `seed` (model-written) or `human` |
| `expected_primary_agent` | no | set where routing is the point |
| `expected_supporting_agents` | no | non-empty makes it a composite case |
| `note` | no | why the case is interesting — usually the boundary it probes |

Lines starting with `//` are comments and are skipped.
