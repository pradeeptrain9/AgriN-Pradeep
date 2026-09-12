"""Vertex AI prediction, for phones that cannot run the model themselves.

The on-device classifier is not negotiable and this does not replace it. Running
inference on the phone is what lets a farmer check a leaf standing in a field
with no signal, and moving that to a server would trade the product's best
property for a line on a slide.

What it fixes is a real hole underneath that claim. The bundled model needs a
working TensorFlow Lite delegate; on some low-end and older Android builds
`classify()` fails to load it at all. Today those phones get nothing from the
on-device path and go straight to the cloud vision model, which is the most
expensive answer available and overkill for a rice leaf the small CNN would have
handled. This serves the *same* rice classifier from Vertex AI for exactly those
clients: same weights, same labels, same gate, a fraction of the cost.

So the escalation order becomes:

    on-device CNN  ->  Vertex (same model, when the phone could not run it)
                   ->  Gemini multimodal  ->  inconclusive

The gate is applied to Vertex's output identically. A remote copy of a model is
not more trustworthy than a local one, and the thresholds exist because of what
the model is, not where it ran.

Auth uses a service-account access token. Vertex does not accept an API key, so
unlike every other provider here this one needs a credential file -- which is
why it is off unless configured, and why a node that never configures it loses
nothing it had before.
"""

from __future__ import annotations

import base64
import logging
import time

import httpx

logger = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/cloud-platform"

# Tokens last an hour; refreshed early so a prediction never races the expiry.
TOKEN_TTL_MARGIN_SECONDS = 300

_token: tuple[float, str] | None = None


class VertexUnavailable(RuntimeError):
    pass


def endpoint_url(project: str, location: str, endpoint_id: str) -> str:
    return (
        f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}"
        f"/locations/{location}/endpoints/{endpoint_id}:predict"
    )


def _access_token(credentials_path: str) -> str:
    """Exchange the service-account key for an access token, cached in process.

    `google-auth` is imported lazily. A node that does not use Vertex should not
    need the dependency installed, and on a field deployment every megabyte of
    image matters.
    """
    global _token
    if _token and _token[0] > time.time():
        return _token[1]

    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise VertexUnavailable(
            "google-auth is not installed, so Vertex AI cannot be reached."
        ) from exc

    try:
        creds = service_account.Credentials.from_service_account_file(
            credentials_path, scopes=[SCOPE]
        )
        creds.refresh(Request())
    except Exception as exc:  # noqa: BLE001 - file missing, malformed, or refused
        raise VertexUnavailable(f"Vertex credentials rejected: {exc}") from exc

    expiry = getattr(creds, "expiry", None)
    lifetime = 3600.0
    if expiry is not None:
        lifetime = max(60.0, expiry.timestamp() - time.time())
    _token = (time.time() + lifetime - TOKEN_TTL_MARGIN_SECONDS, creds.token)
    return creds.token


def parse_predictions(payload: dict, labels: list[str]) -> list[tuple[str, float]]:
    """Vertex's response into (class_code, probability), highest first.

    Two shapes are accepted because AutoML and a custom container disagree:
    AutoML returns parallel `displayNames`/`confidences`, a custom TFLite
    container typically returns a bare score vector. A vector whose length does
    not match the label list is refused rather than zipped short -- silently
    truncating would map probabilities onto the wrong diseases.
    """
    predictions = payload.get("predictions") or []
    if not predictions:
        raise VertexUnavailable("Vertex returned no predictions.")

    first = predictions[0]

    if isinstance(first, dict) and "displayNames" in first:
        names = first.get("displayNames") or []
        scores = first.get("confidences") or first.get("scores") or []
        if len(names) != len(scores):
            raise VertexUnavailable("Vertex returned mismatched names and scores.")
        pairs = [(str(n), float(s)) for n, s in zip(names, scores)]
    else:
        vector = first if isinstance(first, list) else first.get("output")
        if not isinstance(vector, list):
            raise VertexUnavailable("Vertex returned an unrecognised prediction shape.")
        if len(vector) != len(labels):
            raise VertexUnavailable(
                f"Vertex returned {len(vector)} scores for {len(labels)} labels."
            )
        pairs = [(labels[i], float(v)) for i, v in enumerate(vector)]

    return sorted(pairs, key=lambda p: p[1], reverse=True)


async def classify(
    image_bytes: bytes,
    *,
    labels: list[str],
    project: str,
    location: str,
    endpoint_id: str,
    credentials_path: str,
    client: httpx.AsyncClient | None = None,
) -> list[tuple[str, float]]:
    """Classify one leaf photograph. Raises rather than guessing."""
    if not (project and location and endpoint_id and credentials_path):
        raise VertexUnavailable("Vertex AI is not configured on this node.")

    token = _access_token(credentials_path)
    body = {
        "instances": [
            {"content": base64.b64encode(image_bytes).decode("ascii")}
        ]
    }

    owns = client is None
    client = client or httpx.AsyncClient()
    try:
        response = await client.post(
            endpoint_url(project, location, endpoint_id),
            headers={"Authorization": f"Bearer {token}"},
            json=body,
            timeout=45.0,
        )
    except httpx.HTTPError as exc:
        raise VertexUnavailable(f"Vertex unreachable: {exc}") from exc
    finally:
        if owns:
            await client.aclose()

    if response.status_code != 200:
        raise VertexUnavailable(
            f"Vertex prediction failed ({response.status_code}): {response.text[:200]}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise VertexUnavailable(f"Vertex returned non-JSON: {exc}") from exc

    return parse_predictions(payload, labels)
