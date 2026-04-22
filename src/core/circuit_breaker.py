"""
Circuit Breaker Pattern Implementation
Prevents cascading failures by stopping calls to failing services
Based on PRD specifications for resilience
"""

import asyncio
import time
import logging
from typing import Any, Callable, Optional, Dict
from enum import Enum
from datetime import datetime, timedelta
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = "CLOSED"      # Normal operation
    OPEN = "OPEN"          # Failures exceeded threshold, blocking calls
    HALF_OPEN = "HALF_OPEN"  # Testing if service recovered


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open and blocking calls"""
    def __init__(self, service_name: str, recovery_time: float):
        self.service_name = service_name
        self.recovery_time = recovery_time
        super().__init__(
            f"Circuit breaker for {service_name} is OPEN. "
            f"Retry in {recovery_time:.1f} seconds"
        )


@dataclass
class CircuitBreakerStats:
    """Statistics for circuit breaker monitoring"""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    circuit_opens: int = 0
    last_failure_time: Optional[datetime] = None
    last_success_time: Optional[datetime] = None
    consecutive_failures: int = 0
    consecutive_successes: int = 0


class CircuitBreaker:
    """
    Circuit breaker implementation for external API calls
    Implements the circuit breaker pattern to prevent cascading failures
    """
    
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        success_threshold: int = 2,
        expected_exception: type = Exception,
        fallback_function: Optional[Callable] = None
    ):
        """
        Initialize circuit breaker
        
        Args:
            name: Name of the service/API being protected
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds to wait before attempting recovery
            success_threshold: Successes needed in HALF_OPEN to close circuit
            expected_exception: Exception type to catch (others will pass through)
            fallback_function: Optional function to call when circuit is open
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.expected_exception = expected_exception
        self.fallback_function = fallback_function
        
        # State management
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[float] = None
        self.last_state_change = time.time()
        
        # Statistics
        self.stats = CircuitBreakerStats()
        
        # Lock for thread safety
        self._lock = asyncio.Lock()
        
        logger.info(
            f"Circuit breaker '{name}' initialized: "
            f"failure_threshold={failure_threshold}, "
            f"recovery_timeout={recovery_timeout}s"
        )
    
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function through circuit breaker
        
        Args:
            func: Async function to execute
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func
            
        Returns:
            Result from func or fallback
            
        Raises:
            CircuitBreakerOpenError: When circuit is open
            Exception: When func fails and no fallback is available
        """
        async with self._lock:
            # Check circuit state
            if self.state == CircuitState.OPEN:
                if await self._should_attempt_reset():
                    self.state = CircuitState.HALF_OPEN
                    logger.info(f"Circuit breaker '{self.name}' entering HALF_OPEN state")
                else:
                    # Circuit is still open
                    self.stats.total_calls += 1
                    recovery_time = self.recovery_timeout - (time.time() - self.last_failure_time)
                    
                    if self.fallback_function:
                        logger.debug(f"Circuit breaker '{self.name}' is OPEN, using fallback")
                        return await self._call_fallback(*args, **kwargs)
                    else:
                        raise CircuitBreakerOpenError(self.name, recovery_time)
        
        # Try to execute the function
        try:
            self.stats.total_calls += 1
            result = await func(*args, **kwargs)
            await self._on_success()
            return result
            
        except self.expected_exception as e:
            await self._on_failure(e)
            
            # Use fallback if available
            if self.fallback_function:
                logger.debug(f"Using fallback for '{self.name}' after failure: {e}")
                return await self._call_fallback(*args, **kwargs)
            raise
    
    async def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset"""
        return (
            self.last_failure_time is not None and
            time.time() - self.last_failure_time >= self.recovery_timeout
        )
    
    async def _on_success(self):
        """Handle successful call"""
        async with self._lock:
            self.stats.successful_calls += 1
            self.stats.last_success_time = datetime.now()
            self.stats.consecutive_successes += 1
            self.stats.consecutive_failures = 0
            
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                
                if self.success_count >= self.success_threshold:
                    # Enough successes, close the circuit
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
                    self.success_count = 0
                    self.last_state_change = time.time()
                    logger.info(f"Circuit breaker '{self.name}' is now CLOSED (recovered)")
            
            elif self.state == CircuitState.CLOSED:
                # Reset failure count on success in closed state
                self.failure_count = 0
    
    async def _on_failure(self, error: Exception):
        """Handle failed call"""
        async with self._lock:
            self.stats.failed_calls += 1
            self.stats.last_failure_time = datetime.now()
            self.stats.consecutive_failures += 1
            self.stats.consecutive_successes = 0
            
            self.failure_count += 1
            self.last_failure_time = time.time()
            
            logger.warning(
                f"Circuit breaker '{self.name}' failure {self.failure_count}/{self.failure_threshold}: {error}"
            )
            
            if self.state == CircuitState.HALF_OPEN:
                # Failed in half-open state, reopen immediately
                self.state = CircuitState.OPEN
                self.success_count = 0
                self.last_state_change = time.time()
                self.stats.circuit_opens += 1
                logger.warning(f"Circuit breaker '{self.name}' reopened after failure in HALF_OPEN")
                
            elif self.state == CircuitState.CLOSED:
                if self.failure_count >= self.failure_threshold:
                    # Too many failures, open the circuit
                    self.state = CircuitState.OPEN
                    self.last_state_change = time.time()
                    self.stats.circuit_opens += 1
                    logger.error(
                        f"Circuit breaker '{self.name}' is now OPEN after "
                        f"{self.failure_count} consecutive failures"
                    )
    
    async def _call_fallback(self, *args, **kwargs) -> Any:
        """Call fallback function if available"""
        if self.fallback_function:
            if asyncio.iscoroutinefunction(self.fallback_function):
                return await self.fallback_function(*args, **kwargs)
            else:
                return self.fallback_function(*args, **kwargs)
        return None
    
    def get_state(self) -> str:
        """Get current circuit state"""
        return self.state.value
    
    def get_stats(self) -> Dict[str, Any]:
        """Get circuit breaker statistics"""
        success_rate = 0
        if self.stats.total_calls > 0:
            success_rate = (self.stats.successful_calls / self.stats.total_calls) * 100
        
        return {
            "name": self.name,
            "state": self.state.value,
            "total_calls": self.stats.total_calls,
            "successful_calls": self.stats.successful_calls,
            "failed_calls": self.stats.failed_calls,
            "success_rate": f"{success_rate:.1f}%",
            "circuit_opens": self.stats.circuit_opens,
            "consecutive_failures": self.stats.consecutive_failures,
            "consecutive_successes": self.stats.consecutive_successes,
            "last_failure": self.stats.last_failure_time.isoformat() if self.stats.last_failure_time else None,
            "last_success": self.stats.last_success_time.isoformat() if self.stats.last_success_time else None,
            "uptime": f"{time.time() - self.last_state_change:.1f}s"
        }
    
    async def reset(self):
        """Manually reset the circuit breaker"""
        async with self._lock:
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.success_count = 0
            self.last_failure_time = None
            self.last_state_change = time.time()
            logger.info(f"Circuit breaker '{self.name}' manually reset to CLOSED")
    
    async def trip(self):
        """Manually trip the circuit breaker (open it)"""
        async with self._lock:
            self.state = CircuitState.OPEN
            self.last_failure_time = time.time()
            self.last_state_change = time.time()
            self.stats.circuit_opens += 1
            logger.warning(f"Circuit breaker '{self.name}' manually tripped to OPEN")


