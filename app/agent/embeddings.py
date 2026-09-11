"""
WHAT:
    Turns text into a vector using Azure OpenAI, so a free-text query can
    be compared against the product embeddings stored in MongoDB.

WHY THIS EXISTS AT ALL:
    find_similar_products already does vector search without any of this,
    by reusing a product's STORED embedding as the query vector. That
    works precisely because stored-vs-stored is guaranteed to be the same
    model, whatever that model is.

    Free-text search cannot borrow that trick: "something cosy for
    winter" has no stored vector, so the query has to be embedded - which
    means knowing, and matching, the model that embedded the catalogue.

THE RULE THAT GOVERNS EVERYTHING HERE:
    A query embedded by a DIFFERENT model than the documents is not
    "slightly worse". It is meaningless. Cosine similarity between two
    unrelated vector spaces returns numbers that look entirely normal -
    ~0.0 to ~0.3, ranked, plausible - and are noise. Nothing errors,
    nothing logs, and the results read as merely mediocre rather than
    wrong.

    So this module refuses to run against a catalogue it did not embed:
    EMBEDDING_MODEL_TAG is written into every document by the re-embed
    script and checked before any search. A mismatch raises rather than
    quietly returning nonsense.

MECHANISM:
    Azure puts the deployment in the URL path rather than the request
    body, and pins behaviour to a dated api-version - the same shape as
    the chat endpoint in llm_client.py.
"""

import asyncio

import httpx
import structlog

from app.config.settings import get_settings

logger = structlog.get_logger()

# Stamped into every embedding document, and checked before searching.
# This is the AZURE tag; the active tag comes from embedding_model_tag()
# below, because the backend is now configurable.
AZURE_MODEL_TAG = "azure:text-embedding-3-small:1536"


def embedding_model_tag() -> str:
    """The tag identifying whatever model embeds queries right now.

    A FUNCTION, NOT A CONSTANT, because the backend is chosen in .env -
    and the tag has to track it exactly. If the tag could drift from the
    model it names, the guard that compares it would be worse than
    useless: it would actively certify a mismatch as safe.

    Change the model, the tag changes with it, and a catalogue embedded
    by the old one stops matching. That is the intended behaviour.
    """
    settings = get_settings()
    if settings.embedding_backend == "local":
        return (
            f"local:{settings.local_embedding_model}"
            f":{settings.local_embedding_dimensions}"
        )
    return AZURE_MODEL_TAG


# Backwards compatibility for anything still importing the old name.
# Deliberately the Azure value: modules that treat the tag as a constant
# predate the local backend and would be wrong under it anyway.
EMBEDDING_MODEL_TAG = AZURE_MODEL_TAG

REQUEST_TIMEOUT_SECONDS = 30.0

# Azure caps how many inputs one embeddings request may carry. Well
# under any documented limit, and small enough that a failure costs
# little to retry.
BATCH_SIZE = 64


class EmbeddingUnavailable(Exception):
    """Raised when the embedding endpoint cannot be reached or refuses."""


class EmbeddingDimensionMismatch(Exception):
    """Raised when a vector is not the width the index expects.

    SEPARATE FROM EmbeddingModelMismatch because the causes and the
    remedies differ. That one means the catalogue was embedded by a
    different MODEL - the fix is to re-embed or reconfigure. This one
    means a vector of the wrong WIDTH exists or is about to be written,
    which Atlas cannot index at all: the fix is to stop whatever
    produced it.

    Both are fatal on purpose. A wrong-width vector is not a slightly
    worse result - it is a document that silently vanishes from every
    search, because the index refuses to hold it.
    """


class EmbeddingModelMismatch(Exception):
    """Raised when the stored catalogue was embedded by a different model.

    Deliberately fatal. The alternative is returning ranked nonsense that
    looks like a working search.
    """


def expected_dimensions() -> int:
    """The vector width the ACTIVE backend must produce.

    Mirrors embedding_model_tag()'s backend switch deliberately: the tag
    and the width describe the same model, so they have to be read from
    the same place or they can disagree - and a guard whose two halves
    disagree certifies exactly the state it exists to catch.

    This value is also what the Atlas index was built with (see
    scripts/reembed_catalogue.py, which passes it as numDimensions), so
    "matches this" and "the index can hold it" are the same question.
    """
    settings = get_settings()
    if settings.embedding_backend == "local":
        return settings.local_embedding_dimensions
    return settings.azure_embedding_dimensions


