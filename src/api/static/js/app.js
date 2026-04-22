// TalkBot Application JavaScript
// Global application utilities and initialization


// Timer Manager for preventing memory leaks
class TimerManager {
    constructor() {
        this.timers = new Set();
    }
    
    timerManager.setTimeout(callback, delay) {
        const id = timerManager.setTimeout(() => {
            this.timers.delete(id);
            callback();
        }, delay);
        this.timers.add(id);
        return id;
    }
    
    timerManager.setInterval(callback, delay) {
        const id = timerManager.setInterval(callback, delay);
        this.timers.add(id);
        return id;
    }
    
    timerManager.clearTimeout(id) {
        this.timers.delete(id);
        timerManager.clearTimeout(id);
    }
    
    timerManager.clearInterval(id) {
        this.timers.delete(id);
        timerManager.clearInterval(id);
    }
    
    clearAll() {
        for (const id of this.timers) {
            timerManager.clearTimeout(id);
            timerManager.clearInterval(id);
        }
        this.timers.clear();
    }
}

const timerManager = new TimerManager();

console.log('[TalkBot App] Initializing application...');

// Global application state
window.TalkBot = {
    version: '2.0',
    initialized: false,
    debug: false
};

// Toast notification system
class Toast {
    static container = null;
    static toastCount = 0;

    static init() {
        // Ensure DOM is ready before trying to access elements
        if (!document.body) {
            console.warn('[Toast] Document body not ready, deferring initialization');
            return;
        }
        
        if (!this.container) {
            this.container = document.getElementById('toast-container');
            if (!this.container) {
                // Create container if it doesn't exist
                this.container = document.createElement('div');
                this.container.id = 'toast-container';
                this.container.className = 'toast-container';
                document.body.appendChild(this.container);
            }
        }
    }

    static show(message, type = 'info', duration = 3000) {
        this.init();
        
        const toast = document.createElement('div');
        const toastId = ++this.toastCount;
        
        toast.className = `toast toast-${type}`;
        toast.innerHTML = `
            <span class="toast-message">${message}</span>
            <button onclick="Toast.dismiss(${toastId})" class="toast-close">
                <svg class="toast-close-icon" fill="currentColor" viewBox="0 0 20 20">
                    <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"/>
                </svg>
            </button>
        `;
        toast.dataset.toastId = toastId;
        
        // No need for manual stacking with relative positioning
        // Toasts will stack naturally with margin-bottom
        
        this.container.appendChild(toast);
        
        // Auto dismiss
        if (duration > 0) {
            timerManager.setTimeout(() => this.dismiss(toastId), duration);
        }
        
        // Manual dismiss on click
        toast.addEventListener('click', () => this.dismiss(toastId));
        
        return toastId;
    }

    static dismiss(toastId) {
        const toast = this.container.querySelector(`[data-toast-id="${toastId}"]`);
        if (toast) {
            toast.classList.add('toast-fade-out');
            toast.addEventListener('animationend', () => {
                toast.remove();
                this.restack();
            });
        }
    }

    static restack() {
        // No longer needed with relative positioning
        // Kept for compatibility
    }

    static clear() {
        if (this.container) {
            this.container.innerHTML = '';
        }
    }
}

// Global error handler
window.addEventListener('error', function(e) {
    console.error('[TalkBot App] Global error:', e.error);
    if (window.TalkBot.debug) {
        Toast.show(`Error: ${e.message}`, 'error', 5000);
    }
});

// Unhandled promise rejection handler
window.addEventListener('unhandledrejection', function(e) {
    console.error('[TalkBot App] Unhandled promise rejection:', e.reason);
    if (window.TalkBot.debug) {
        Toast.show(`Promise rejection: ${e.reason}`, 'error', 5000);
    }
});

// API helper functions
class API {
    static baseUrl = '';
    
    static async request(endpoint, options = {}) {
        const url = this.baseUrl + endpoint;
        const defaultOptions = {
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            }
        };
        
