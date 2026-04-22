"""
Registry migration module stub - to be implemented
"""

import logging

logger = logging.getLogger(__name__)


class MigrationController:
    """Controls service registry migrations"""
    pass


def get_migration_controller():
    """Get migration controller"""
    return MigrationController()


def get_registry_mode():
    """Get current registry mode"""
    return "standard"