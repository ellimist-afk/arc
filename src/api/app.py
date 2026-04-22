# src/api/app.py
"""
Main FastAPI application for TalkBot.
Provides REST API endpoints and WebSocket connections for bot management.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Configure enhanced structured logging
from src.core.logging_config import configure_logging
# from src.utils.structured_logging import setup_structured_logging
from src.monitoring.health_checker import initialize_health_monitoring, shutdown_health_monitoring
from src.monitoring.metrics_collector import get_metrics_collector
import os

# Setup minimal logging based on environment settings
log_level = os.getenv("LOG_LEVEL", "WARNING")
configure_logging(level=log_level, json_output=None)

# Setup structured logging if enabled
# if os.getenv("STRUCTURED_LOGGING", "false").lower() == "true":
#     setup_structured_logging(log_level, json_format=True)

# Apply minimal logging configuration if available
try:
    from startup_config import configure_minimal_logging
    configure_minimal_logging()
except ImportError:
    pass

from src.api.websocket_handler import get_websocket_hub
from src.bot import get_registry
# from src.core.background_tasks import get_task_manager
from src.core.config_unified import get_settings
# from src.core.connection_pool import (
#     pre_warm_all_pools,
#     shutdown_all_pools,
# )
# from src.core.memory_cleanup import start_memory_manager, stop_memory_manager
# from src.core.security_monitor import get_security_monitor
from src.core.self_healing import start_self_healing, stop_self_healing
from src.core.shutdown_manager import ShutdownPriority, get_shutdown_manager
from src.services import get_cache_service, get_llm_service
from src.services.cache_cleanup import (
    CleanupStrategy,
    start_cache_cleanup,
    stop_cache_cleanup,
)
from src.services.service_registry import ServiceRegistry
from src.services.registry_migration import (
    get_migration_controller,
    get_registry_mode,
    MigrationController
)
from src.utils.task_registry import TaskRegistry
from src.core.backend_optimization_init import initialize_backend_optimizations, shutdown_backend_optimizations

# Import shutdown helper
try:
    from src.utils.shutdown_helper import set_shutdown_flag
except ImportError:

    def set_shutdown_flag():
        pass


# Import V2 endpoints that actually exist
from .v2.endpoints.health import router as health_router
from .v2.endpoints.bot import router as bot_router  
from .v2.endpoints.settings import router as settings_router
from .v2.endpoints.memory import router as memory_router
from .v2.endpoints.audio import router as audio_router
from .v2.endpoints.analytics import router as analytics_router
from .v2.endpoints.personality import router as personality_router
from .middleware.csp import CSPMiddleware, SecurityHeadersMiddleware
from .middleware.error_handler import ErrorHandlerMiddleware
from .middleware.rate_limiter import EnhancedRateLimitMiddleware, RateLimitConfig
from .middleware.response_optimization import ResponseOptimizationMiddleware
from .monitoring import get_api_monitor
from .prometheus_metrics import metrics_endpoint
from .utils.error_handling import create_error_response
from .utils.logging_cleanup import api_logger
from .v2 import v2_router
# monitoring endpoint was consolidated/removed
# from src.api.v2.endpoints.monitoring import router as enhanced_monitoring_router
from src.core.jwt_auth import TokenData, get_current_user

log = logging.getLogger(__name__)
settings = get_settings()


async def auto_start_bot_background(channel: str):
    """Auto-start bot in background to avoid blocking server startup."""
    try:
        # Give the server a moment to finish startup
        await asyncio.sleep(1)

        log.info(f"[BACKGROUND] Starting auto-start bot for channel: {channel}")
        registry = get_registry()
        await registry.get_or_create(
            streamer_id=channel,
            channel=channel,
        )
        log.info(f"[BACKGROUND] Auto-start bot created successfully for {channel}")
    except Exception as e:
        log.error(f"[BACKGROUND] Auto-start failed for {channel}: {e}")
        # Don't crash the server if auto-start fails
        import traceback
        log.debug(f"Auto-start traceback: {traceback.format_exc()}")


async def register_shutdown_components():
    """Register all TalkBot components with the shutdown manager."""
    shutdown_manager = get_shutdown_manager()

    # Critical: Stop accepting new requests first
    shutdown_manager.register(
        name="WebSocket Hub",
        shutdown_func=shutdown_websocket_hub,
        priority=ShutdownPriority.CRITICAL,
        timeout=5.0,
    )

    # High: Close external connections
    shutdown_manager.register(
        name="Bot Registry",
        shutdown_func=shutdown_bot_registry,
        priority=ShutdownPriority.HIGH,
        timeout=10.0,
    )

    shutdown_manager.register(
        name="API Monitor",
        shutdown_func=shutdown_api_monitor,
        priority=ShutdownPriority.HIGH,
        timeout=5.0,
    )

    shutdown_manager.register(
        name="Security Monitor",
        # shutdown_func=shutdown_security_monitor,
        priority=ShutdownPriority.HIGH,
        timeout=5.0,
    )
    
    shutdown_manager.register(
        name="Health Monitoring",
        shutdown_func=shutdown_health_monitoring,
        priority=ShutdownPriority.NORMAL,
        timeout=2.0,
    )
    
    # TEMPORARILY DISABLED - Backend optimizations causing startup hang
    # shutdown_manager.register(
    #     name="Backend Optimizations",
    #     shutdown_func=shutdown_backend_optimizations,
    #     priority=ShutdownPriority.HIGH,
    #     timeout=10.0,
    # )

    # Normal: Stop processing systems
    shutdown_manager.register(
        name="Self Healing System",
        shutdown_func=shutdown_self_healing_system,
        priority=ShutdownPriority.NORMAL,
        timeout=2.0,  # Reduced for faster shutdown
    )

    shutdown_manager.register(
        name="Memory Cleanup Manager",
        # shutdown_func=shutdown_memory_cleanup_manager,
        priority=ShutdownPriority.NORMAL,
        timeout=1.0,  # Reduced for faster shutdown
    )

    # Low: Close connections and cleanup
    shutdown_manager.register(
        name="ServiceRegistry",
        shutdown_func=shutdown_service_registry,
        priority=ShutdownPriority.LOW,
        timeout=3.0,
    )
    
    shutdown_manager.register(
        name="Cache Cleanup Service",
        shutdown_func=shutdown_cache_cleanup_service,
        priority=ShutdownPriority.LOW,
        timeout=1.0,  # Reduced for faster shutdown
    )

    shutdown_manager.register(
        name="Cache Service",
        shutdown_func=shutdown_cache_service,
        priority=ShutdownPriority.LOW,
        timeout=1.0,  # Reduced for faster shutdown
    )
    
    shutdown_manager.register(
        name="Cache Limit System",
        shutdown_func=shutdown_cache_limit_system,
        priority=ShutdownPriority.LOW,
        timeout=2.0,
    )

    shutdown_manager.register(
        name="Background Task Manager",
        shutdown_func=shutdown_background_task_manager,
        priority=ShutdownPriority.NORMAL,
        timeout=2.0,  # Reduced for faster shutdown
    )

    shutdown_manager.register(
        name="Connection Pools",
        # shutdown_func=shutdown_connection_pools,
        priority=ShutdownPriority.LOW,
        timeout=2.0,  # Reduced for faster shutdown
    )

    # Only log in verbose mode
    if os.getenv("VERBOSE_STARTUP", "false").lower() == "true":
        log.info("[ShutdownManager] Registered 10 shutdown components")


# Shutdown component functions
async def shutdown_websocket_hub():
    """Shutdown WebSocket hub."""
    try:
        websocket_hub = get_websocket_hub()
        await websocket_hub.shutdown()
    except Exception as e:
        log.error(f"WebSocket hub shutdown error: {e}")


async def shutdown_bot_registry():
    """Shutdown bot registry."""
    try:
        registry = get_registry()
        await registry.stop_all()
    except Exception as e:
        log.error(f"Bot registry shutdown error: {e}")


async def shutdown_api_monitor():
    """Shutdown API monitor."""
    try:
        monitor = get_api_monitor()
        await monitor.stop()
    except Exception as e:
        log.error(f"API monitor shutdown error: {e}")


# async def shutdown_security_monitor():
#     """Shutdown security monitor."""
#     try:
#         security_monitor = get_security_monitor()
#         await security_monitor.stop()
#     except Exception as e:
#         log.error(f"Security monitor shutdown error: {e}")


async def shutdown_self_healing_system():
    """Shutdown self-healing system."""
    try:
        await stop_self_healing()
    except Exception as e:
        log.error(f"Self-healing system shutdown error: {e}")


# async def shutdown_memory_cleanup_manager():
#     """Shutdown memory cleanup manager."""
#     try:
#         await stop_memory_manager()
#     except Exception as e:
#         log.error(f"Memory cleanup manager shutdown error: {e}")


async def shutdown_cache_cleanup_service():
    """Shutdown cache cleanup service."""
    try:
        await stop_cache_cleanup()
    except Exception as e:
        log.error(f"Cache cleanup service shutdown error: {e}")


async def shutdown_cache_service():
    """Shutdown cache service."""
    try:
        cache_service = get_cache_service()
        if cache_service:
            await cache_service.close()
    except Exception as e:
        log.error(f"Cache service shutdown error: {e}")


async def shutdown_service_registry():
    """Shutdown ServiceRegistry and all registered services."""
    try:
        # Check if using migration controller
        registry_mode = get_registry_mode()
        if registry_mode != "old":
            # Use migration controller for shutdown
            migration_controller = get_migration_controller()
            await migration_controller.shutdown_all()
        else:
            # Use old registry directly
            service_registry = ServiceRegistry.get_instance()
            await service_registry.shutdown_all()
    except Exception as e:
        log.error(f"ServiceRegistry shutdown error: {e}")


async def shutdown_cache_limit_system():
    """Shutdown cache limit system."""
    try:
        from src.core.cache_initialization import shutdown_cache_system
        await shutdown_cache_system()
    except Exception as e:
        log.error(f"Cache limit system shutdown error: {e}")


async def shutdown_background_task_manager():
    """Shutdown background task manager."""
    try:
        task_manager = get_task_manager()
        await task_manager.stop()
    except Exception as e:
        log.error(f"Background task manager shutdown error: {e}")


# async def shutdown_connection_pools():
#     """Shutdown connection pools and network connections."""
#     try:
#         # Shutdown HTTP connection pools
#         await shutdown_all_pools()
#
#         # Cleanup network resilience connections
#         from src.core.network_resilience import cleanup_network_connections
#         await cleanup_network_connections()
#
#         # Cleanup HTTP client manager
#         from src.core.http_client import cleanup_http_clients
#         await cleanup_http_clients()
#
#     except Exception as e:
        log.error(f"Connection pools shutdown error: {e}")


async def _register_all_services(service_registry: ServiceRegistry) -> None:
    """Register all services with the service registry."""
    try:
        # Import service classes
        from src.services.stream_service import StreamService
        from src.services.analytics_service import AnalyticsService
        from src.services.audio_service import AudioService
        from src.services.chat_service import ChatService
        from src.services.memory_service import MemoryService
        from src.services.personality_service import PersonalityService
        from src.services.config_service import ConfigService
        from src.services.llm_service import LLMService
        from src.services.health_service import HealthService
        from src.services.metrics_service import MetricsService
        
        # Register services with dependencies  
        # Note: ServiceRegistry will pass the service name as the first argument to constructors
        service_registry.register("config", ConfigService, [])
        service_registry.register("stream", StreamService, [])
        service_registry.register("analytics", AnalyticsService, [])
        service_registry.register("audio", AudioService, [])
        service_registry.register("memory", MemoryService, [])
        service_registry.register("personality", PersonalityService, [])
        service_registry.register("health", HealthService, [])
        service_registry.register("metrics", MetricsService, [])
        
        # Chat service depends on stream service for Twitch integration
        service_registry.register("chat", ChatService, ["stream"])
        
        # LLM service depends on config for API keys
        service_registry.register("llm", LLMService, ["config"])
        
        log.info("Successfully registered 10 services with registry (including health and metrics)")
        
    except Exception as e:
        log.error(f"Failed to register services: {e}")
        # Continue without registry - services can still be used directly


# Application lifecycle - RESTORED FULL VERSION
@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI application lifespan manager."""
    log.info("===== LIFESPAN CONTEXT MANAGER ENTERED =====")
    
    # Create TaskRegistry for API server tasks
    task_registry = TaskRegistry(name="api_server")
    
    # Helper for conditional logging
    verbose_mode = os.getenv("VERBOSE_STARTUP", "false").lower() == "true"
    log.info(f"VERBOSE_STARTUP mode: {verbose_mode}")
    def startup_log(message, always=False):
        if always or verbose_mode:
            log.info(message)
    
    # Minimal startup message
    if verbose_mode:
        log.info("=" * 60)
        log.info("[STARTUP] TalkBot API starting up...")
        log.info("=" * 60)
    else:
        log.info("TalkBot starting...")

    # Initialize services
    try:
        # CRITICAL: Initialize database tables first
        # This ensures all required tables exist before any service tries to use them
        from src.core.database.manager import db_manager
        startup_log("[STARTUP] Initializing database...")
        await db_manager.initialize()
        startup_log("[STARTUP] Creating database tables...")
        await db_manager.create_tables()
        startup_log("[STARTUP] Database initialization complete")
        
        # Initialize ServiceRegistry based on migration mode
        registry_mode = get_registry_mode()
        startup_log(f"[STARTUP] Registry mode: {registry_mode}")
        
        if registry_mode != "old":
            # Use migration controller
            migration_controller = get_migration_controller()
            
            # Register services with both registries if needed
            if migration_controller.old_registry:
                await _register_all_services(migration_controller.old_registry)
            if migration_controller.new_registry:
                # New registry uses different registration method
                from src.services.simplified_registry_setup import register_all_services
                await register_all_services(migration_controller.new_registry)
            
            # Use the migration controller as service_registry for compatibility
            service_registry = migration_controller
        else:
            # Use old registry directly
            service_registry = ServiceRegistry.get_instance()
            startup_log("[STARTUP] ServiceRegistry initialized (legacy mode)")
            
            # Register all services with the registry
            await _register_all_services(service_registry)
        startup_log("[STARTUP] All services registered with registry")
        
        # Initialize all registered services (defer background tasks)
        startup_log("[STARTUP] Initializing all services (deferred mode)...")
        if isinstance(service_registry, MigrationController):
            await service_registry.initialize_all()
        else:
            await service_registry.initialize_all(defer_background_tasks=True)
        startup_log("[STARTUP] All services initialized successfully (background tasks deferred)")
        
        # TEMPORARILY DISABLED - debugging startup hang
        # # Initialize core services
        # llm_service = await get_llm_service()
        # cache_service = get_cache_service()

        # # Start automatic cache cleanup
        # await start_cache_cleanup(cache_service, CleanupStrategy.HYBRID)
        # startup_log("[STARTUP] Cache cleanup service started")

        # # Initialize monitoring
        # monitor = get_api_monitor()
        # await monitor.start()

        # # Initialize security monitoring
        # security_monitor = get_security_monitor()
        # await security_monitor.start()
        
        startup_log("[STARTUP] Skipped additional service initialization (debugging hang)")
        
        # Initialize enhanced health monitoring
        # await initialize_health_monitoring()  # Temporarily disabled due to task registry issue
        startup_log("[STARTUP] Health monitoring skipped (temporarily disabled)")
        
        # Initialize metrics collector
        metrics_collector = get_metrics_collector()
        startup_log("[STARTUP] Metrics collection initialized")

        # Start WebSocket health checks (non-blocking)
        websocket_hub = get_websocket_hub()
        try:
            await asyncio.wait_for(websocket_hub.start_health_checks(), timeout=5.0)
            startup_log("[STARTUP] WebSocket health checks started")
        except asyncio.TimeoutError:
            startup_log("[STARTUP] WebSocket health check startup timed out, continuing...")
        except Exception as e:
            startup_log(f"[STARTUP] WebSocket health check startup failed: {e}")

        # TEMPORARILY DISABLED - debugging server startup hang
        # # Start self-healing monitoring
        # await start_self_healing()
        # startup_log("[STARTUP] Self-healing system started")

        # # Start memory cleanup manager
        # await start_memory_manager()
        # startup_log("[STARTUP] Memory cleanup manager started")

        # # Initialize connection pools and pre-warm
        # await pre_warm_all_pools()
        # startup_log("[STARTUP] Connection pools pre-warmed")

        # # Initialize background task manager
        # task_manager = get_task_manager()
        # await task_manager.start()
        # startup_log("[STARTUP] Background task manager started")
        
        startup_log("[STARTUP] Skipped additional initialization (debugging hang)")

        # Initialize backend architecture optimizations (non-blocking)
        # TEMPORARILY DISABLED - causing startup hang
        # startup_log("[STARTUP] Starting backend optimizations...")
        # try:
        #     await asyncio.wait_for(initialize_backend_optimizations(memory_manager=None), timeout=3.0)
        #     startup_log("[STARTUP] Backend optimizations initialized")
        # except asyncio.TimeoutError:
        #     startup_log("[STARTUP] Backend optimizations timed out, continuing...")
        # except Exception as e:
        #     startup_log(f"[STARTUP] Backend optimizations failed: {e}")
        startup_log("[STARTUP] Backend optimizations disabled (causing hang)")
        
        # Initialize shutdown manager and register core components
        shutdown_manager = get_shutdown_manager()
        shutdown_manager.install_signal_handlers()
        await register_shutdown_components()
        startup_log("[STARTUP] Shutdown manager initialized")

        startup_log("[STARTUP] Core services initialized")

        # Schedule auto-start bot in background (TEMPORARILY DISABLED)
        if False:  # Temporarily disabled: settings.AUTO_START_CHANNEL:
            startup_log(
                f"[STARTUP] Scheduling background auto-start for channel: {settings.AUTO_START_CHANNEL}",
                always=True  # Always show auto-start channel
            )
            await task_registry.create_task(
                auto_start_bot_background(settings.AUTO_START_CHANNEL),
                name="auto_start_bot"
            )

        # Schedule background task startup after server starts
        async def start_service_background_tasks():
            """Start service background tasks after server is running."""
            await asyncio.sleep(2)  # Give server time to start
            log.info("[BACKGROUND] Starting service background tasks...")
            try:
                await service_registry.start_background_tasks()
                log.info("[BACKGROUND] Service background tasks started successfully")
            except Exception as e:
                log.error(f"[BACKGROUND] Failed to start service background tasks: {e}")
        
        # Schedule the background task
        task_registry.create_task(
            start_service_background_tasks(),
            name="start_service_background_tasks"
        )
        
        log.info("TalkBot API ready")  # Always show completion
        log.info("===== ABOUT TO YIELD FROM LIFESPAN =====")
        
        yield  # Application runs here
        
        log.info("===== LIFESPAN RESUMING AFTER YIELD =====")

    except Exception as e:
        log.error(f"[STARTUP] Startup failed: {e}")
        raise
    finally:
        # Use the shutdown manager for coordinated shutdown
        log.info("[SHUTDOWN] TalkBot API shutting down...")

        # Set global shutdown flag first to notify all components
        try:
            set_shutdown_flag()
        except Exception as e:
            log.error(f"[SHUTDOWN] Failed to set shutdown flag: {e}")

        # Shutdown backend optimizations first - TEMPORARILY DISABLED
        try:
            # await shutdown_backend_optimizations()
            log.info("[SHUTDOWN] Backend optimizations shutdown skipped")
        except Exception as e:
            log.error(f"[SHUTDOWN] Backend optimization shutdown error: {e}")
        
        # Execute graceful shutdown using shutdown manager
        try:
            shutdown_manager = get_shutdown_manager()
            await shutdown_manager.shutdown(timeout=30.0, reason="application_exit")
        except Exception as e:
            log.error(f"[SHUTDOWN] Shutdown manager error: {e}")
            # Fallback to force shutdown if graceful fails
            try:
                shutdown_manager = get_shutdown_manager()
                await shutdown_manager.shutdown(timeout=5.0, reason="fallback_force")
            except Exception as fallback_error:
                log.error(f"[SHUTDOWN] Fallback shutdown failed: {fallback_error}")

        # Final task cleanup as last resort
        try:
            current_task = asyncio.current_task()
            all_tasks = [
                task
                for task in asyncio.all_tasks()
                if task != current_task and not task.done()
            ]

            if all_tasks:
                log.warning(f"[SHUTDOWN] Cancelling {len(all_tasks)} remaining tasks...")
                for task in all_tasks:
                    if not task.done():
                        task.cancel()

                # Cancel TaskRegistry tasks first
                try:
                    await task_registry.cancel_all()
                except Exception as e:
                    log.error(f"[SHUTDOWN] TaskRegistry cleanup error: {e}")

                # Then handle remaining tasks
                try:
                    await asyncio.wait_for(
                        task_registry.gather(*all_tasks, return_exceptions=True, name_prefix="shutdown_cleanup"), 
                        timeout=3.0
                    )
                except asyncio.TimeoutError:
                    log.warning("[SHUTDOWN] Some tasks did not cancel within timeout")

        except Exception as e:
            log.error(f"[SHUTDOWN] Final cleanup error: {e}")

        log.info("[SHUTDOWN] TalkBot API shutdown complete")