def validate_dimensions(vectors: list, where: str = "embedding") -> None:
    """Refuses a batch containing a vector of the wrong width.

    CALLED AT THE SOURCE, not at each call site, so that "validate before
    writing" is not a step anyone can forget. Every vector in this
    project comes out of embed_texts(), and it cannot return one that
    would not fit the index.

    THE FAILURE THIS PREVENTS IS SILENT AND PERMANENT. Atlas will not
    index a vector whose width does not match numDimensions, so the
    document simply stops being findable - the search keeps working,
    returns slightly fewer results, and nothing anywhere errors. Writing
    such a vector is worse than failing to write one, so this raises.

    The offending POSITIONS are named, not just the count: with a batch
    of 64 the difference between "one bad vector" and "the model changed
    width" is the thing you need to know first.
    """
    expected = expected_dimensions()
    wrong = [
        (index, len(vector) if vector is not None else 0)
        for index, vector in enumerate(vectors)
        if vector is None or len(vector) != expected
    ]
    if not wrong:
        return

    detail = ", ".join(f"#{i} has {width}" for i, width in wrong[:5])
    more = f" (+{len(wrong) - 5} more)" if len(wrong) > 5 else ""
    logger.error(
        "embedding_dimension_mismatch",
        where=where,
        expected=expected,
        wrong_count=len(wrong),
        sample=detail,
        consequence="Atlas cannot index a vector of the wrong width, so the "
                    "document would silently disappear from every search",
    )
    raise EmbeddingDimensionMismatch(
        f"{where}: expected {expected}-dim vectors, got {detail}{more}. "
        f"Refusing to continue - a wrong-width vector is not indexable and "
        f"would vanish from search without erroring."
    )


def embeddings_configured() -> bool:
    settings = get_settings()
    if settings.embedding_backend == "local":
        return bool(settings.local_embedding_model)
    return bool(
        settings.azure_openai_endpoint
        and settings.azure_openai_api_key
        and settings.azure_embedding_deployment
    )


# ── Local backend ────────────────────────────────────────────────────
# Loaded once and kept, because loading is the expensive part: reading
# weights off disk costs seconds, encoding a short query costs
# milliseconds. A per-request load would make every search feel broken.
_local_model = None
_local_model_lock = asyncio.Lock()


class EmbeddingBackendUnavailable(EmbeddingUnavailable):
    """The configured backend cannot run here at all.

    Separate from EmbeddingUnavailable because the causes differ: that
    one means a network call failed and retrying may work, this one
    means sentence-transformers is not installed in this image and
    retrying never will.
    """


async def _get_local_model():
    global _local_model
    if _local_model is not None:
        return _local_model

    # The lock matters under uvicorn: several concurrent first-requests
    # would otherwise each load their own copy of the weights, which is
    # both slow and a real memory spike.
    async with _local_model_lock:
        if _local_model is not None:
            return _local_model

        settings = get_settings()
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingBackendUnavailable(
                "EMBEDDING_BACKEND=local needs sentence-transformers, which "
                "is not installed in this image. Install the 'local-embeddings' "
                "extra, or set EMBEDDING_BACKEND=azure."
            ) from exc

        name = settings.local_embedding_model
        logger.info("local_embedding_model_loading", model=name)
        try:
            # to_thread because loading is blocking CPU/disk work, and
            # doing it on the event loop stalls every other request.
            model = await asyncio.to_thread(SentenceTransformer, name)
        except Exception as exc:
            raise EmbeddingBackendUnavailable(
                f"could not load local embedding model {name!r}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        width = model.get_sentence_embedding_dimension()
        if width != settings.local_embedding_dimensions:
            # Fatal rather than adaptive: Atlas rejects a query vector of
            # the wrong width, so continuing only defers the failure to a
            # less obvious place.
            raise EmbeddingBackendUnavailable(
                f"{name} produces {width}-dim vectors but "
                f"LOCAL_EMBEDDING_DIMENSIONS is "
                f"{settings.local_embedding_dimensions}"
            )

        logger.info("local_embedding_model_ready", model=name, dimensions=width)
        _local_model = model
        return _local_model


async def _embed_local(texts: list[str]) -> list[list[float]]:
    model = await _get_local_model()

    def encode():
        # normalize_embeddings=True because the stored catalogue is unit
        # length. Cosine is scale-invariant so ranking is unaffected
        # either way, but Atlas's cosine similarity assumes it, and
        # matching the stored convention keeps scores comparable with
        # find_similar_products.
        return model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False
        )

    vectors = await asyncio.to_thread(encode)
    out = [list(map(float, v)) for v in vectors]
    validate_dimensions(out, where="local embedding backend")
    return out


