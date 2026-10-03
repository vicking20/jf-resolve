import logging
import time
from typing import List

from backend.addons.interfaces import BasePlugin, CatalogueDefinition, CatalogueItem, CatalogueProvider
from backend.database import AsyncSessionLocal

from backend.services.settings_manager import SettingsManager
from backend.services.tmdb_service import TMDBService

logger = logging.getLogger(__name__)

_cache = {}
_cache_expiry = {}
CACHE_DURATION_SECONDS = 24 * 60 * 60


class Plugin(BasePlugin, CatalogueProvider):
    default_config = {"api_key": ""}

    async def on_load(self):
        logger.info("TMDB Core Plugin loaded")

    async def get_catalogues(self) -> List[CatalogueDefinition]:
        return [
            CatalogueDefinition(
                id="tmdb_trending_movies",
                name="Trending Movies",
                description="Movies trending this week",
                media_type="movie",
            ),
            CatalogueDefinition(
                id="tmdb_popular_movies",
                name="Popular Movies",
                description="Popular movies on TMDB",
                media_type="movie",
            ),
            CatalogueDefinition(
                id="tmdb_trending_tv",
                name="Trending TV",
                description="TV Shows trending this week",
                media_type="tv",
            ),
            CatalogueDefinition(
                id="tmdb_popular_tv",
                name="Popular Series",
                description="Popular TV Series on TMDB",
                media_type="tv",
            ),
            CatalogueDefinition(
                id="tmdb_top_rated_movies",
                name="Top Rated Movies",
                description="Highest rated movies of all time",
                media_type="movie",
            ),
            CatalogueDefinition(
                id="tmdb_top_rated_tv",
                name="Top Rated Series",
                description="Highest rated TV Series of all time",
                media_type="tv",
            ),
        ]

    async def get_items(self, catalogue_id: str, page: int = 1) -> List[CatalogueItem]:
        cache_key = f"{catalogue_id}_{page}"
        if cache_key in _cache and time.time() < _cache_expiry.get(cache_key, 0):
            logger.info(f"Returning cached items for {cache_key}")
            return _cache[cache_key]

        logger.info(f"Fetching fresh items for {cache_key}")
        async with AsyncSessionLocal() as db:
            settings_manager = SettingsManager(db)
            api_key = await settings_manager.get("tmdb_api_key") or self.config.get("api_key")

            if not api_key:
                logger.warning("No TMDB API Key configured")
                return []

            tmdb = TMDBService(api_key)

            try:
                results = []
                if catalogue_id == "tmdb_trending_movies":
                    data = await tmdb.get_trending("movie", "week", page)
                    results = data.get("results", [])
                elif catalogue_id == "tmdb_popular_movies":
                    data = await tmdb.get_popular("movie", page)
                    results = data.get("results", [])
                elif catalogue_id == "tmdb_trending_tv":
                    data = await tmdb.get_trending("tv", "week", page)
                    results = data.get("results", [])
                elif catalogue_id == "tmdb_popular_tv":
                    data = await tmdb.get_popular("tv", page)
                    results = data.get("results", [])
                elif catalogue_id == "tmdb_top_rated_movies":
                    data = await tmdb.get_top_rated("movie", page)
                    results = data.get("results", [])
                elif catalogue_id == "tmdb_top_rated_tv":
                    data = await tmdb.get_top_rated("tv", page)
                    results = data.get("results", [])

                items = []
                for item in results:
                    parsed = tmdb.parse_media_item(item)

                    media_type = parsed.get("media_type")
                    if catalogue_id.endswith("_movies") and media_type != "movie":
                        media_type = "movie"
                    if catalogue_id.endswith("_tv") and media_type != "tv":
                        media_type = "tv"

                    items.append(
                        CatalogueItem(
                            id=str(parsed.get("tmdb_id")),
                            title=parsed.get("title"),
                            poster_url=parsed.get("poster_path"),
                            backdrop_url=parsed.get("backdrop_path"),
                            overview=parsed.get("overview"),
                            media_type=media_type,
                            year=parsed.get("year"),
                            rating=parsed.get("vote_average"),
                        )
                    )

                _cache[cache_key] = items
                _cache_expiry[cache_key] = time.time() + CACHE_DURATION_SECONDS
                logger.info(f"Cached {len(items)} new items for {cache_key}")

                return items

            except Exception as e:
                logger.error(
                    f"Error fetching catalogue {catalogue_id}: {e}", exc_info=True
                )
                return []
            finally:
                await tmdb.close()
