"""
Network Resilience Layer - PRD Required Component (Section 1.3)
Circuit breakers, exponential backoff, connection pooling, fallback chains
"""

import asyncio
import time
import random
import logging
from typing import Optional, Callable, Any, Dict, TypeVar, Coroutine
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import aiohttp

logger = logging.getLogger(__name__)

T = TypeVar('T')


class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"      # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitStats:
    """Statistics for circuit breaker"""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    last_failure_time: Optional[datetime] = None
    consecutive_failures: int = 0
    state_changes: list = field(default_factory=list)


class CircuitBreaker:
    """
    Circuit breaker pattern implementation
    PRD Section 1.3 - Circuit breakers for all external API calls
    """
    
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        expected_exception: type = Exception
    ):
        """
        Initialize circuit breaker
        
        Args:
            name: Circuit breaker name for logging
            failure_threshold: Number of failures before opening
            recovery_timeout: Seconds before attempting recovery
            expected_exception: Exception type to catch
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        
        self.state = CircuitState.CLOSED
        self.stats = CircuitStats()
        self._half_open_attempts = 0
        
    def _record_success(self):
        """Record successful call"""
        self.stats.successful_calls += 1
        self.stats.total_calls += 1
        self.stats.consecutive_failures = 0
        
        if self.state == CircuitState.HALF_OPEN:
            # Successful call in half-open state, close the circuit
            self._change_state(CircuitState.CLOSED)
            self._half_open_attempts = 0
            
    def _record_failure(self):
        """Record failed call"""
        self.stats.failed_calls += 1
        self.stats.total_calls += 1
        self.stats.consecutive_failures += 1
        self.stats.last_failure_time = datetime.now()
        
        if self.state == CircuitState.HALF_OPEN:
            # Failed in half-open, reopen the circuit
            self._change_state(CircuitState.OPEN)
            self._half_open_attempts = 0
        elif self.stats.consecutive_failures >= self.failure_threshold:
            # Too many failures, open the circuit
            self._change_state(CircuitState.OPEN)
            
    def _change_state(self, new_state: CircuitState):
        """Change circuit state"""
        if self.state != new_state:
            logger.info(f"Circuit {self.name}: {self.state.value} -> {new_state.value}")
            self.state = new_state
            self.stats.state_changes.append((datetime.now(), new_state))
            
    def _should_attempt_reset(self) -> bool:
        """Check if we should try to reset the circuit"""
        if self.state != CircuitState.OPEN:
            return False
            
        if not self.stats.last_failure_time:
            return True
            
        time_since_failure = (datetime.now() - self.stats.last_failure_time).seconds
        return time_since_failure >= self.recovery_timeout
        
    async def call(self, func: Callable[..., Coroutine[Any, Any, T]], *args, **kwargs) -> T:
        """
        Execute function through circuit breaker
        
        Args:
            func: Async function to call
            *args: Function arguments
            **kwargs: Function keyword arguments
            
        Returns:
            Function result
            
        Raises:
            Exception: If circuit is open or function fails
        """
        # Check if circuit should transition to half-open
        if self._should_attempt_reset():
            self._change_state(CircuitState.HALF_OPEN)
            
        # Reject if circuit is open
        if self.state == CircuitState.OPEN:
            raise Exception(f"Circuit breaker {self.name} is OPEN")
            
        try:
            result = await func(*args, **kwargs)
            self._record_success()
            return result
            
        except self.expected_exception as e:
            self._record_failure()
            raise
            
    def get_stats(self) -> Dict[str, Any]:
        """Get circuit breaker statistics"""
        success_rate = (
            self.stats.successful_calls / self.stats.total_calls * 100
            if self.stats.total_calls > 0 else 0
        )
        
        return {
            'name': self.name,
            'state': self.state.value,
            'total_calls': self.stats.total_calls,
            'success_rate': f"{success_rate:.1f}%",
            'consecutive_failures': self.stats.consecutive_failures
        }


class ExponentialBackoff:
    """
    Exponential backoff with jitter implementation
    PRD Section 1.3 - Exponential backoff with jitter for all retry logic
    """
    
    def __init__(
        self,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True
    ):
        """
        Initialize exponential backoff
        
        Args:
            base_delay: Initial delay in seconds
            max_delay: Maximum delay in seconds
            exponential_base: Base for exponential calculation
            jitter: Add randomization to prevent thundering herd
        """
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter
        
    def calculate_delay(self, attempt: int) -> float:
        """
        Calculate delay for given attempt number
        
        Args:
            attempt: Attempt number (0-based)
            
        Returns:
            Delay in seconds
        """
        # Calculate exponential delay
        delay = min(
            self.base_delay * (self.exponential_base ** attempt),
            self.max_delay
        )
        
        # Add jitter if enabled
        if self.jitter:
            # Random jitter between 0 and half the delay
            jitter_amount = random.uniform(0, delay * 0.5)
            delay = delay - (delay * 0.25) + jitter_amount
            
        return delay
        
    async def wait(self, attempt: int):
        """Wait for the calculated delay"""
        delay = self.calculate_delay(attempt)
        logger.debug(f"Backoff: waiting {delay:.2f}s (attempt {attempt + 1})")
        await asyncio.sleep(delay)


class ConnectionPool:
    """
    Connection pooling for HTTP clients
    PRD Section 1.3 - Reusable HTTPX clients with health monitoring
    """
    
    def __init__(self, max_connections: int = 10, timeout: int = 30):
        """
        Initialize connection pool
        
        Args:
            max_connections: Maximum number of connections
            timeout: Request timeout in seconds
        """
        self.max_connections = max_connections
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None
        self._health_check_interval = 60  # seconds
        self._last_health_check = time.time()
        
    async def get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session with connection pooling"""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                limit=self.max_connections,
                limit_per_host=self.max_connections // 2,
                ttl_dns_cache=300
            )
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=self.timeout
            )
            logger.info(f"Created new connection pool with {self.max_connections} connections")
            
        # Periodic health check
        if time.time() - self._last_health_check > self._health_check_interval:
            await self._health_check()
            
        return self._session
        
    async def _health_check(self):
        """Check connection pool health"""
        self._last_health_check = time.time()
        
        if self._session and not self._session.closed:
            connector = self._session.connector
            if connector:
                logger.debug(f"Connection pool health: {len(connector._conns)} active connections")
                
    async def close(self):
        """Close connection pool"""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.info("Connection pool closed")


