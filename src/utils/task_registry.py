"""
TaskRegistry implementation for proper AsyncIO task management
Prevents memory leaks by tracking and cleaning up all tasks
"""

import asyncio
import logging
from typing import Dict, Set, Optional, Any, Coroutine
from datetime import datetime
import weakref

logger = logging.getLogger(__name__)

class TaskRegistry:
    """
    Centralized task management to prevent AsyncIO memory leaks
    Replaces direct asyncio.create_task() calls throughout the codebase
    """
    
    def __init__(self):
        """Initialize the task registry"""
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.completed_tasks: Set[str] = set()
        self.task_stats: Dict[str, Dict[str, Any]] = {}
        self._cleanup_interval = 60  # Cleanup every 60 seconds
        self._cleanup_task: Optional[asyncio.Task] = None
        self._shutdown = False
        
        # Start cleanup task
        self._start_cleanup()
        
    def _start_cleanup(self) -> None:
        """Start the background cleanup task"""
        if not self._cleanup_task or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
            
    async def _periodic_cleanup(self) -> None:
        """Periodically clean up completed tasks"""
        while not self._shutdown:
            try:
                await asyncio.sleep(self._cleanup_interval)
                await self.cleanup_completed()
            except Exception as e:
                logger.error(f"Error in periodic cleanup: {e}")
                
    def create_task(
        self,
        coro: Coroutine,
        name: Optional[str] = None,
        cleanup: bool = True
    ) -> asyncio.Task:
        """
        Create and register a task with proper tracking
        
        Args:
            coro: Coroutine to run
            name: Optional name for the task
            cleanup: Whether to auto-cleanup when done
            
        Returns:
            The created task
        """
        # Generate name if not provided
        if not name:
            name = f"task_{len(self.active_tasks)}_{datetime.now().timestamp()}"
            
        # Cancel existing task with same name if it exists
        if name in self.active_tasks:
            old_task = self.active_tasks[name]
            if not old_task.done():
                logger.warning(f"Cancelling existing task with name: {name}")
                old_task.cancel()
                
        # Create the task
        task = asyncio.create_task(coro)
        task.set_name(name)
        
        # Register the task
        self.active_tasks[name] = task
        self.task_stats[name] = {
            'created': datetime.now(),
            'status': 'running'
        }
        
        # Add completion callback if cleanup is enabled
        if cleanup:
            task.add_done_callback(lambda t: self._task_done(name, t))
            
        logger.debug(f"Created task: {name}")
        return task
        
    def _task_done(self, name: str, task: asyncio.Task) -> None:
        """
        Callback when a task completes
        
        Args:
            name: Task name
            task: The completed task
        """
        try:
            # Update stats
            if name in self.task_stats:
                self.task_stats[name]['completed'] = datetime.now()
                self.task_stats[name]['status'] = 'done'
                
                # Check for exceptions
                if task.exception():
                    self.task_stats[name]['error'] = str(task.exception())
                    logger.error(f"Task {name} failed with error: {task.exception()}")
                    
            # Move to completed set
            if name in self.active_tasks:
                del self.active_tasks[name]
            self.completed_tasks.add(name)
            
            logger.debug(f"Task completed: {name}")
            
        except Exception as e:
            logger.error(f"Error in task completion callback: {e}")
            
    async def cancel_task(self, name: str) -> bool:
        """
        Cancel a specific task by name
        
        Args:
            name: Task name
            
        Returns:
            True if task was cancelled, False if not found
        """
        if name in self.active_tasks:
            task = self.active_tasks[name]
            if not task.done():
                task.cancel()
                logger.info(f"Cancelled task: {name}")
                return True
        return False
        
    async def cancel_all(self) -> int:
        """
        Cancel all active tasks
        
        Returns:
            Number of tasks cancelled
        """
        cancelled = 0
        for name, task in list(self.active_tasks.items()):
            if not task.done():
                task.cancel()
                cancelled += 1
                
        logger.info(f"Cancelled {cancelled} tasks")
        return cancelled
        
    async def wait_for_task(self, name: str, timeout: Optional[float] = None) -> Any:
        """
        Wait for a specific task to complete
        
        Args:
            name: Task name
            timeout: Optional timeout in seconds
            
        Returns:
            Task result
            
        Raises:
            KeyError: If task not found
            asyncio.TimeoutError: If timeout exceeded
        """
        if name not in self.active_tasks:
            raise KeyError(f"Task not found: {name}")
            
        task = self.active_tasks[name]
        return await asyncio.wait_for(task, timeout=timeout)
        
    async def gather(
        self,
        *names: str,
        return_exceptions: bool = False,
        timeout: Optional[float] = None
    ) -> list:
        """
        Gather results from multiple tasks
        Fixed version that properly handles task objects
        
        Args:
            names: Task names to gather
            return_exceptions: Whether to return exceptions as results
            timeout: Optional timeout for all tasks
            
        Returns:
            List of task results
        """
        tasks = []
        for name in names:
            if name in self.active_tasks:
                task = self.active_tasks[name]
                # Only add if task is not done or if we want the result
                if not task.done() or return_exceptions:
                    tasks.append(task)
                    
        if not tasks:
            return []
            
        if timeout:
            return await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=return_exceptions),
                timeout=timeout
            )
        else:
            return await asyncio.gather(*tasks, return_exceptions=return_exceptions)
            
    async def cleanup_completed(self) -> int:
        """
        Clean up completed tasks to free memory
        
        Returns:
            Number of tasks cleaned up
        """
        cleaned = 0
        
        # Clean up completed tasks from stats
        cutoff_time = datetime.now().timestamp() - 3600  # Keep stats for 1 hour
        for name in list(self.completed_tasks):
            if name in self.task_stats:
                completed_time = self.task_stats[name].get('completed')
                if completed_time and completed_time.timestamp() < cutoff_time:
                    del self.task_stats[name]
                    self.completed_tasks.discard(name)
                    cleaned += 1
                    
        if cleaned > 0:
            logger.debug(f"Cleaned up {cleaned} completed tasks")
            
        return cleaned
        
    def get_stats(self) -> Dict[str, Any]:
        """
        Get registry statistics
        
        Returns:
            Dictionary with stats
        """
        return {
            'active_tasks': len(self.active_tasks),
            'completed_tasks': len(self.completed_tasks),
            'total_tracked': len(self.task_stats),
            'tasks': {
                name: {
                    'status': 'running' if name in self.active_tasks else 'completed',
                    'created': stats.get('created').isoformat() if stats.get('created') else None,
                    'completed': stats.get('completed').isoformat() if stats.get('completed') else None,
                    'error': stats.get('error')
                }
                for name, stats in list(self.task_stats.items())[:10]  # Limit to 10 for display
            }
        }
        
    def is_running(self, name: str) -> bool:
        """
        Check if a task is currently running
        
        Args:
            name: Task name
            
        Returns:
            True if task is running
        """
        if name in self.active_tasks:
            task = self.active_tasks[name]
            return not task.done()
        return False
        
    async def shutdown(self) -> None:
        """
        Shutdown the registry and cancel all tasks
        """
        logger.info("Shutting down TaskRegistry...")
        self._shutdown = True
        
        # Cancel cleanup task
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            
        # Cancel all active tasks
        cancelled = await self.cancel_all()
        
        # Wait a bit for tasks to finish cancelling
        await asyncio.sleep(0.1)
        
        # Clear all tracking
        self.active_tasks.clear()
        self.completed_tasks.clear()
        self.task_stats.clear()
        
        logger.info(f"TaskRegistry shutdown complete. Cancelled {cancelled} tasks")

# Global registry instance (singleton pattern)
_global_registry: Optional[TaskRegistry] = None

def get_global_registry() -> TaskRegistry:
    """
    Get the global TaskRegistry instance
    
    Returns:
        The global TaskRegistry
    """
    global _global_registry
    if _global_registry is None:
        _global_registry = TaskRegistry()
    return _global_registry

def create_task(
    coro: Coroutine,
    name: Optional[str] = None,
    cleanup: bool = True
) -> asyncio.Task:
    """
    Convenience function to create a task using the global registry
    
    Args:
        coro: Coroutine to run
        name: Optional task name
        cleanup: Whether to auto-cleanup
        
    Returns:
        The created task
    """
    return get_global_registry().create_task(coro, name, cleanup)