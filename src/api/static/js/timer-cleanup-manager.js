/**
 * Global Timer Cleanup Manager
 * Tracks and cleans up all timers/intervals to prevent memory leaks
 */
class TimerCleanupManager {
    constructor() {
        this.intervals = new Set();
        this.timeouts = new Set();
        this.animationFrames = new Set();
        this.componentTimers = new Map(); // Component-specific timers
        
        // Auto-cleanup on page unload
        window.addEventListener('beforeunload', () => this.cleanupAll());
        window.addEventListener('pagehide', () => this.cleanupAll());
        
        // For Single Page Apps - cleanup on navigation
        if (window.Alpine) {
            document.addEventListener('alpine:destroying', () => this.cleanupAll());
        }
    }
    
    /**
     * Register and start an interval
     * @param {Function} callback - Function to execute
     * @param {number} delay - Delay in milliseconds
     * @param {string} componentId - Optional component identifier
     * @returns {number} Interval ID
     */
    setInterval(callback, delay, componentId = null) {
        const id = window.setInterval(callback, delay);
        this.intervals.add(id);
        
        if (componentId) {
            if (!this.componentTimers.has(componentId)) {
                this.componentTimers.set(componentId, {
                    intervals: new Set(),
                    timeouts: new Set()
                });
            }
            this.componentTimers.get(componentId).intervals.add(id);
        }
        
        return id;
    }
    
    /**
     * Register and start a timeout
     * @param {Function} callback - Function to execute
     * @param {number} delay - Delay in milliseconds
     * @param {string} componentId - Optional component identifier
     * @returns {number} Timeout ID
     */
    setTimeout(callback, delay, componentId = null) {
        const wrappedCallback = () => {
            callback();
            this.timeouts.delete(id);
            if (componentId && this.componentTimers.has(componentId)) {
                this.componentTimers.get(componentId).timeouts.delete(id);
            }
        };
        
        const id = window.setTimeout(wrappedCallback, delay);
        this.timeouts.add(id);
        
        if (componentId) {
            if (!this.componentTimers.has(componentId)) {
                this.componentTimers.set(componentId, {
                    intervals: new Set(),
                    timeouts: new Set()
                });
            }
            this.componentTimers.get(componentId).timeouts.add(id);
        }
        
        return id;
    }
    
    /**
     * Clear a specific interval
     * @param {number} id - Interval ID to clear
     */
    clearInterval(id) {
        window.clearInterval(id);
        this.intervals.delete(id);
        
        // Remove from component timers if exists
        for (const [componentId, timers] of this.componentTimers) {
            if (timers.intervals.has(id)) {
                timers.intervals.delete(id);
                break;
            }
        }
    }
    
    /**
     * Clear a specific timeout
     * @param {number} id - Timeout ID to clear
     */
    clearTimeout(id) {
        window.clearTimeout(id);
        this.timeouts.delete(id);
        
        // Remove from component timers if exists
        for (const [componentId, timers] of this.componentTimers) {
            if (timers.timeouts.has(id)) {
                timers.timeouts.delete(id);
                break;
            }
        }
    }
    
    /**
     * Register an animation frame
     * @param {Function} callback - Animation callback
     * @returns {number} Animation frame ID
     */
    requestAnimationFrame(callback) {
        const wrappedCallback = (timestamp) => {
            callback(timestamp);
            this.animationFrames.delete(id);
        };
        
        const id = window.requestAnimationFrame(wrappedCallback);
        this.animationFrames.add(id);
        return id;
    }
    
    /**
     * Cancel an animation frame
     * @param {number} id - Animation frame ID
     */
    cancelAnimationFrame(id) {
        window.cancelAnimationFrame(id);
        this.animationFrames.delete(id);
    }
    
    /**
     * Clean up all timers for a specific component
     * @param {string} componentId - Component identifier
     */
    cleanupComponent(componentId) {
        if (!this.componentTimers.has(componentId)) {
            return;
        }
        
        const timers = this.componentTimers.get(componentId);
        
        // Clear all intervals for this component
        for (const id of timers.intervals) {
            this.clearInterval(id);
        }
        
        // Clear all timeouts for this component
        for (const id of timers.timeouts) {
            this.clearTimeout(id);
        }
        
        // Remove component from registry
        this.componentTimers.delete(componentId);
    }
    
    /**
     * Clean up all registered timers
     */
    cleanupAll() {
        // Clear all intervals
        for (const id of this.intervals) {
            window.clearInterval(id);
        }
        this.intervals.clear();
        
        // Clear all timeouts
        for (const id of this.timeouts) {
            window.clearTimeout(id);
        }
        this.timeouts.clear();
        
        // Cancel all animation frames
        for (const id of this.animationFrames) {
            window.cancelAnimationFrame(id);
        }
        this.animationFrames.clear();
        
        // Clear component registry
        this.componentTimers.clear();
        
        console.log('[TimerCleanupManager] All timers cleaned up');
    }
    
    /**
     * Get statistics about registered timers
     * @returns {Object} Timer statistics
     */
    getStats() {
        return {
            intervals: this.intervals.size,
            timeouts: this.timeouts.size,
            animationFrames: this.animationFrames.size,
            components: this.componentTimers.size,
            total: this.intervals.size + this.timeouts.size + this.animationFrames.size
        };
    }
    
    /**
     * Create a component-scoped timer manager
     * @param {string} componentId - Component identifier
     * @returns {Object} Component timer interface
     */
    createComponentScope(componentId) {
        return {
            setInterval: (callback, delay) => this.setInterval(callback, delay, componentId),
            setTimeout: (callback, delay) => this.setTimeout(callback, delay, componentId),
            clearInterval: (id) => this.clearInterval(id),
            clearTimeout: (id) => this.clearTimeout(id),
            cleanup: () => this.cleanupComponent(componentId)
        };
    }
}

// Create global instance
const timerManager = new TimerCleanupManager();

// Override global timer functions to use manager
window.managedSetInterval = (callback, delay, componentId) => timerManager.setInterval(callback, delay, componentId);
window.managedSetTimeout = (callback, delay, componentId) => timerManager.setTimeout(callback, delay, componentId);
window.managedClearInterval = (id) => timerManager.clearInterval(id);
window.managedClearTimeout = (id) => timerManager.clearTimeout(id);

// Export for module usage
if (typeof module !== 'undefined' && module.exports) {
    module.exports = timerManager;
}

// Make available globally
window.TimerCleanupManager = TimerCleanupManager;
window.timerManager = timerManager;

console.log('[TimerCleanupManager] Initialized - preventing timer memory leaks');