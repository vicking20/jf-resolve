"""Content rating utilities for mapping TMDB certifications to our rating system"""

from typing import Dict, Optional

from ..models.user_profile import ContentRating

# Mapping of TMDB movie certifications (US) to our ContentRating enum
MOVIE_RATING_MAP = {
    "G": ContentRating.FAMILY,
    "PG": ContentRating.FAMILY,
    "PG-13": ContentRating.TEEN,
    "R": ContentRating.MATURE,
    "NC-17": ContentRating.ADULT,
    "NR": ContentRating.ADULT,  # Not Rated - assume adult
    "UR": ContentRating.ADULT,  # Unrated - assume adult
}

# Mapping of TMDB TV ratings (US) to our ContentRating enum
TV_RATING_MAP = {
    "TV-Y": ContentRating.FAMILY,  # All Children
    "TV-Y7": ContentRating.FAMILY,  # Older Children
    "TV-G": ContentRating.FAMILY,  # General Audience
    "TV-PG": ContentRating.TEEN,  # Parental Guidance
    "TV-14": ContentRating.TEEN,  # Parents Strongly Cautioned
    "TV-MA": ContentRating.MATURE,  # Mature Audience
    "NR": ContentRating.ADULT,  # Not Rated
}


def get_us_movie_certification(tmdb_data: Dict) -> Optional[str]:
    """
    Extract US movie certification from TMDB release_dates response

    Args:
        tmdb_data: TMDB movie details with release_dates appended

    Returns:
        US certification (e.g., "PG-13", "R") or None
    """
    release_dates = tmdb_data.get("release_dates", {}).get("results", [])

    # Look for US certification
    for country_data in release_dates:
        if country_data.get("iso_3166_1") == "US":
            certifications = country_data.get("release_dates", [])
            # Get the first non-empty certification
            for release in certifications:
                cert = release.get("certification")
                if cert:
                    return cert

    return None


def get_us_tv_rating(tmdb_data: Dict) -> Optional[str]:
    """
    Extract US TV rating from TMDB content_ratings response

    Args:
        tmdb_data: TMDB TV details with content_ratings appended

    Returns:
        US TV rating (e.g., "TV-14", "TV-MA") or None
    """
    content_ratings = tmdb_data.get("content_ratings", {}).get("results", [])

    # Look for US rating
    for rating_data in content_ratings:
        if rating_data.get("iso_3166_1") == "US":
            return rating_data.get("rating")

    return None


def map_certification_to_content_rating(
    certification: Optional[str], media_type: str
) -> Optional[str]:
    """
    Map TMDB certification to our content rating string

    Args:
        certification: TMDB certification (e.g., "PG-13", "TV-14")
        media_type: "movie" or "tv"

    Returns:
        Content rating string or None
    """
    if not certification:
        return None

    if media_type == "movie":
        rating_enum = MOVIE_RATING_MAP.get(certification)
    else:  # tv
        rating_enum = TV_RATING_MAP.get(certification)

    # Return the original certification string for storage
    # We'll use it for both display and filtering
    return certification if rating_enum else None


def get_content_rating_level(content_rating: Optional[str], media_type: str) -> int:
    """
    Get numeric level for content rating to enable filtering

    Returns:
        0 = FAMILY (G, PG, TV-Y, TV-G)
        1 = TEEN (PG-13, TV-PG, TV-14)
        2 = MATURE (R, TV-MA)
        3 = ADULT (NC-17, NR, UR)
        4 = UNKNOWN (no rating)
    """
    if not content_rating:
        return 4  # Unknown - most restrictive

    if media_type == "movie":
        rating_enum = MOVIE_RATING_MAP.get(content_rating)
    else:
        rating_enum = TV_RATING_MAP.get(content_rating)

    if not rating_enum:
        return 4  # Unknown

    level_map = {
        ContentRating.FAMILY: 0,
        ContentRating.TEEN: 1,
        ContentRating.MATURE: 2,
        ContentRating.ADULT: 3,
    }

    return level_map.get(rating_enum, 4)


def get_max_rating_level(max_content_rating: ContentRating) -> int:
    """
    Get the maximum allowed rating level from user's ContentRating preference

    Args:
        max_content_rating: User's max allowed content rating

    Returns:
        Maximum allowed numeric level
    """
    level_map = {
        ContentRating.FAMILY: 0,
        ContentRating.TEEN: 1,
        ContentRating.MATURE: 2,
        ContentRating.ADULT: 3,
        ContentRating.ALL: 4,  # Allow everything including unknown
    }

    return level_map.get(max_content_rating, 4)


def is_content_allowed(
    content_rating: Optional[str],
    media_type: str,
    max_content_rating: ContentRating,
) -> bool:
    """
    Check if content is allowed based on user's max content rating

    Args:
        content_rating: Content's rating (e.g., "PG-13", "TV-MA")
        media_type: "movie" or "tv"
        max_content_rating: User's maximum allowed rating

    Returns:
        True if content is allowed, False otherwise
    """
    content_level = get_content_rating_level(content_rating, media_type)
    max_level = get_max_rating_level(max_content_rating)

    return content_level <= max_level
