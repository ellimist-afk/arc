/**

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

 * Personality Integration Bridge
 * Ensures seamless integration between legacy and v2 personality systems
 */

class PersonalityIntegrationBridge {
    constructor() {
        this.initialized = false;
        this.eventListeners = [];
        this.debounceTimeout = null;
        this.lastSyncTime = null;
        
        this.init();
    }
    
    init() {
        if (this.initialized) return;
        
        console.log('[PersonalityBridge] Initializing integration bridge...');
        
        // Wait for Alpine and DOM to be ready
        this.waitForAlpine().then(() => {
            this.setupEventListeners();
            this.setupStateSync();
            this.initialized = true;
            console.log('[PersonalityBridge] Bridge initialized successfully');
        });
    }
    
    async waitForAlpine() {
        return new Promise((resolve) => {
            if (window.Alpine) {
                resolve();
            } else {
                const checkAlpine = () => {
                    if (window.Alpine) {
                        resolve();
                    } else {
                        timerManager.setTimeout(checkAlpine, 100);
                    }
                };
                checkAlpine();
            }
        });
    }
    
    setupEventListeners() {
        // Listen for trait changes from legacy system
        document.addEventListener('traitsLoaded', (event) => {
            console.log('[PersonalityBridge] Legacy traits loaded:', event.detail);
            this.syncToV2Components(event.detail);
        });
        
        document.addEventListener('presetApplied', (event) => {
            console.log('[PersonalityBridge] Legacy preset applied:', event.detail);
            this.syncToV2Components(event.detail);
        });
        
        // Listen for v2 component events
        document.addEventListener('personality:loaded', (event) => {
            console.log('[PersonalityBridge] V2 personality loaded:', event.detail);
            this.syncToLegacyComponents(event.detail.traits);
        });
        
        document.addEventListener('trait:changed', (event) => {
            console.log('[PersonalityBridge] V2 trait changed:', event.detail);
            this.debouncedSync(() => {
                this.syncToLegacyComponents({ [event.detail.trait]: event.detail.value });
            });
        });
        
        document.addEventListener('settings:saved', (event) => {
            console.log('[PersonalityBridge] V2 settings saved:', event.detail);
            this.syncToLegacyComponents(event.detail.traits);
        });
        
        // Listen for preset applications from either system
        document.addEventListener('preset:applied', (event) => {
            console.log('[PersonalityBridge] Preset applied:', event.detail);
            this.syncBothSystems(event.detail.traits);
        });
    }
    
    setupStateSync() {
        // Sync when Alpine stores change
        if (window.Alpine && Alpine.store) {
            // Watch for personality store changes
            this.$watch = Alpine.effect(() => {
                const store = Alpine.store('settings');
                if (store && store.personality) {
                    this.debouncedSync(() => {
                        this.syncToLegacyComponents(store.personality.traits);
                    });
                }
            });
        }
    }
    
    syncToV2Components(traits) {
        try {
            // Update Alpine store
            if (window.Alpine && Alpine.store) {
                const settingsStore = Alpine.store('settings');
                if (settingsStore && settingsStore.personality) {
                    Object.assign(settingsStore.personality.traits, traits);
                    console.log('[PersonalityBridge] Synced to Alpine store');
                }
            }
            
            // Dispatch event for v2 components
            document.dispatchEvent(new CustomEvent('personality:sync', {
                detail: { traits, source: 'legacy' }
            }));
            
        } catch (error) {
            console.error('[PersonalityBridge] Error syncing to v2:', error);
        }
    }
    
    syncToLegacyComponents(traits) {
        try {
            // Find legacy settings component
            const settingsEl = document.querySelector('[x-data*="settingsPage"]');
            if (settingsEl && settingsEl._x_dataStack) {
                const settingsData = settingsEl._x_dataStack[0];
                if (settingsData && settingsData.traits) {
                    Object.assign(settingsData.traits, traits);
                    
                    // Update visual sliders if they exist
                    this.updateLegacySliders(traits);
                    console.log('[PersonalityBridge] Synced to legacy components');
                }
            }
            
        } catch (error) {
            console.error('[PersonalityBridge] Error syncing to legacy:', error);
        }
    }
    