# Create application
def create_app() -> FastAPI:
    """Create and configure FastAPI application with security hardening."""
    app = FastAPI(
        title="TalkBot API",
        description="Production-ready AI Co-Host API for Twitch Streaming",
        version="2.0.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.DEBUG else None,  # Disable docs in production
        redoc_url="/redoc" if settings.DEBUG else None,  # Disable redoc in production
        openapi_url=(
            "/openapi.json" if settings.DEBUG else None
        ),  # Disable OpenAPI in production
        openapi_tags=[
            {
                "name": "Health & Monitoring",
                "description": "System health and monitoring endpoints",
            },
            {
                "name": "Bot Management",
                "description": "Bot lifecycle and control operations",
            },
            {
                "name": "Configuration",
                "description": "Settings and configuration management",
            },
            {
                "name": "Analytics & Metrics",
                "description": "Performance metrics and analytics",
            },
            {"name": "Event Management", "description": "Event simulation and history"},
            {"name": "Memory System", "description": "Memory and context management"},
            {
                "name": "Personality System",
                "description": "AI personality configuration",
            },
            {
                "name": "Authentication",
                "description": "User authentication and authorization",
            },
            {
                "name": "Legacy",
                "description": "Legacy endpoints for backward compatibility",
            },
        ],
        servers=[
            {"url": "/api/v2", "description": "API Version 2 (Current)"},
            {"url": "/api", "description": "Legacy API (Deprecated)"},
        ],
    )

    # Route registration is working - removing duplicate early routes

    # Add improved validation error handler
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ):
        """Handle Pydantic validation errors with user-friendly messages."""
        api_logger.validation_error(
            "request_data", exc.errors(), "Invalid request format"
        )

        # Generate user-friendly error message
        field_errors = []
        for error in exc.errors():
            field = ".".join(str(loc) for loc in error["loc"])
            message = error["msg"]
            if len(field_errors) >= 1000:  # Prevent unbounded growth
                field_errors.pop(0)
            field_errors.append(f"{field}: {message}")

        from .utils.error_handling import ValidationError

        validation_error = ValidationError(
            "request",
            f"Please check: {'; '.join(field_errors[:3])}"
            + ("..." if len(field_errors) > 3 else ""),
        )
        validation_error.details = {"field_errors": field_errors}

        return create_error_response(
            validation_error, request_id=getattr(request.state, "request_id", None)
        )

    # Add general exception handler for unhandled errors
    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        """Handle all unhandled exceptions with proper logging and user-friendly responses."""
        api_logger.request_error(request.method, str(request.url.path), exc)

        # Don't expose internal error details in production
        include_details = getattr(settings, "DEBUG", False)

        return create_error_response(
            exc,
            request_id=getattr(request.state, "request_id", None),
            include_details=include_details,
        )

    # Create rate limit configuration with specific limits for critical endpoints
    rate_limit_config = RateLimitConfig(
        requests_per_second=getattr(settings, "RATE_LIMIT_PER_SECOND", 10.0),
        burst_size=getattr(settings, "RATE_LIMIT_BURST", 20),
        authenticated_multiplier=2.0,
        premium_multiplier=5.0,
        endpoint_limits={
            "/api/auth/login": RateLimitConfig(
                requests_per_second=1.0,  # Strict limit for login attempts
                burst_size=3,
                window_size=300,  # 5 minute window
            ),
            "/api/auth/register": RateLimitConfig(
                requests_per_second=0.1,  # Very strict for registration
                burst_size=1,
                window_size=3600,  # 1 hour window
            ),
            "/api/bot/*/start": RateLimitConfig(
                requests_per_second=0.5,  # Limit bot creation
                burst_size=2,
                window_size=60,
            ),
            "/ws/*": RateLimitConfig(
                requests_per_second=1.0,  # Limit WebSocket connections
                burst_size=5,
                window_size=60,
            ),
        },
    )

    # Correlation ID middleware
    @app.middleware("http")
    async def correlation_id_middleware(request: Request, call_next):
        """Add correlation ID to all requests for distributed tracing."""
        import time

        from src.core.correlation import CorrelationManager

        # Get or generate correlation ID
        corr_id = request.headers.get("X-Correlation-ID")
        if not corr_id:
            corr_id = f"req-{CorrelationManager.generate_id()}"

        # Set in context
        CorrelationManager.set_correlation_id(corr_id)

        # Log request start (only in debug/verbose mode)
        start_time = time.time()
        if settings.DEBUG or os.getenv("VERBOSE_STARTUP", "false").lower() == "true":
            log.info(
                f"Request started: {request.method} {request.url.path}",
                extra={
                    "correlation_id": corr_id,
                    "method": request.method,
                    "path": request.url.path,
                    "client": request.client.host if request.client else None,
                },
            )

        try:
            # Process request
            response = await call_next(request)

            # Add correlation ID to response
            response.headers["X-Correlation-ID"] = corr_id

            # Log request completion (only in debug/verbose mode or for slow requests)
            duration_ms = (time.time() - start_time) * 1000
            if settings.DEBUG or os.getenv("VERBOSE_STARTUP", "false").lower() == "true" or duration_ms > 1000:
                log.info(
                    f"Request completed: {response.status_code} ({duration_ms:.0f}ms)",
                    extra={
                        "correlation_id": corr_id,
                        "status_code": response.status_code,
                        "duration_ms": duration_ms,
                    },
                )

            return response
        except Exception as e:
            # Log error
            log.error(
                f"Request failed: {str(e)}",
                exc_info=True,
                extra={"correlation_id": corr_id, "error_type": type(e).__name__},
            )
            raise
        finally:
            # Clear correlation ID from context
            CorrelationManager.clear_correlation_id()

    # Security headers are now handled by SecurityHeadersMiddleware

    # Add enhanced middleware (order matters - error handler first, then security, response optimization, then rate limiting)
    app.add_middleware(ErrorHandlerMiddleware, debug=settings.DEBUG)
    app.add_middleware(CSPMiddleware, debug_mode=settings.DEBUG)
    app.add_middleware(SecurityHeadersMiddleware)
    
    # Add response optimization middleware
    app.add_middleware(ResponseOptimizationMiddleware, config={
        "enable_compression": True,
        "compression_threshold": 1024,
        "max_cache_age": 3600,
        "cacheable_paths": {
            "/api/v2/analytics/": 300,
            "/api/v2/monitoring/": 60,
            "/api/health": 30,
            "/metrics": 60,
            "/static/": 86400,
        }
    })

    # Add authentication middleware to protect sensitive endpoints
    # Enable authentication middleware for security
    from src.api.middleware.auth import AuthenticationMiddleware
    app.add_middleware(AuthenticationMiddleware)

    # Add rate limiting middleware and store reference for monitoring
    rate_limiter = EnhancedRateLimitMiddleware(app, config=rate_limit_config)
    app.add_middleware(EnhancedRateLimitMiddleware, config=rate_limit_config)

    # Set rate limiter reference for monitoring endpoints - COMMENTED OUT (non-existent import)
    # from .endpoints.rate_limit_monitor import set_rate_limiter
    # set_rate_limiter(rate_limiter)

    # Add CORS middleware with restricted configuration for production security
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,  # Should be specific domains in production
        allow_credentials=True,
        allow_methods=[
            "GET",
            "POST",
            "PUT",
            "DELETE",
            "OPTIONS",
        ],  # Only allow necessary methods
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Requested-With",
            "Accept",
            "Origin",
            "Access-Control-Request-Method",
            "Access-Control-Request-Headers",
        ],  # Only allow necessary headers
        expose_headers=["Content-Length", "X-Total-Count"],  # Limit exposed headers
        max_age=3600,  # Cache preflight requests for 1 hour
    )

    # Mount V2 API (the only working API endpoints)
    app.include_router(v2_router, prefix="/api")  # V2 API with enhanced OpenAPI docs
    
    # COMMENTED OUT - These routers don't exist, were causing import errors
    # app.include_router(performance_monitor_router)
    # app.include_router(advanced_performance_router)
    # app.include_router(bot_management_router, prefix="/api", tags=["Bot Management v1"])
    # app.include_router(monitoring_router, tags=["Monitoring"])
    # app.include_router(monitoring_dashboard_router, tags=["Monitoring Dashboard"])
    # app.include_router(memory_stats_router, tags=["Memory Stats"])
    # app.include_router(self_healing_router, prefix="/api", tags=["Self-Healing"])
    # app.include_router(channel_points_router, tags=["Channel Points"])
    # app.include_router(metrics_router, tags=["Metrics"])
    # app.include_router(rate_limit_monitor_router, tags=["Rate Limiting"])
    # app.include_router(shutdown_monitor_router, tags=["Shutdown Management"])
    # app.include_router(cache_monitor_router, tags=["Cache Management"])
    # app.include_router(background_tasks_monitor_router, tags=["Background Tasks"])
    # app.include_router(connection_pool_monitor_router, tags=["Connection Pools"])

    # ============================================================================
    # 🚨 CRITICAL: STATIC FILES MOUNTING - THIS HAS BEEN BROKEN MULTIPLE TIMES
    # ============================================================================
    # PROBLEM: Dashboard shows pink rectangles, /static/js/app.js returns 404
    # CAUSE: Relative paths break when working directory changes
    # SOLUTION: Use absolute path with os.path.join
    #
    # ❌ WRONG: app.mount("/static", StaticFiles(directory="static"), name="static")
    # ❌ WRONG: app.mount("/static", StaticFiles(directory="src/api/static"), name="static")
    # ✅ RIGHT: Use absolute path as shown below
    #
    # VERIFICATION: curl -I http://localhost:8000/static/js/app.js should return 200 OK
    # ============================================================================
    import os
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # Initialize templates with absolute path
    import os
    templates_dir = os.path.join(os.path.dirname(__file__), "templates")
    templates = Jinja2Templates(directory=templates_dir)

    # Add CSP helper functions to template globals
    from .middleware.csp import get_csp_nonce

    templates.env.globals["get_csp_nonce"] = get_csp_nonce

    # Initialize cache system at startup
    @app.on_event("startup")
    async def initialize_cache_system():
        """Initialize cache system with limits and monitoring."""
        try:
            from src.core.cache_initialization import initialize_cache_system
            
            startup_log("🔄 Initializing cache system with limits...", always=True)
            
            results = await initialize_cache_system()
            successful_caches = sum(1 for success in results.values() if success)
            total_caches = len(results)
            
            if successful_caches > 0:
                startup_log(f"Cache system initialized: {successful_caches}/{total_caches} caches active", always=True)
            else:
                startup_log("WARNING: Cache system initialization failed, continuing without caching", always=True)
            
        except Exception as e:
            log.error(f"Failed to initialize cache system: {e}")
            startup_log("WARNING: Cache system initialization failed, continuing without caching", always=True)

    # Debug: Log all registered routes at startup (only in verbose mode)
    @app.on_event("startup")
    async def debug_routes():
        """Log all registered routes for debugging."""
        if settings.DEBUG or os.getenv("VERBOSE_STARTUP", "false").lower() == "true":
            log.info("=" * 60)
            log.info("REGISTERED API ROUTES:")
            for route in app.routes:
                if hasattr(route, "path") and hasattr(route, "methods"):
                    methods = ", ".join(route.methods) if route.methods else "N/A"
                    log.info(f"  {methods:8} {route.path}")
            log.info("=" * 60)

    # Get services
    registry = get_registry()
    websocket_hub = get_websocket_hub()

    async def handle_config_update(
        streamer_id: str, message: dict, websocket: WebSocket
    ):
        """Handle dynamic configuration updates via WebSocket."""
        try:
            from src.core.dynamic_config import get_config_manager

            changes = message.get("changes", {})
            if not changes:
                await websocket.send_json(
                    {
                        "type": "config_update_response",
                        "success": False,
                        "error": "No changes provided",
                    }
                )
                return

            # Apply configuration changes
            config_manager = get_config_manager()
            results = await config_manager.apply_changes(streamer_id, changes)

            # Send response back to client
            await websocket.send_json(
                {
                    "type": "config_update_response",
                    "success": True,
                    "results": results,
                    "applied_at": time.time(),
                }
            )

            log.debug(f"[WebSocket] Applied config changes for {streamer_id}: {changes}")

        except Exception as e:
            log.error(f"[WebSocket] Config update error for {streamer_id}: {e}")
            await websocket.send_json(
                {"type": "config_update_response", "success": False, "error": str(e)}
            )

    # Import WebSocket manager
    from src.api.websocket_manager import manager
    
    # WebSocket endpoint for dashboard with JWT authentication
    @app.websocket("/ws/{streamer_id}")
    async def websocket_endpoint(
        websocket: WebSocket, streamer_id: str, token: str | None = None
    ):
        """WebSocket endpoint for real-time dashboard updates with authentication."""
        # First check for token in query params or headers
        if not token:
            # Try to get token from query parameters
            token = websocket.query_params.get("token")

        if not token:
            # Try to get token from headers (for clients that support it)
            auth_header = websocket.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]

        # Validate JWT token if authentication is enabled
        if settings.ENABLE_JWT_AUTH:
            if not token:
                await websocket.close(code=1008, reason="Missing authentication token")
                log.debug(
                    f"[WebSocket] Connection rejected for {streamer_id}: No token provided"
                )
                return

            # Validate the token
            from src.core.jwt_auth import get_jwt_authenticator

            authenticator = get_jwt_authenticator()
            token_data = authenticator.decode_token(token)

            if not token_data:
                await websocket.close(code=1008, reason="Invalid authentication token")
                log.debug(
                    f"[WebSocket] Connection rejected for {streamer_id}: Invalid token"
                )
                return

            # Check if user has permission for this streamer
            if streamer_id != "default" and streamer_id != token_data.get("username"):
                # Check if user has admin role or specific permission
                roles = token_data.get("roles", [])
                if "admin" not in roles and "operator" not in roles:
                    await websocket.close(code=1003, reason="Insufficient permissions")
                    log.debug(
                        f"[WebSocket] Connection rejected for {streamer_id}: Insufficient permissions"
                    )
                    return

            log.debug(
                f"[WebSocket] Authenticated connection for {streamer_id} by user {token_data.get('username')}"
            )

        # Connect using the new WebSocket manager
        metadata = {
            'token_data': token_data if settings.ENABLE_JWT_AUTH and token else None
        }
        
        await manager.connect(websocket, streamer_id, metadata)
        
        try:
            # Keep connection alive and handle messages
            while True:
                try:
                    # Wait for messages
                    message = await websocket.receive_json()
                    log.debug(f"[WebSocket] Received message from {streamer_id}: {message.get('type')}")
                    
                    # Handle message through manager
                    await manager.handle_message(websocket, message)
                    
                    # Legacy config_update handling
                    if message.get("type") == "config_update":
                        await handle_config_update(streamer_id, message, websocket)
                        
                except WebSocketDisconnect:
                    log.debug(f"[WebSocket] Client disconnected: {streamer_id}")
                    break
                except Exception as e:
                    log.error(f"[WebSocket] Error handling message: {e}")
                    # Continue listening for more messages
                    
        finally:
            manager.disconnect(websocket)

    # Prometheus metrics endpoint
    @app.get("/metrics")
    async def prometheus_metrics():
        """Export metrics in Prometheus format."""
        return await metrics_endpoint()

    # Store app start time for uptime calculation
    @app.get("/stats/{streamer_id}", response_class=HTMLResponse)
    async def stats_page(request: Request, streamer_id: str):
        """Serve comprehensive statistics dashboard for specified streamer."""
        return templates.TemplateResponse(
            "stats.html", {"request": request, "streamer_id": streamer_id}
        )

    # Unified System Monitoring Dashboard (V2)
    @app.get("/monitoring", response_class=HTMLResponse)
    async def unified_monitoring_dashboard(request: Request):
        """Serve unified system monitoring dashboard with all monitoring consolidated."""
        return templates.TemplateResponse(
            "unified_monitoring.html", {"request": request}
        )

    @app.get("/monitoring/{streamer_id}", response_class=HTMLResponse)
    async def unified_monitoring_dashboard_with_streamer(
        request: Request, streamer_id: str
    ):
        """Serve unified system monitoring dashboard with streamer context."""
        return templates.TemplateResponse(
            "unified_monitoring.html", {"request": request, "streamer_id": streamer_id}
        )

    # Personality Health Monitor Dashboard
    @app.get("/personality-monitor", response_class=HTMLResponse)
    async def personality_monitor_dashboard(request: Request):
        """Serve personality health monitoring dashboard."""
        # Import feature flag module
        from .feature_flags import should_use_v2_ui
        
        streamer_id = "test_streamer"  # Default for testing
        
        # Check if v2 UI should be used
        if should_use_v2_ui("personality", streamer_id):
            return templates.TemplateResponse(
                "pages-v2/personality.html", {"request": request, "streamer_id": streamer_id}
            )
        else:
            return templates.TemplateResponse(
                "personality_monitor.html", {"request": request}
            )

    @app.get("/personality-monitor/{streamer_id}", response_class=HTMLResponse)
    async def personality_monitor_dashboard_with_streamer(
        request: Request, streamer_id: str
    ):
        """Serve personality health monitoring dashboard with streamer context."""
        # Import feature flag module
        from .feature_flags import should_use_v2_ui
        
        # Check if v2 UI should be used
        if should_use_v2_ui("personality", streamer_id):
            return templates.TemplateResponse(
                "pages-v2/personality.html", {"request": request, "streamer_id": streamer_id}
            )
        else:
            return templates.TemplateResponse(
                "personality_monitor.html", {"request": request, "streamer_id": streamer_id}
            )

    # Redirect old monitoring routes to unified dashboard
    @app.get("/resilience", response_class=HTMLResponse)
    async def resilience_redirect(request: Request):
        """Redirect to unified monitoring dashboard."""
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url="/monitoring", status_code=301)

    @app.get("/self-healing", response_class=HTMLResponse)
    async def self_healing_redirect(request: Request):
        """Redirect to unified monitoring dashboard."""
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url="/monitoring", status_code=301)

    # EMERGENCY FIX: Add missing endpoints directly to app
    @app.get("/api/health")
    async def emergency_health():
        """Emergency health endpoint fix."""
        return {
            "status": "healthy",
            "timestamp": time.time(),
            "message": "TalkBot API is running",
        }
    
    @app.get("/api/services/status")
    async def get_services_status():
        """Get status of all registered services in the ServiceRegistry."""
        try:
            service_registry = ServiceRegistry.get_instance()
            service_info = service_registry.get_service_info()
            
            return {
                "status": "success",
                "timestamp": time.time(),
                "services": service_info,
                "message": f"{service_info['total_initialized']}/{service_info['total_registered']} services initialized"
            }
        except Exception as e:
            log.error(f"Failed to get service status: {e}")
            return {
                "status": "error",
                "timestamp": time.time(),
                "message": str(e)
            }

    @app.post("/api/services/initialize")
    async def initialize_services():
        """Initialize all registered services in the ServiceRegistry."""
        try:
            service_registry = ServiceRegistry.get_instance()
            
            # Get current status before initialization
            before_info = service_registry.get_service_info()
            
            # Initialize all services
            await service_registry.initialize_all()
            
            # Get status after initialization
            after_info = service_registry.get_service_info()
            
            return {
                "status": "success",
                "timestamp": time.time(),
                "before": f"{before_info['total_initialized']}/{before_info['total_registered']} services initialized",
                "after": f"{after_info['total_initialized']}/{after_info['total_registered']} services initialized",
                "newly_initialized": after_info['total_initialized'] - before_info['total_initialized'],
                "message": "Service initialization completed"
            }
        except Exception as e:
            log.error(f"Failed to initialize services: {e}")
            import traceback
            return {
                "status": "error",
                "timestamp": time.time(),
                "message": str(e),
                "traceback": traceback.format_exc()
            }

    @app.get("/api/settings/{streamer_id}")
    async def emergency_settings(
        streamer_id: str,
        current_user: TokenData = Depends(get_current_user)
    ):
        """Settings endpoint - requires authentication.
        
        Users can only access their own settings unless admin.
        """
        # Permission check: only allow access to own settings or if admin
        if "admin" not in current_user.roles:
            if current_user.twitch_id != streamer_id:
                raise HTTPException(
                    status_code=403,
                    detail="Access denied: You can only access your own settings"
                )
        
        try:
            from src.personality.runtime import persona_runtime

            # Read actual saved settings from persona_runtime
            saved_model = persona_runtime.get_model(streamer_id) or "gpt-4o-mini"
            saved_realtime = persona_runtime.get_realtime(streamer_id) or False

            return {
                "model": saved_model,
                "voice_enabled": True,
                "deadair_enabled": True,
                "deadair_threshold": 15.0,
                "memory_enabled": True,
                "realtime_enabled": saved_realtime,
            }
        except Exception as e:
            log.error(f"Error reading settings for {streamer_id}: {e}")
            # Fallback to defaults if reading fails
            return {
                "model": "gpt-4o-mini",
                "voice_enabled": True,
                "deadair_enabled": True,
                "deadair_threshold": 15.0,
                "memory_enabled": True,
                "realtime_enabled": False,
            }

    @app.get("/api/v2/settings/{streamer_id}")
    async def emergency_v2_settings(
        streamer_id: str,
        current_user: TokenData = Depends(get_current_user)
    ):
        """V2 settings endpoint - requires authentication.
        
        Users can only access their own settings unless admin.
        """
        # Permission check: only allow access to own settings or if admin
        if "admin" not in current_user.roles:
            if current_user.twitch_id != streamer_id:
                raise HTTPException(
                    status_code=403,
                    detail="Access denied: You can only access your own settings"
                )
        
        try:
            from src.personality.runtime import persona_runtime
            
            # Read actual saved settings from persona_runtime
            saved_model = persona_runtime.get_model(streamer_id) or "gpt-4o-mini"
            saved_realtime = persona_runtime.get_realtime(streamer_id) or False
            
            return {
                "model": saved_model,
                "voice_enabled": True,
                "deadair_enabled": True,
                "deadair_threshold": 15.0,
                "memory_enabled": True,
                "realtime_enabled": saved_realtime,
            }
        except Exception as e:
            log.error(f"Error reading v2 settings for {streamer_id}: {e}")
            # Fallback to defaults if reading fails
            return {
                "model": "gpt-4o-mini",
                "voice_enabled": True,
                "deadair_enabled": True,
                "deadair_threshold": 15.0,
                "memory_enabled": True,
                "realtime_enabled": False,
            }

    @app.post("/api/mute")
    async def emergency_mute(
        current_user: TokenData = Depends(get_current_user)
    ):
        """Emergency mute endpoint - requires authentication.
        
        Required permission: bot:manage or admin
        """
        # Permission check: require bot:manage or admin
        if "admin" not in current_user.roles and "bot:manage" not in current_user.permissions:
            raise HTTPException(
                status_code=403,
                detail="Access denied: Requires bot:manage permission"
            )
        
        try:
            from src.bot import get_registry

            registry = get_registry()
            # Get the bot for the user's streamer ID
            streamer_id = current_user.twitch_id or current_user.username
            bot = await registry.get(streamer_id)

            if not bot:
                return {"status": "error", "message": "Bot not found"}

            success = await bot.mute(emergency=True)

            print(
                f"[MUTE] Emergency mute result: {success}, Bot state: {bot.state.is_muted}"
            )

            return {
                "status": "success" if success else "failed",
                "action": "mute",
                "muted": bot.state.is_muted,
                "timestamp": time.time(),
            }
        except Exception as e:
            print(f"[MUTE ERROR] {e}")
            import traceback

            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    @app.post("/api/unmute")
    async def emergency_unmute(
        current_user: TokenData = Depends(get_current_user)
    ):
        """Emergency unmute endpoint - requires authentication.
        
        Required permission: bot:manage or admin
        """
        # Permission check: require bot:manage or admin
        if "admin" not in current_user.roles and "bot:manage" not in current_user.permissions:
            raise HTTPException(
                status_code=403,
                detail="Access denied: Requires bot:manage permission"
            )
        
        try:
            from src.bot import get_registry

            registry = get_registry()
            all_bots = registry.get_all()
            bot = None

            # Find the bot for the authenticated user
            streamer_id = current_user.twitch_id or current_user.username
            for bot_id, bot_instance in all_bots.items():
                if bot_instance.channel == streamer_id or streamer_id in bot_id:
                    bot = bot_instance
                    break

            if bot:
                await bot.unmute()
                print(f"[UNMUTE] Bot unmuted successfully. State: {bot.state.is_muted}")
                return {
                    "status": "success",
                    "message": "Bot has been UNMUTED",
                    "muted": False,
                    "timestamp": time.time(),
                }
            else:
                return {"status": "error", "message": "Bot not found"}
        except Exception as e:
            print(f"[UNMUTE ERROR] {e}")
            return {"status": "error", "message": str(e)}

    @app.get("/api/mute/status")
    async def emergency_mute_status(
        current_user: TokenData = Depends(get_current_user)
    ):
        """Check current mute status - requires authentication."""
        try:
            from src.bot import get_registry

            registry = get_registry()
            all_bots = registry.get_all()
            bot = None

            # Find the bot for the authenticated user
            streamer_id = current_user.twitch_id or current_user.username
            for bot_id, bot_instance in all_bots.items():
                if bot_instance.channel == streamer_id or streamer_id in bot_id:
                    bot = bot_instance
                    break

            if bot:
                return {
                    "status": "success",
                    "muted": bot.state.is_muted,
                    "timestamp": time.time(),
                }
            else:
                return {"status": "error", "message": "Bot not found"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @app.post("/api/v2/settings/realtime/{streamer_id}")
    async def update_realtime_mode(
        streamer_id: str,
        request: dict,
        current_user: TokenData = Depends(get_current_user)
    ):
        """Update turbo/realtime mode setting - requires authentication.
        
        Users can only update their own settings unless admin.
        """
        # Permission check: only allow updates to own settings or if admin
        if "admin" not in current_user.roles:
            if current_user.twitch_id != streamer_id:
                raise HTTPException(
                    status_code=403,
                    detail="Access denied: You can only update your own settings"
                )
        
        try:
            from src.personality.runtime import persona_runtime
            from src.bot import get_registry
            
            enabled = request.get("enabled", False)
            
            # Set the realtime mode for the specific streamer
            persona_runtime.set_realtime(streamer_id, enabled)
            
            # Trigger bot configuration refresh to apply the change
            registry = get_registry()
            bot = await registry.get(streamer_id)
            if bot and hasattr(bot, "refresh_configuration"):
                await bot.refresh_configuration()
                log.debug(f"[Settings] Bot configuration refreshed for {streamer_id}")
            
            log.debug(f"[Settings] Turbo mode updated to: {enabled} for {streamer_id}")
            
            return {
                "status": "success",
                "enabled": enabled,
                "message": f"Turbo mode {'enabled' if enabled else 'disabled'}"
            }
        except Exception as e:
            log.error(f"[Settings] Failed to update turbo mode: {e}")
            return {"status": "error", "message": str(e)}

    @app.post("/api/v2/personality-builder/emergency-generate")
    async def emergency_personality_generate(request: dict):
        """Emergency personality generation without AI dependency."""
        from datetime import datetime

        description = request.get("description", "").lower()

        # Simple keyword matching
        if (
            "energetic" in description
            or "hype" in description
            or "gaming" in description
        ):
            personality = {
                "name": "Gaming Companion",
                "traits": {"energy": 0.8, "humor": 0.7, "warmth": 0.6, "sass": 0.3},
                "voice_id": "nova",
                "created_at": datetime.now().isoformat(),
            }
        elif (
            "chill" in description or "calm" in description or "relaxed" in description
        ):
            personality = {
                "name": "Chill Co-Host",
                "traits": {"energy": 0.3, "humor": 0.6, "warmth": 0.8, "sass": 0.2},
                "voice_id": "echo",
                "created_at": datetime.now().isoformat(),
            }
        elif "funny" in description or "comedy" in description or "joke" in description:
            personality = {
                "name": "Comedy Companion",
                "traits": {"energy": 0.7, "humor": 0.9, "warmth": 0.7, "sass": 0.6},
                "voice_id": "fable",
                "created_at": datetime.now().isoformat(),
            }
        else:
            # Default friendly personality
            personality = {
                "name": "Friendly Host",
                "traits": {"energy": 0.6, "humor": 0.6, "warmth": 0.8, "sass": 0.2},
                "voice_id": "alloy",
                "created_at": datetime.now().isoformat(),
            }

        return {
            "success": True,
            "personality": personality,
            "message": "Personality generated successfully (emergency mode)",
            "fallback_used": True,
        }

    # Prometheus metrics endpoint
    @app.get("/metrics")
    async def prometheus_metrics():
        """Export metrics in Prometheus format."""
        return await metrics_endpoint()

    # Store app start time for uptime calculation
    app.state.start_time = time.time()

    # Template routes (moved outside WebSocket function to fix 404 errors)
    @app.get("/test")
    async def test_route():
        """Simple test route to verify route registration."""
        return {"message": "Routes are working!", "timestamp": time.time()}

    @app.get("/debug/routes")
    async def debug_routes():
        """Debug endpoint to list all registered routes."""
        routes = []
        for route in app.routes:
            if hasattr(route, "path") and hasattr(route, "methods"):
                methods = ", ".join(route.methods) if route.methods else "N/A"
                if len(routes) >= 1000:  # Prevent unbounded growth
                    routes.pop(0)
                routes.append(f"{methods:8} {route.path}")
        return {"total_routes": len(routes), "routes": routes}

    @app.get("/debug/template-test", response_class=HTMLResponse)
    async def debug_template_test(request: Request):
        """Debug endpoint to test which template is actually being loaded."""
        return templates.TemplateResponse(
            "dashboard.html", {"request": request, "streamer_id": "debug_test"}
        )

    @app.get("/", response_class=HTMLResponse)
    async def root(request: Request):
        """Redirect root to dashboard with default streamer."""
        from fastapi.responses import RedirectResponse
        # Use confusedamish as the default streamer ID since it's the primary user account
        return RedirectResponse(url="/ui/v2/dashboard/confusedamish", status_code=303)

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard_default(request: Request):
        """Serve dashboard HTML with default test streamer (V1 - Legacy Route)."""
        # Force V1 template for legacy dashboard route to maintain visual distinction
        streamer_id = "test_streamer"  # Default for testing
        return templates.TemplateResponse(
            "dashboard.html", {"request": request, "streamer_id": streamer_id}
        )

    @app.get("/dashboard/{streamer_id}", response_class=HTMLResponse)
    async def dashboard(request: Request, streamer_id: str):
        """Serve dashboard HTML for specified streamer (V1 - Legacy Route)."""
        # Force V1 template for legacy dashboard route to maintain visual distinction
        # This ensures /dashboard/{streamer_id} always serves V1 while /ui/v2/dashboard/{streamer_id} serves V2
        return templates.TemplateResponse(
            "dashboard.html", {"request": request, "streamer_id": streamer_id}
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_default(request: Request):
        """Redirect to settings with default streamer ID."""
        from fastapi.responses import RedirectResponse
        # Use confusedamish as the default streamer ID since it's the primary user account
        return RedirectResponse(url="/settings/confusedamish", status_code=303)

    @app.get("/settings/{streamer_id}", response_class=HTMLResponse)
    async def settings_page(request: Request, streamer_id: str):
        """Serve settings UI for specified streamer."""
        # Import feature flag module
        from .feature_flags import should_use_v2_ui
        
        # Check if v2 UI should be used
        if should_use_v2_ui("settings", streamer_id):
            return templates.TemplateResponse(
                "pages-v2/settings.html", {"request": request, "streamer_id": streamer_id, "timestamp": int(time.time())}
            )
        else:
            return templates.TemplateResponse(
                "settings.html", {"request": request, "streamer_id": streamer_id, "timestamp": int(time.time())}
            )

    @app.get("/analytics/{streamer_id}", response_class=HTMLResponse)
    async def analytics_page(request: Request, streamer_id: str):
        """Serve analytics dashboard for specified streamer."""
        # Import feature flag module
        from .feature_flags import should_use_v2_ui
        
        # Check if v2 UI should be used
        if should_use_v2_ui("analytics", streamer_id):
            return templates.TemplateResponse(
                "pages-v2/analytics.html", {"request": request, "streamer_id": streamer_id}
            )
        else:
            return templates.TemplateResponse(
                "analytics.html", {"request": request, "streamer_id": streamer_id}
            )

    @app.get("/visualizer/demo", response_class=HTMLResponse)
    async def visualizer_demo_page(request: Request):
        """Serve demo graph visualization page."""
        return templates.TemplateResponse(
            "visualizer.html", {"request": request, "streamer_id": "demo"}
        )
    
    @app.get("/visualizer/{streamer_id}", response_class=HTMLResponse)
    async def visualizer_page(request: Request, streamer_id: str):
        """Serve interactive graph visualization for specified streamer's Twitch universe."""
        return templates.TemplateResponse(
            "visualizer.html", {"request": request, "streamer_id": streamer_id}
        )

    # V1 to V2 compatibility layer - REMOVED as part of API migration to v2 only
    # v1_to_v2_compat_router removed - frontend now uses v2 endpoints exclusively
    
    # ============================================================================
    # V2 UI Routes - New design system templates
    # ============================================================================
    @app.get("/ui/v2/test", response_class=HTMLResponse)
    async def v2_test_page(request: Request):
        """Test page for v2 UI to verify everything works."""
        return templates.TemplateResponse(
            "test-v2.html", {"request": request, "debug": settings.DEBUG}
        )
    
    @app.get("/ui/v2/dashboard", response_class=HTMLResponse)
    async def v2_dashboard_default(request: Request):
        """V2 Dashboard with default streamer."""
        streamer_id = "test_streamer"
        return templates.TemplateResponse(
            "pages-v2/dashboard.html", {"request": request, "streamer_id": streamer_id}
        )
    
    @app.get("/ui/v2/dashboard/{streamer_id}", response_class=HTMLResponse)
    async def v2_dashboard(request: Request, streamer_id: str):
        """V2 Dashboard for specified streamer."""
        return templates.TemplateResponse(
            "pages-v2/dashboard.html", {"request": request, "streamer_id": streamer_id}
        )
    
    @app.get("/ui/v2/analytics/{streamer_id}", response_class=HTMLResponse)
    async def v2_analytics(request: Request, streamer_id: str):
        """V2 Analytics page for specified streamer."""
        return templates.TemplateResponse(
            "pages-v2/analytics.html", {"request": request, "streamer_id": streamer_id}
        )
    
    @app.get("/ui/v2/settings/{streamer_id}", response_class=HTMLResponse)
    async def v2_settings(request: Request, streamer_id: str):
        """V2 Settings page for specified streamer."""
        return templates.TemplateResponse(
            "pages-v2/settings.html", {"request": request, "streamer_id": streamer_id, "timestamp": int(time.time())}
        )
    
    @app.get("/ui/v2/memory/{streamer_id}", response_class=HTMLResponse)
    async def v2_memory(request: Request, streamer_id: str):
        """V2 Memory system page for specified streamer."""
        return templates.TemplateResponse(
            "pages-v2/memory.html", {"request": request, "streamer_id": streamer_id}
        )
    
    @app.get("/ui/v2/personality/{streamer_id}", response_class=HTMLResponse)
    async def v2_personality(request: Request, streamer_id: str):
        """V2 Personality configuration page for specified streamer."""
        return templates.TemplateResponse(
            "pages-v2/personality.html", {"request": request, "streamer_id": streamer_id}
        )
    
    @app.get("/ui/v2/live/{streamer_id}", response_class=HTMLResponse)
    async def v2_live(request: Request, streamer_id: str):
        """V2 Live streaming dashboard for specified streamer."""
        return templates.TemplateResponse(
            "pages-v2/live.html", {"request": request, "streamer_id": streamer_id}
        )
    
    @app.get("/ui/v2/health/{streamer_id}", response_class=HTMLResponse)
    async def v2_health(request: Request, streamer_id: str):
        """V2 System health monitoring page for specified streamer."""
        return templates.TemplateResponse(
            "pages-v2/health.html", {"request": request, "streamer_id": streamer_id}
        )
    
    @app.get("/ui/v2/insights/{streamer_id}", response_class=HTMLResponse)
    async def v2_insights(request: Request, streamer_id: str):
        """V2 AI-powered insights page for specified streamer."""
        return templates.TemplateResponse(
            "pages-v2/insights.html", {"request": request, "streamer_id": streamer_id}
        )
    
    @app.get("/components", response_class=HTMLResponse)
    async def component_showcase(request: Request):
        """V2 Component showcase for testing and development."""
        return templates.TemplateResponse(
            "component-showcase.html", {"request": request, "streamer_id": "demo"}
        )
    
    # Note: Dashboard and settings routes are already defined above (lines 1227-1259)
    # Removed duplicate route definitions that were causing conflicts
    
    # Clean Design Mockups (Not integrated yet)
    @app.get("/mockup/analytics", response_class=HTMLResponse)
    @app.get("/mockup/analytics/{streamer_id}", response_class=HTMLResponse)
    async def mockup_analytics(request: Request, streamer_id: str = "default"):
        """Clean analytics mockup for review."""
        return templates.TemplateResponse(
            "mockup-analytics.html", {"request": request, "streamer_id": streamer_id}
        )
    
    @app.get("/mockup/insights", response_class=HTMLResponse)
    @app.get("/mockup/insights/{streamer_id}", response_class=HTMLResponse)
    async def mockup_insights(request: Request, streamer_id: str = "default"):
        """Clean insights mockup for review."""
        return templates.TemplateResponse(
            "mockup-insights.html", {"request": request, "streamer_id": streamer_id}
        )
    
    @app.get("/mockup/health", response_class=HTMLResponse)
    @app.get("/mockup/health/{streamer_id}", response_class=HTMLResponse)
    async def mockup_health(request: Request, streamer_id: str = "default"):
        """Clean system health mockup for review."""
        return templates.TemplateResponse(
            "mockup-health.html", {"request": request, "streamer_id": streamer_id}
        )

    return app


# Global app instance
_global_app: FastAPI | None = None


def get_app() -> FastAPI:
    """Get global FastAPI application instance."""
    global _global_app
    if _global_app is None:
        _global_app = create_app()
    return _global_app


# Initialize the app at module level - EMERGENCY FIX: Create fresh app
app = create_app()  # Bypass get_app() caching that might cause issues
