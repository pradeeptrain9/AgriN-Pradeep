"""The node tells the app which basemap to draw.

Authenticated, because the response carries a tile URL the node is paying for.
That is a spend control, not a secrecy claim -- see `providers/basemap.py` for
why the key cannot be treated as secret once it reaches a phone at all.

Deliberately not part of the advisory payload. The advisory is cached by content
hash and narrated by a language model; a rotating session token in it would miss
the narration cache on every refresh and turn a free reload into a paid call.
"""

from fastapi import APIRouter, Depends

from app.config import get_settings
from app.providers import basemap as provider
from app.security import CurrentUser, current_user

router = APIRouter(tags=["meta"])


@router.get("/basemap")
async def basemap(user: CurrentUser = Depends(current_user)) -> dict:
    settings = get_settings()
    chosen = await provider.fetch(
        settings.google_maps_api_key,
        language=getattr(settings, "node_language", "en"),
        region=settings.node_country,
    )
    return {
        "provider": chosen.provider,
        "tile_url": chosen.tile_url,
        "attribution": chosen.attribution,
        "max_zoom": chosen.max_zoom,
        # The app draws a boundary differently on imagery than on a street map:
        # on streets there is nothing to trace against, so it says so rather
        # than letting a farmer believe they are aligning to their own field.
        "satellite": chosen.satellite,
    }
