"""Credential service for VFS plugins that need password/key prompts.

Replaces the global ``_credential_provider`` with a proper service class
that can be registered once at startup and queried by any plugin.
"""

from __future__ import annotations

from typing import Protocol


class CredentialProvider(Protocol):
    """Callable that prompts the user for a credential and returns it."""

    def __call__(self, prompt: str) -> str | None: ...


class CredentialService:
    """Singleton service to manage credential prompt callbacks.

    Plugins call ``CredentialService.prompt()`` to show a dialog.
    The app registers the actual UI callback via ``register()``.
    """

    _instance: CredentialService | None = None
    _provider: CredentialProvider | None = None

    def __new__(cls) -> CredentialService:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def register(self, provider: CredentialProvider) -> None:
        """Register the UI credential prompt callback."""
        self._provider = provider

    def prompt(self, prompt_text: str) -> str | None:
        """Show a credential prompt dialog, or return None if not registered."""
        if self._provider is None:
            return None
        return self._provider(prompt_text)

    @classmethod
    def reset(cls) -> None:
        """Clear the registered provider (mainly for tests)."""
        cls._instance = None
        cls._provider = None
