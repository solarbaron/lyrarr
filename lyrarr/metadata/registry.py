# coding=utf-8

"""
Provider plugin registry with auto-discovery.
Automatically finds and loads all CoverProvider and LyricsProvider subclasses
from the covers/ and lyrics/ directories.

Usage:
    from lyrarr.metadata.registry import cover_providers, lyrics_providers

    # Get all registered cover providers
    for name, provider in cover_providers.items():
        ...

    # Register a custom provider at runtime
    from lyrarr.metadata.registry import registry
    registry.register_cover(MyCoverProvider())
"""

import importlib
import inspect
import logging
import os
import pkgutil
from typing import Dict

from lyrarr.metadata.base import CoverProvider, LyricsProvider

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """Central registry for metadata providers with auto-discovery."""

    def __init__(self):
        self._cover_providers: Dict[str, CoverProvider] = {}
        self._lyrics_providers: Dict[str, LyricsProvider] = {}
        self._discovered = False

    @property
    def cover_providers(self) -> Dict[str, CoverProvider]:
        if not self._discovered:
            self._discover()
        return self._cover_providers

    @property
    def lyrics_providers(self) -> Dict[str, LyricsProvider]:
        if not self._discovered:
            self._discover()
        return self._lyrics_providers

    def register_cover(self, provider: CoverProvider):
        """Register a cover provider."""
        name = provider.name
        if name in self._cover_providers:
            logger.warning(f"Cover provider '{name}' already registered, replacing")
        self._cover_providers[name] = provider
        logger.debug(f"Registered cover provider: {name}")

    def register_lyrics(self, provider: LyricsProvider):
        """Register a lyrics provider."""
        name = provider.name
        if name in self._lyrics_providers:
            logger.warning(f"Lyrics provider '{name}' already registered, replacing")
        self._lyrics_providers[name] = provider
        logger.debug(f"Registered lyrics provider: {name}")

    def _discover(self):
        """Auto-discover providers from the covers/ and lyrics/ directories."""
        self._discovered = True
        base_dir = os.path.dirname(os.path.abspath(__file__))

        # Discover cover providers
        covers_dir = os.path.join(base_dir, 'covers')
        self._discover_package('lyrarr.metadata.covers', covers_dir, CoverProvider, self.register_cover)

        # Discover lyrics providers
        lyrics_dir = os.path.join(base_dir, 'lyrics')
        self._discover_package('lyrarr.metadata.lyrics', lyrics_dir, LyricsProvider, self.register_lyrics)

        logger.info(
            f"Provider registry: {len(self._cover_providers)} cover providers, "
            f"{len(self._lyrics_providers)} lyrics providers"
        )

    def _discover_package(self, package_name, package_dir, base_class, register_fn):
        """Scan a directory for modules containing provider subclasses."""
        if not os.path.isdir(package_dir):
            return

        for importer, modname, ispkg in pkgutil.iter_modules([package_dir]):
            if modname.startswith('_'):
                continue

            full_name = f'{package_name}.{modname}'
            try:
                module = importlib.import_module(full_name)

                # Find all subclasses of base_class in this module
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (inspect.isclass(attr)
                            and issubclass(attr, base_class)
                            and attr is not base_class
                            and not inspect.isabstract(attr)):
                        try:
                            instance = attr()
                            register_fn(instance)
                        except Exception as e:
                            logger.warning(f"Failed to instantiate {attr_name} from {modname}: {e}")

            except Exception as e:
                logger.warning(f"Failed to import provider module {full_name}: {e}")

    def get_all_names(self):
        """Get all registered provider names."""
        return {
            'cover': list(self.cover_providers.keys()),
            'lyrics': list(self.lyrics_providers.keys()),
        }


# Global singleton
registry = ProviderRegistry()

# Convenience aliases
cover_providers = registry.cover_providers
lyrics_providers = registry.lyrics_providers
