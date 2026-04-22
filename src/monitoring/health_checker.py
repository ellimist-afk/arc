"""
Health Checker for TalkBot
Monitors service health and provides endpoints
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from dataclasses import dataclass
import aiohttp
import asyncpg
import redis.asyncio as redis

logger = logging.getLogger(__name__)


@dataclass
class HealthStatus:
    """Health status for a service"""
    service: str
    healthy: bool
    latency_ms: float
    last_check: datetime
    error: Optional[str] = None
    metadata: Dict[str, Any] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'service': self.service,
            'status': 'healthy' if self.healthy else 'unhealthy',
            'latency_ms': round(self.latency_ms, 2),
            'last_check': self.last_check.isoformat(),
            'error': self.error,
            'metadata': self.metadata or {}
        }


class HealthChecker:
    """
    Performs health checks on all TalkBot services
    Implements PRD monitoring requirements
    """
    
    def __init__(self, config: Dict[str, Any], metrics_collector=None):
        self.config = config
        self.metrics_collector = metrics_collector
        
        # Health check results
        self.health_status: Dict[str, HealthStatus] = {}
        
        # Check intervals
        self.check_interval = 30  # seconds
        self.timeout = 5  # seconds per check
        
        # Service dependencies
        self.critical_services = ['database', 'twitch', 'openai']
        self.optional_services = ['redis', 'voice']
        
        self.running = False
        self.check_task = None
    
    async def start(self):
        """Start health monitoring"""
        self.running = True
        self.check_task = asyncio.create_task(self._health_check_loop())
        logger.info("Health checker started")
    
    async def stop(self):
        """Stop health monitoring"""
        self.running = False
        if self.check_task:
            self.check_task.cancel()
            try:
                await self.check_task
            except asyncio.CancelledError:
                pass
        logger.info("Health checker stopped")
    
    async def _health_check_loop(self):
        """Main health check loop"""
        while self.running:
            try:
                await self.check_all_services()
                await asyncio.sleep(self.check_interval)
            except Exception as e:
                logger.error(f"Health check loop error: {e}")
                await asyncio.sleep(self.check_interval)
    
    async def check_all_services(self) -> Dict[str, HealthStatus]:
        """Check health of all services"""
        tasks = [
            self.check_database(),
            self.check_redis(),
            self.check_twitch(),
            self.check_openai(),
            self.check_voice()
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        for result in results:
            if isinstance(result, HealthStatus):
                self.health_status[result.service] = result
                
                # Update metrics collector if available
                if self.metrics_collector:
                    self.metrics_collector.update_health(result.service, result.healthy)
            elif isinstance(result, Exception):
                logger.error(f"Health check exception: {result}")
        
        return self.health_status
    
    async def check_database(self) -> HealthStatus:
        """Check database connectivity and performance"""
        start_time = datetime.now()
        
        try:
            # Connect to database
            conn = await asyncpg.connect(
                self.config.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5433/streambot'),
                timeout=self.timeout
            )
            
            # Run a simple query
            result = await conn.fetchval("SELECT 1")
            
            # Check table counts for metadata
            message_count = await conn.fetchval("SELECT COUNT(*) FROM messages WHERE timestamp > NOW() - INTERVAL '1 hour'")
            user_count = await conn.fetchval("SELECT COUNT(*) FROM users")
            
            await conn.close()
            
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            
            return HealthStatus(
                service='database',
                healthy=True,
                latency_ms=latency_ms,
                last_check=datetime.now(),
                metadata={
                    'recent_messages': message_count,
                    'total_users': user_count
                }
            )
            
        except Exception as e:
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            return HealthStatus(
                service='database',
                healthy=False,
                latency_ms=latency_ms,
                last_check=datetime.now(),
                error=str(e)
            )
    
    async def check_redis(self) -> HealthStatus:
        """Check Redis connectivity"""
        start_time = datetime.now()
        
        try:
            # Connect to Redis
            r = redis.from_url(
                self.config.get('REDIS_URL', 'redis://localhost:6379'),
                decode_responses=True
            )
            
            # Ping Redis
            await r.ping()
            
            # Get some stats
            info = await r.info()
            used_memory_mb = info.get('used_memory', 0) / 1024 / 1024
            connected_clients = info.get('connected_clients', 0)
            
            await r.close()
            
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            
            return HealthStatus(
                service='redis',
                healthy=True,
                latency_ms=latency_ms,
                last_check=datetime.now(),
                metadata={
                    'memory_mb': round(used_memory_mb, 2),
                    'connected_clients': connected_clients
                }
            )
            
        except Exception as e:
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            return HealthStatus(
                service='redis',
                healthy=False,
                latency_ms=latency_ms,
                last_check=datetime.now(),
                error=str(e)
            )
    
    async def check_twitch(self) -> HealthStatus:
        """Check Twitch API connectivity"""
        start_time = datetime.now()
        
        try:
            # Check Twitch API
            headers = {
                'Client-ID': self.config.get('TWITCH_CLIENT_ID', ''),
                'Authorization': f"Bearer {self.config.get('TWITCH_ACCESS_TOKEN', '')}"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    'https://api.twitch.tv/helix/users',
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        latency_ms = (datetime.now() - start_time).total_seconds() * 1000
                        
                        return HealthStatus(
                            service='twitch',
                            healthy=True,
                            latency_ms=latency_ms,
                            last_check=datetime.now(),
                            metadata={'api_status': response.status}
                        )
                    else:
                        raise Exception(f"Twitch API returned {response.status}")
                        
        except Exception as e:
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            return HealthStatus(
                service='twitch',
                healthy=False,
                latency_ms=latency_ms,
                last_check=datetime.now(),
                error=str(e)
            )
    
    async def check_openai(self) -> HealthStatus:
        """Check OpenAI API connectivity"""
        start_time = datetime.now()
        
        try:
            # Simple API check
            headers = {
                'Authorization': f"Bearer {self.config.get('OPENAI_API_KEY', '')}"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    'https://api.openai.com/v1/models',
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as response:
                    if response.status == 200:
                        latency_ms = (datetime.now() - start_time).total_seconds() * 1000
                        
                        return HealthStatus(
                            service='openai',
                            healthy=True,
                            latency_ms=latency_ms,
                            last_check=datetime.now(),
                            metadata={'api_status': response.status}
                        )
                    else:
                        raise Exception(f"OpenAI API returned {response.status}")
                        
        except Exception as e:
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            return HealthStatus(
                service='openai',
                healthy=False,
                latency_ms=latency_ms,
                last_check=datetime.now(),
                error=str(e)
            )
    
    async def check_voice(self) -> HealthStatus:
        """Check voice recognition status"""
        start_time = datetime.now()
        
        try:
            # Check if voice service is registered and active
            # This would check the actual voice recognition service
            # For now, return a placeholder
            
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            
            # Check if voice is enabled in config
            voice_enabled = (
                self.config.get('VOICE_INPUT_ENABLED', False) or 
                self.config.get('VOICE_ENABLED', False)
            )
            
            return HealthStatus(
                service='voice',
                healthy=voice_enabled,
                latency_ms=latency_ms,
                last_check=datetime.now(),
                metadata={'enabled': voice_enabled}
            )
            
        except Exception as e:
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            return HealthStatus(
                service='voice',
                healthy=False,
                latency_ms=latency_ms,
                last_check=datetime.now(),
                error=str(e)
            )
    
    def get_overall_health(self) -> Dict[str, Any]:
        """Get overall system health"""
        all_healthy = all(
            status.healthy for service, status in self.health_status.items()
            if service in self.critical_services
        )
        
        # Calculate average latency
        latencies = [s.latency_ms for s in self.health_status.values() if s.latency_ms > 0]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0
        
        return {
            'status': 'healthy' if all_healthy else 'unhealthy',
            'timestamp': datetime.now().isoformat(),
            'services': {
                name: status.to_dict() 
                for name, status in self.health_status.items()
            },
            'metrics': {
                'average_latency_ms': round(avg_latency, 2),
                'healthy_services': sum(1 for s in self.health_status.values() if s.healthy),
                'total_services': len(self.health_status)
            }
        }
    
    async def get_readiness(self) -> Dict[str, Any]:
        """Check if the bot is ready to handle requests"""
        # Check critical services only
        critical_healthy = all(
            self.health_status.get(service, HealthStatus(service, False, 0, datetime.now())).healthy
            for service in self.critical_services
        )
        
        return {
            'ready': critical_healthy,
            'timestamp': datetime.now().isoformat(),
            'checks': {
                service: self.health_status.get(service, HealthStatus(service, False, 0, datetime.now())).healthy
                for service in self.critical_services
            }
        }
    
    async def get_liveness(self) -> Dict[str, Any]:
        """Simple liveness check"""
        return {
            'alive': True,
            'timestamp': datetime.now().isoformat(),
            'uptime': str(datetime.now() - (self.health_status.get('database', HealthStatus('database', False, 0, datetime.now())).last_check - timedelta(seconds=30)))
        }


# Singleton instance
_health_checker: Optional[HealthChecker] = None


def get_health_checker() -> HealthChecker:
    """Get the singleton health checker"""
    global _health_checker
    if _health_checker is None:
        _health_checker = HealthChecker()
    return _health_checker


async def initialize_health_monitoring() -> None:
    """Initialize health monitoring"""
    checker = get_health_checker()
    await checker.start_monitoring()


async def shutdown_health_monitoring() -> None:
    """Shutdown health monitoring"""
    checker = get_health_checker()
    await checker.stop_monitoring()