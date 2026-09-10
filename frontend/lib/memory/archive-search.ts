import type { TimelineItem } from "@/lib/types";

export function archiveSearchText(item: TimelineItem) {
  return [
    item.story.title,
    item.story.body,
    item.life_stage,
    item.narrator_label,
    item.image_annotation,
    item.detail?.place_name,
    item.detail?.event_year,
    item.detail?.summary,
    ...(item.detail?.theme_tags ?? []),
    ...item.events.flatMap((event) => [event.time_expression, event.normalized_time]),
    ...item.contributions.flatMap((entry) => [entry.contributor_label, entry.body]),
    ...item.person_tags.flatMap((tag) => [tag.person_name, tag.note]),
  ]
    .filter((value) => value !== null && value !== undefined && value !== "")
    .join(" ")
    .toLocaleLowerCase("zh-CN");
}

export function matchesArchiveSearch(item: TimelineItem, query: string) {
  const normalized = query.trim().toLocaleLowerCase("zh-CN");
  return !normalized || archiveSearchText(item).includes(normalized);
}