class NetworkResilience:
    """
    Unified network resilience layer
    Combines circuit breakers, backoff, connection pooling, and fallback chains
    """
    
    def __init__(self):
        """Initialize network resilience components"""
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}
        self.backoff = ExponentialBackoff()
        self.connection_pool = ConnectionPool()
        
    def get_circuit_breaker(self, name: str) -> CircuitBreaker:
        """Get or create circuit breaker for service"""
        if name not in self.circuit_breakers:
            self.circuit_breakers[name] = CircuitBreaker(name)
        return self.circuit_breakers[name]
        
    async def call_with_resilience(
        self,
        service_name: str,
        primary_func: Callable,
        fallback_func: Optional[Callable] = None,
        max_retries: int = 3,
        *args,
        **kwargs
    ) -> Any:
        """
        Call function with full resilience pattern
        
        Args:
            service_name: Name of the service for circuit breaker
            primary_func: Primary function to call
            fallback_func: Fallback function if primary fails
            max_retries: Maximum number of retries
            *args: Function arguments
            **kwargs: Function keyword arguments
            
        Returns:
            Function result or fallback result
        """
        circuit = self.get_circuit_breaker(service_name)
        
        # Try primary function with retries
        for attempt in range(max_retries):
            try:
                # Use circuit breaker
                result = await circuit.call(primary_func, *args, **kwargs)
                return result
                
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} failed for {service_name}: {e}")
                
                # Don't retry if circuit is open
                if circuit.state == CircuitState.OPEN:
                    break
                    
                # Wait before retry (except on last attempt)
                if attempt < max_retries - 1:
                    await self.backoff.wait(attempt)
                    
        # Primary failed, try fallback
        if fallback_func:
            try:
                logger.info(f"Using fallback for {service_name}")
                return await fallback_func(*args, **kwargs)
            except Exception as e:
                logger.error(f"Fallback also failed for {service_name}: {e}")
                
        # Everything failed
        raise Exception(f"All attempts failed for {service_name}")
        
    async def shutdown(self):
        """Cleanup resources"""
        await self.connection_pool.close()
        
    def get_stats(self) -> Dict[str, Any]:
        """Get resilience layer statistics"""
        return {
            'circuit_breakers': [
                cb.get_stats() for cb in self.circuit_breakers.values()
            ]
        }


# Singleton instance
_resilience = NetworkResilience()


def get_resilience() -> NetworkResilience:
    """Get the singleton NetworkResilience instance"""
    return _resilience