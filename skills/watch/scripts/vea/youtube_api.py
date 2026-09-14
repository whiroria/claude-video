"""Optional YouTube Data API metadata and complete preceding-video catalog."""

import json
import os
import re
import urllib.parse
import urllib.request

from .models import now
from .performance import date


def request(resource, parameters, key):
    url = (
        "https://www.googleapis.com/youtube/v3/"
        + resource
        + "?"
        + urllib.parse.urlencode(parameters)
    )
    req = urllib.request.Request(url, headers={"X-Goog-Api-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except Exception as exc:
        # Do not print request objects or credentials.
        raise RuntimeError(
            "YouTube Data API request failed: " + type(exc).__name__
        ) from None


def normalize(item, collected_at):
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    duration = item.get("contentDetails", {}).get("duration", "")
    m = re.fullmatch(r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    seconds = (
        sum(int(v or 0) * factor for v, factor in zip(m.groups(), (86400, 3600, 60, 1)))
        if m
        else None
    )
    count = lambda key: int(stats[key]) if key in stats else None
    thumbs = snippet.get("thumbnails", {})
    thumb = next(
        (
            thumbs[k]["url"]
            for k in ("maxres", "standard", "high", "medium", "default")
            if k in thumbs
        ),
        None,
    )
    return {
        "video_id": "yt:" + item["id"],
        "channel_id": snippet.get("channelId"),
        "title": snippet.get("title"),
        "description": snippet.get("description"),
        "published_at": snippet.get("publishedAt"),
        "duration_seconds": seconds,
        "views": count("viewCount"),
        "likes": count("likeCount"),
        "comments": count("commentCount"),
        "tags": snippet.get("tags"),
        "category_id": snippet.get("categoryId"),
        "thumbnail_url": thumb,
        "is_short": False if seconds is not None and seconds > 180 else None,
        "collected_at": collected_at,
        "source": "youtube_data_api",
    }


def collect(video_id, include_shorts=True, max_pages=20, key=None):
    key = key or os.environ.get("YOUTUBE_API_KEY")
    if not key:
        raise ValueError("YOUTUBE_API_KEY not configured")
    collected = now()
    items = request(
        "videos",
        {
            "part": "snippet,statistics,contentDetails",
            "id": video_id.removeprefix("yt:"),
        },
        key,
    ).get("items", [])
    if not items:
        raise ValueError("YouTube video unavailable")
    target = normalize(items[0], collected)
    channels = request(
        "channels", {"part": "contentDetails", "id": target["channel_id"]}, key
    ).get("items", [])
    if not channels:
        return target, [], False
    playlist = channels[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    candidates = []
    token = None
    for _ in range(max_pages):
        params = {"part": "contentDetails", "playlistId": playlist, "maxResults": 50}
        if token:
            params["pageToken"] = token
        page = request("playlistItems", params, key)
        ids = [i["contentDetails"]["videoId"] for i in page.get("items", [])]
        if ids:
            raw = request(
                "videos",
                {"part": "snippet,statistics,contentDetails", "id": ",".join(ids)},
                key,
            )
            candidates.extend(normalize(i, now()) for i in raw.get("items", []))
        prior = [
            v
            for v in candidates
            if date(v["published_at"])
            and date(v["published_at"]) < date(target["published_at"])
            and (include_shorts or v["is_short"] is False)
        ]
        token = page.get("nextPageToken")
        # Uploads playlist is ordered newest first. Excluding Shorts cannot classify <=180s via the API.
        if not token or len(prior) >= 10:
            return target, candidates, include_shorts
    return target, candidates, False