        try {
            const response = await fetch(url, { ...defaultOptions, ...options });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const contentType = response.headers.get('content-type');
            if (contentType && contentType.includes('application/json')) {
                return await response.json();
            }
            
            return await response.text();
        } catch (error) {
            console.error(`[API] Request failed: ${endpoint}`, error);
            throw error;
        }
    }
    
    static async get(endpoint) {
        return this.request(endpoint, { method: 'GET' });
    }
    
    static async post(endpoint, data) {
        return this.request(endpoint, {
            method: 'POST',
            body: JSON.stringify(data)
        });
    }
    
    static async put(endpoint, data) {
        return this.request(endpoint, {
            method: 'PUT',
            body: JSON.stringify(data)
        });
    }
    
    static async delete(endpoint) {
        return this.request(endpoint, { method: 'DELETE' });
    }
}

// Utility functions
const utils = {
    debounce(func, wait) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                timerManager.clearTimeout(timeout);
                func(...args);
            };
            timerManager.clearTimeout(timeout);
            timeout = timerManager.setTimeout(later, wait);
        };
    },
    
    throttle(func, limit) {
        let inThrottle;
        return function() {
            const args = arguments;
            const context = this;
            if (!inThrottle) {
                func.apply(context, args);
                inThrottle = true;
                timerManager.setTimeout(() => inThrottle = false, limit);
            }
        };
    },
    
    formatTime(timestamp) {
        const now = Date.now();
        const diff = now - timestamp;
        
        if (diff < 60000) return 'now';
        if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
        if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
        return Math.floor(diff / 86400000) + 'd ago';
    },
    
    formatUptime(seconds) {
        if (!seconds) return '0m';
        
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        
        if (hours > 0) {
            return `${hours}h ${minutes}m`;
        }
        return `${minutes}m`;
    },
    
    validateTraitValue(value, defaultValue = 0.5) {
        const numValue = parseFloat(value);
        if (isNaN(numValue) || numValue < 0 || numValue > 1) {
            return defaultValue;
        }
        return numValue;
    },
    
    copyToClipboard(text) {
        if (navigator.clipboard) {
            return navigator.clipboard.writeText(text);
        } else {
            // Fallback for older browsers
            const textArea = document.createElement('textarea');
            textArea.value = text;
            document.body.appendChild(textArea);
            textArea.select();
            document.execCommand('copy');
            document.body.removeChild(textArea);
            return Promise.resolve();
        }
    }
};

// Keyboard shortcuts
document.addEventListener('keydown', function(e) {
    // Ctrl+S to save settings (if on settings page)
    if (e.ctrlKey && e.key === 's' && window.settingsPage) {
        e.preventDefault();
        if (typeof window.settingsPage === 'function') {
            const component = Alpine.store('settings');
            if (component && component.saveTraits) {
                component.saveTraits();
                Toast.show('Settings saved!', 'success');
            }
        }
    }
    
    // Escape to close modals
    if (e.key === 'Escape') {
        const modals = document.querySelectorAll('[x-show]:not([style*="display: none"])');
        modals.forEach(modal => {
            const alpineData = Alpine.$data(modal);
            if (alpineData) {
                // Look for common modal state variables
                ['showModal', 'showCustomPresetModal', 'showMemoryModal'].forEach(prop => {
                    if (alpineData[prop] === true) {
                        alpineData[prop] = false;
                    }
                });
            }
        });
    }
});

// Enhanced console logging for development
if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
    window.TalkBot.debug = true;
    console.log('[TalkBot App] Debug mode enabled');
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
    console.log('[TalkBot App] DOM ready, initializing...');
    
    // Initialize toast system
    Toast.init();
    
    // Mark as initialized
    window.TalkBot.initialized = true;
    
    // Dispatch custom event for other scripts
    document.dispatchEvent(new CustomEvent('talkbot:ready'));
    
    console.log('[TalkBot App] Application initialized successfully');
});

// Make utilities globally available
window.Toast = Toast;
window.API = API;
window.utils = utils;
// Bind Toast.show to the Toast class for backward compatibility
window.showToast = function(message, type, duration) {
    return Toast.show(message, type, duration);
};

// Export for module systems if needed
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { Toast, API, utils };
}

console.log('[TalkBot App] Script loaded successfully');
// Clean up timers on page unload
if (typeof window !== 'undefined') {
    window.addEventListener('beforeunload', () => {
        if (typeof timerManager !== 'undefined') {
            timerManager.clearAll();
        }
    });
}
