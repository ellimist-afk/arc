"""
ServiceRegistry for dependency injection and service management
"""

import logging
from typing import Dict, Any, Optional, Type, TypeVar, Generic
from datetime import datetime

logger = logging.getLogger(__name__)

T = TypeVar('T')

class ServiceRegistry:
    """
    Central registry for all bot services
    Provides dependency injection and service discovery
    """
    
    def __init__(self):
        """Initialize the service registry"""
        self.services: Dict[str, Any] = {}
        self.service_metadata: Dict[str, Dict[str, Any]] = {}
        self.initialization_order: list = []
        
    def register(
        self,
        name: str,
        service: Any,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Register a service
        
        Args:
            name: Service name
            service: Service instance
            metadata: Optional metadata about the service
        """
        if name in self.services:
            logger.warning(f"Overwriting existing service: {name}")
            
        self.services[name] = service
        self.service_metadata[name] = {
            'registered_at': datetime.now(),
            'type': type(service).__name__,
            **(metadata or {})
        }
        self.initialization_order.append(name)
        
        logger.info(f"Registered service: {name} ({type(service).__name__})")
        
    def get(self, name: str, default: Any = None) -> Any:
        """
        Get a service by name
        
        Args:
            name: Service name
            default: Default value if not found
            
        Returns:
            Service instance or default
        """
        return self.services.get(name, default)
        
    def get_typed(self, name: str, service_type: Type[T]) -> Optional[T]:
        """
        Get a service with type checking
        
        Args:
            name: Service name
            service_type: Expected type
            
        Returns:
            Service instance if type matches, None otherwise
        """
        service = self.services.get(name)
        if service and isinstance(service, service_type):
            return service
        return None
        
    def require(self, name: str) -> Any:
        """
        Get a required service (raises if not found)
        
        Args:
            name: Service name
            
        Returns:
            Service instance
            
        Raises:
            KeyError: If service not found
        """
        if name not in self.services:
            raise KeyError(f"Required service not found: {name}")
        return self.services[name]
        
    def has(self, name: str) -> bool:
        """
        Check if a service is registered
        
        Args:
            name: Service name
            
        Returns:
            True if service is registered
        """
        return name in self.services
        
    def remove(self, name: str) -> bool:
        """
        Remove a service from the registry
        
        Args:
            name: Service name
            
        Returns:
            True if service was removed
        """
        if name in self.services:
            del self.services[name]
            del self.service_metadata[name]
            self.initialization_order.remove(name)
            logger.info(f"Removed service: {name}")
            return True
        return False
        
    def get_all(self) -> Dict[str, Any]:
        """
        Get all registered services
        
        Returns:
            Dictionary of all services
        """
        return self.services.copy()
        
    def get_metadata(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Get metadata for a service
        
        Args:
            name: Service name
            
        Returns:
            Service metadata or None
        """
        return self.service_metadata.get(name)
        
    def get_stats(self) -> Dict[str, Any]:
        """
        Get registry statistics
        
        Returns:
            Statistics dictionary
        """
        return {
            'total_services': len(self.services),
            'services': list(self.services.keys()),
            'initialization_order': self.initialization_order,
            'metadata': {
                name: {
                    'type': meta.get('type'),
                    'registered_at': meta.get('registered_at').isoformat() if meta.get('registered_at') else None
                }
                for name, meta in self.service_metadata.items()
            }
        }
        
    def clear(self) -> None:
        """Clear all services from the registry"""
        self.services.clear()
        self.service_metadata.clear()
        self.initialization_order.clear()
        logger.info("Cleared all services from registry")
        
    async def shutdown_all(self) -> None:
        """
        Shutdown all services that have a shutdown method
        Services are shutdown in reverse initialization order
        """
        logger.info("Shutting down all services...")
        
        # Shutdown in reverse order
        for name in reversed(self.initialization_order):
            service = self.services.get(name)
            if service and hasattr(service, 'shutdown'):
                try:
                    logger.info(f"Shutting down service: {name}")
                    if asyncio.iscoroutinefunction(service.shutdown):
                        await service.shutdown()
                    else:
                        service.shutdown()
                except Exception as e:
                    logger.error(f"Error shutting down service {name}: {e}")
                    
        logger.info("All services shutdown complete")

class ServiceContainer:
    """
    Container for commonly used services
    Provides typed access to services
    """
    
    def __init__(self, registry: ServiceRegistry):
        """
        Initialize the service container
        
        Args:
            registry: ServiceRegistry instance
        """
        self.registry = registry
        
    @property
    def memory(self):
        """Get the memory service"""
        return self.registry.get('MemoryService')
        
    @property
    def twitch(self):
        """Get the Twitch service"""
        return self.registry.get('TwitchService')
        
    @property
    def audio(self):
        """Get the audio service"""
        return self.registry.get('AudioService')
        
    @property
    def personality(self):
        """Get the personality service"""
        return self.registry.get('PersonalityService')
        
    @property
    def websocket(self):
        """Get the WebSocket service"""
        return self.registry.get('WebSocketService')
        
    @property
    def llm(self):
        """Get the LLM service"""
        return self.registry.get('LLMService')
        
    @property
    def chat(self):
        """Get the chat service"""
        return self.registry.get('ChatService')
        
    @property
    def stream(self):
        """Get the stream service"""
        return self.registry.get('StreamService')
        
    @property
    def health(self):
        """Get the health service"""
        return self.registry.get('HealthService')
        
    @property
    def metrics(self):
        """Get the metrics service"""
        return self.registry.get('MetricsService')

# Global registry instance
_global_registry: Optional[ServiceRegistry] = None

def get_global_registry() -> ServiceRegistry:
    """
    Get the global ServiceRegistry instance
    
    Returns:
        The global ServiceRegistry
    """
    global _global_registry
    if _global_registry is None:
        _global_registry = ServiceRegistry()
    return _global_registry

def get_service(name: str) -> Any:
    """
    Convenience function to get a service from the global registry
    
    Args:
        name: Service name
        
    Returns:
        Service instance or None
    """
    return get_global_registry().get(name)

def register_service(
    name: str,
    service: Any,
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    """
    Convenience function to register a service in the global registry
    
    Args:
        name: Service name
        service: Service instance
        metadata: Optional metadata
    """
    get_global_registry().register(name, service, metadata)

import asyncio  # Import at the end to avoid circular imports