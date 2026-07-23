from __future__ import annotations

from services.image_catalog_service import image_catalog


def set_tags(image_rel: str, tags: list[str]) -> list[str]:
    return image_catalog.set_tags(image_rel, tags)


def delete_tag(tag: str) -> int:
    return image_catalog.delete_tag(tag)


def get_all_tags() -> list[str]:
    return image_catalog.get_all_tags()