def _endpoint() -> tuple[str, dict]:
    settings = get_settings()
    base = settings.azure_openai_endpoint.rstrip("/")
    url = (
        f"{base}/openai/deployments/{settings.azure_embedding_deployment}"
        f"/embeddings?api-version={settings.azure_openai_api_version}"
    )
    return url, {"api-key": settings.azure_openai_api_key}


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embeds a list of strings, in batches, preserving order."""
    settings = get_settings()
    if not embeddings_configured():
        missing = (
            "LOCAL_EMBEDDING_MODEL"
            if settings.embedding_backend == "local"
            else "AZURE_EMBEDDING_DEPLOYMENT"
        )
        raise EmbeddingUnavailable(
            f"{missing} is not configured - free-text semantic search "
            "needs an embedding model."
        )
    if not texts:
        return []

    if settings.embedding_backend == "local":
        return await _embed_local(texts)

    url, headers = _endpoint()
    vectors: list[list[float]] = []

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start:start + BATCH_SIZE]
            payload = {
                "input": batch,
                # Explicit rather than defaulted: the stored vectors and
                # the Atlas index are both fixed at this width, and a
                # silent change would produce vectors the index rejects.
                "dimensions": settings.azure_embedding_dimensions,
            }
            try:
                response = await client.post(url, headers=headers, json=payload)
            except httpx.HTTPError as exc:
                raise EmbeddingUnavailable(
                    f"embedding request failed: {type(exc).__name__}"
                ) from exc

            if response.status_code != 200:
                raise EmbeddingUnavailable(
                    f"embedding endpoint returned HTTP {response.status_code}: "
                    f"{response.text[:200]}"
                )

            body = response.json()
            # Azure does not guarantee ordering, but it does return an
            # index per item - sorting by it is what keeps vectors
            # aligned with the products they belong to.
            ordered = sorted(body.get("data", []), key=lambda d: d.get("index", 0))
            vectors.extend(item["embedding"] for item in ordered)

    if len(vectors) != len(texts):
        raise EmbeddingUnavailable(
            f"asked for {len(texts)} embeddings, received {len(vectors)}"
        )
    # THE LAST POINT AT WHICH A BAD VECTOR IS STILL CHEAP TO STOP.
    # `dimensions` is sent in the request, but that is a REQUEST - a
    # model that ignores it, a deployment pointed at a different model,
    # or a future API change all return a width nobody asked for, and
    # every one of them looks like success.
    validate_dimensions(vectors, where="Azure embedding response")
    return vectors


async def embed_query(text: str) -> list[float]:
    """One string, for a search query."""
    return (await embed_texts([text]))[0]


def product_text(product: dict) -> str:
    """The text a product is embedded FROM.

    Must stay identical between the re-embed script and anything that
    reasons about it, or documents and queries drift apart. Name first
    because it carries the most signal; description and tags add the
    vocabulary that makes "cosy" or "gift" land on something.
    """
    # tags and searchKeywords frequently hold the SAME values, which
    # would otherwise repeat every keyword twice in the embedded text.
    # Repetition adds no information and drags the vector toward those
    # terms, so keyword-heavy products would drift together for no
    # reason. Deduped while preserving order, since order carries
    # emphasis - name first is deliberate.
    keywords = []
    seen = set()
    for word in (product.get("tags") or []) + (product.get("searchKeywords") or []):
        cleaned = (word or "").strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            keywords.append(cleaned)

    parts = [
        product.get("name") or "",
        product.get("category") or "",
        product.get("subCategory") or "",
        product.get("description") or "",
        " ".join(keywords),
    ]
    return " ".join(p.strip() for p in parts if p and p.strip())[:8000]