class CircuitBreakerManager:
    """
    Manages multiple circuit breakers for different services
    """
    
    def __init__(self):
        self.breakers: Dict[str, CircuitBreaker] = {}
        
    def add_breaker(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        success_threshold: int = 2,
        expected_exception: type = Exception,
        fallback_function: Optional[Callable] = None
    ) -> CircuitBreaker:
        """
        Add a new circuit breaker
        
        Args:
            name: Service name
            failure_threshold: Failures before opening
            recovery_timeout: Recovery timeout in seconds
            success_threshold: Successes needed to close
            expected_exception: Exception type to handle
            fallback_function: Optional fallback
            
        Returns:
            Created CircuitBreaker instance
        """
        if name not in self.breakers:
            self.breakers[name] = CircuitBreaker(
                name=name,
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
                success_threshold=success_threshold,
                expected_exception=expected_exception,
                fallback_function=fallback_function
            )
            logger.info(f"Added circuit breaker for service: {name}")
        
        return self.breakers[name]
    
    def get_breaker(self, name: str) -> Optional[CircuitBreaker]:
        """Get circuit breaker by name"""
        return self.breakers.get(name)
    
    def get_all_stats(self) -> Dict[str, Any]:
        """Get statistics for all circuit breakers"""
        return {
            name: breaker.get_stats()
            for name, breaker in self.breakers.items()
        }
    
    async def reset_all(self):
        """Reset all circuit breakers"""
        for breaker in self.breakers.values():
            await breaker.reset()
        logger.info("All circuit breakers reset")
    
    def get_health_status(self) -> Dict[str, Any]:
        """Get health status of all services"""
        total = len(self.breakers)
        open_count = sum(1 for b in self.breakers.values() if b.state == CircuitState.OPEN)
        half_open_count = sum(1 for b in self.breakers.values() if b.state == CircuitState.HALF_OPEN)
        
        return {
            "total_services": total,
            "healthy": total - open_count,
            "degraded": half_open_count,
            "unhealthy": open_count,
            "services": {
                name: breaker.get_state()
                for name, breaker in self.breakers.items()
            }
        }


# Global circuit breaker manager instance
circuit_breaker_manager = CircuitBreakerManager()