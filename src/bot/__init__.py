"""
Bot module initialization
"""

from src.utils.task_registry import TaskRegistry
from src.services.service_registry import ServiceRegistry

# Create global registries
_task_registry: TaskRegistry = None
_service_registry: ServiceRegistry = None


def get_registry() -> TaskRegistry:
    """Get the global task registry"""
    global _task_registry
    if _task_registry is None:
        _task_registry = TaskRegistry()
    return _task_registry


def get_service_registry() -> ServiceRegistry:
    """Get the global service registry"""
    global _service_registry
    if _service_registry is None:
        _service_registry = ServiceRegistry()
    return _service_registry


__all__ = ['get_registry', 'get_service_registry']