    syncBothSystems(traits) {
        this.syncToV2Components(traits);
        this.syncToLegacyComponents(traits);
    }
    
    updateLegacySliders(traits) {
        Object.entries(traits).forEach(([trait, value]) => {
            // Update slider value
            const slider = document.querySelector(`input[x-model="traits.${trait}"]`);
            if (slider) {
                slider.value = value;
                slider.dispatchEvent(new Event('input'));
            }
            
            // Update visual progress
            const progressFill = document.querySelector(`.slider-progress-fill[data-trait="${trait}"]`);
            if (progressFill) {
                progressFill.style.width = `${value * 100}%`;
            }
        });
    }
    
    debouncedSync(callback) {
        timerManager.clearTimeout(this.debounceTimeout);
        this.debounceTimeout = timerManager.setTimeout(() => {
            if (Date.now() - (this.lastSyncTime || 0) > 500) {
                callback();
                this.lastSyncTime = Date.now();
            }
        }, 200);
    }
    
    // API Integration Methods
    async savePersonality(streamerId, traits) {
        try {
            console.log('[PersonalityBridge] Saving personality via bridge:', traits);
            
            const response = await fetch(`/api/v2/personality-v2/traits/${streamerId}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(traits)
            });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const result = await response.json();
            
            if (result.success) {
                // Sync the saved traits to both systems
                this.syncBothSystems(result.traits);
                
                // Show success notification
                if (window.showToast) {
                    window.showToast('Personality settings saved successfully', 'success');
                } else if (window.Alpine && Alpine.store('app')) {
                    Alpine.store('app').notify('Personality settings saved successfully', 'success');
                }
                
                console.log('[PersonalityBridge] Personality saved successfully');
                return result;
            } else {
                throw new Error(result.message || 'Save failed');
            }
            
        } catch (error) {
            console.error('[PersonalityBridge] Failed to save personality:', error);
            
            // Show error notification
            if (window.showToast) {
                window.showToast('Failed to save personality settings', 'error');
            } else if (window.Alpine && Alpine.store('app')) {
                Alpine.store('app').handleError(error, 'Save Personality');
            }
            
            throw error;
        }
    }
    
    async loadPersonality(streamerId) {
        try {
            console.log('[PersonalityBridge] Loading personality via bridge');
            
            const response = await fetch(`/api/v2/personality-v2/current/${streamerId}`);
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const result = await response.json();
            
            if (result.traits) {
                // Sync loaded traits to both systems
                this.syncBothSystems(result.traits);
                console.log('[PersonalityBridge] Personality loaded successfully');
                return result;
            } else {
                throw new Error('Invalid personality data received');
            }
            
        } catch (error) {
            console.error('[PersonalityBridge] Failed to load personality:', error);
            throw error;
        }
    }
    
    // Utility method to get current streamer ID
    getCurrentStreamerId() {
        // Try to get from global variables
        if (window.STREAMER_ID) return window.STREAMER_ID;
        
        // Try to get from URL path
        const pathMatch = window.location.pathname.match(/\/settings\/([^\/]+)/);
        if (pathMatch) return pathMatch[1];
        
        // Try to get from Alpine store
        if (window.Alpine && Alpine.store('app')) {
            const app = Alpine.store('app');
            if (app.user && app.user.streamerId) {
                return app.user.streamerId;
            }
        }
        
        // Default fallback
        return 'confusedamish';
    }
    
    // Public API for external use
    static getInstance() {
        if (!window.personalityBridge) {
            window.personalityBridge = new PersonalityIntegrationBridge();
        }
        return window.personalityBridge;
    }
}

// Initialize the bridge when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        PersonalityIntegrationBridge.getInstance();
    });
} else {
    PersonalityIntegrationBridge.getInstance();
}

// Export for external use
window.PersonalityIntegrationBridge = PersonalityIntegrationBridge;
// Clean up timers on page unload
if (typeof window !== 'undefined') {
    window.addEventListener('beforeunload', () => {
        if (typeof timerManager !== 'undefined') {
            timerManager.clearAll();
        }
    });
}
