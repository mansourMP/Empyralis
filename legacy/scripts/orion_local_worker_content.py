from __future__ import annotations

from typing import Any, Dict


def _text_key(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _apply_content_plan_variation(
    items: list[Dict[str, str]],
    seed_topics: list[str],
    seed_channels: list[str],
    default_cta: str,
) -> list[Dict[str, str]]:
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    format_cycle = ["Video", "Image", "Carousel", "Story", "Reel", "Text", "Live"]
    angle_cycle = [
        "How-to",
        "FAQ",
        "Behind the Scenes",
        "Customer Story",
        "Offer Spotlight",
        "Checklist",
        "Comparison",
    ]
    cta_candidates = [
        default_cta or "Learn more",
        "Learn more",
        "Start now",
        "Book today",
        "See examples",
        "Try now",
        "Join today",
    ]
    cta_cycle: list[str] = []
    cta_cycle_keys: set[str] = set()
    for candidate in cta_candidates:
        key = _text_key(candidate)
        if key and key not in cta_cycle_keys:
            cta_cycle.append(candidate)
            cta_cycle_keys.add(key)

    out: list[Dict[str, str]] = []
    used_day_keys: set[str] = set()
    used_topic_keys: set[str] = set()
    used_headline_keys: set[str] = set()
    used_cta_keys: set[str] = set()

    for idx, raw in enumerate(items[:7]):
        item = dict(raw)

        day = str(item.get("day") or "").strip()
        day_key = _text_key(day)
        if not day or day_key in used_day_keys:
            day = weekdays[idx % len(weekdays)]
            day_key = _text_key(day)
        used_day_keys.add(day_key)

        channel = str(item.get("channel") or "").strip() or seed_channels[idx % len(seed_channels)] or "Instagram"

        fmt = str(item.get("format") or "").strip()
        if not fmt:
            fmt = format_cycle[idx % len(format_cycle)]
        elif out and _text_key(fmt) == _text_key(out[-1].get("format")):
            fmt = format_cycle[idx % len(format_cycle)]

        base_topic = str(item.get("topic") or "").strip()
        if not base_topic:
            base_topic = seed_topics[idx % len(seed_topics)] if seed_topics else f"Update {idx + 1}"
        topic = base_topic
        topic_key = _text_key(topic)
        if topic_key in used_topic_keys:
            seed = seed_topics[idx % len(seed_topics)] if seed_topics else base_topic
            angle = angle_cycle[idx % len(angle_cycle)]
            topic = f"{seed}: {angle}"
            topic_key = _text_key(topic)
            bump = 2
            while topic_key in used_topic_keys:
                topic = f"{seed}: {angle} {bump}"
                topic_key = _text_key(topic)
                bump += 1
        used_topic_keys.add(topic_key)

        cta = str(item.get("cta") or "").strip()
        cta_key = _text_key(cta)
        if not cta or (out and cta_key == _text_key(out[-1].get("cta"))):
            cta = cta_cycle[idx % len(cta_cycle)]
            cta_key = _text_key(cta)
        if cta_key in used_cta_keys and len(cta_cycle) > 1:
            for offset in range(1, len(cta_cycle) + 1):
                candidate = cta_cycle[(idx + offset) % len(cta_cycle)]
                candidate_key = _text_key(candidate)
                if candidate_key not in used_cta_keys:
                    cta = candidate
                    cta_key = candidate_key
                    break
        if cta_key in used_cta_keys:
            base_cta = default_cta or "Learn more"
            cta = f"{base_cta} ({weekdays[idx % len(weekdays)]})"
            cta_key = _text_key(cta)
            bump = 2
            while cta_key in used_cta_keys:
                cta = f"{base_cta} ({weekdays[idx % len(weekdays)]} {bump})"
                cta_key = _text_key(cta)
                bump += 1
        used_cta_keys.add(cta_key)

        headline = str(item.get("headline") or "").strip()
        headline_key = _text_key(headline)
        if not headline or headline_key in used_headline_keys:
            headline_templates = [
                "Discover the benefits of {topic}",
                "How to get started with {topic}",
                "Common mistakes to avoid with {topic}",
                "Real examples: {topic}",
                "Quick guide: {topic}",
                "What makes {topic} different",
                "Before you start: {topic}",
            ]
            headline = headline_templates[idx % len(headline_templates)].format(topic=topic)
            headline_key = _text_key(headline)
            bump = 2
            while headline_key in used_headline_keys:
                headline = f"{headline} ({bump})"
                headline_key = _text_key(headline)
                bump += 1
        used_headline_keys.add(headline_key)

        status = str(item.get("status") or "").strip() or "drafted"

        out.append(
            {
                "id": f"local-{idx + 1}",
                "day": day,
                "channel": channel,
                "format": fmt,
                "topic": topic,
                "headline": headline,
                "cta": cta,
                "status": status,
            }
        )

    return out


def normalize_content_plan_items(items: Any, topics: list[str], channels: list[str], offers: str) -> list[Dict[str, str]]:
    default_topics = topics[:4] or ["General update"]
    default_channels = channels or ["Instagram"]
    default_cta = offers or "Learn more"
    normalized: list[Dict[str, str]] = []
    if isinstance(items, list):
        for idx, raw in enumerate(items[:7]):
            if not isinstance(raw, dict):
                continue
            topic = str(raw.get("topic") or default_topics[idx % len(default_topics)]).strip() or default_topics[idx % len(default_topics)]
            channel = str(raw.get("channel") or default_channels[idx % len(default_channels)]).strip() or default_channels[idx % len(default_channels)]
            normalized.append(
                {
                    "id": f"local-{idx + 1}",
                    "day": str(raw.get("day") or f"Day {idx + 1}").strip() or f"Day {idx + 1}",
                    "channel": channel,
                    "format": str(raw.get("format") or "Post").strip() or "Post",
                    "topic": topic,
                    "headline": str(raw.get("headline") or f"{topic} in one minute").strip() or f"{topic} in one minute",
                    "cta": str(raw.get("cta") or default_cta).strip() or default_cta,
                    "status": str(raw.get("status") or "drafted").strip() or "drafted",
                }
            )
    if not normalized:
        for idx, topic in enumerate(default_topics):
            channel = default_channels[idx % len(default_channels)]
            normalized.append(
                {
                    "id": f"local-{idx + 1}",
                    "day": f"Day {idx + 1}",
                    "channel": channel,
                    "format": "Post",
                    "topic": topic,
                    "headline": f"{topic} in one minute",
                    "cta": default_cta,
                    "status": "drafted",
                }
            )
    return _apply_content_plan_variation(normalized, default_topics, default_channels, default_cta)
