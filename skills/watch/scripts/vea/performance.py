from datetime import datetime, timezone
from statistics import median

from .models import feature, provenance


def date(value):
    if not value:
        return None
    try:
        dt = (
            datetime.strptime(value, "%Y%m%d")
            if len(value) == 8
            else datetime.fromisoformat(value.replace("Z", "+00:00"))
        )
        return (
            dt.replace(tzinfo=timezone.utc)
            if dt.tzinfo is None
            else dt.astimezone(timezone.utc)
        )
    except (ValueError, TypeError):
        return None


def metrics(views, likes, comments, published_at, collected_at):
    p = provenance(
        "cumulative_count_ratios",
        "metadata",
        {"published_at": published_at, "collected_at": collected_at},
    )
    published, collected = date(published_at), date(collected_at)
    days = (
        (collected - published).total_seconds() / 86400
        if published and collected
        else None
    )

    def ratio(a, b):
        return (
            feature(a / b, prov=p)
            if a is not None and a >= 0 and b is not None and b > 0
            else feature(prov=p)
        )

    return {
        "raw_views": feature(views, prov=p),
        "views_per_day": ratio(views, days),
        "likes_per_view": ratio(likes, views),
        "comments_per_view": ratio(comments, views),
    }


def relative(target, candidates, include_shorts=False):
    published = date(target.get("published_at"))
    selected = []
    if published and target.get("channel_id"):
        dedup = {
            v["video_id"]: v
            for v in candidates
            if v.get("video_id") != target["video_id"]
        }
        eligible = [
            v
            for v in dedup.values()
            if v.get("channel_id") == target["channel_id"]
            and date(v.get("published_at"))
            and date(v["published_at"]) < published
            and (include_shorts or v.get("is_short") is False)
        ]
        selected = sorted(
            eligible, key=lambda v: date(v["published_at"]), reverse=True
        )[:10]
    available = [v for v in selected if v.get("views") is not None and v["views"] >= 0]
    denominator = median([v["views"] for v in available]) if available else None
    p = provenance(
        "previous_10_median_views",
        "performance_snapshots",
        {
            "include_shorts": include_shorts,
            "comparison_video_ids": [v["video_id"] for v in selected],
            "observations": available,
            "comparison_video_count": len(available),
            "median_views": denominator,
        },
    )
    valid = (
        denominator is not None and denominator > 0 and target.get("views") is not None
    )
    result = feature(target["views"] / denominator if valid else None, prov=p)
    result["comparison_video_count"] = len(available)
    result["warnings"] = (
        []
        if len(available) == 10
        else ["Fewer than 10 observed predecessors; baseline is incomplete."]
    )
    if not include_shorts:
        result["warnings"].append("Videos with unknown Shorts status excluded.")
    return result
