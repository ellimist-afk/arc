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

 * Personality Settings UX Fixes
 * Fixes interaction issues, visual feedback, and component coordination
 * Version: 2025-01-25
 */

(function() {
    'use strict';
    
    console.log('[PersonalityUX] Initializing UX fixes...');
    
    // Wait for DOM and Alpine to be ready
    function initFixes() {
        // Fix 1: Ensure slider visual updates work correctly
        fixSliderVisuals();
        
        // Fix 2: Fix preset button highlighting
        fixPresetButtonStates();
        
        // Fix 3: Fix dropdown interactions
        fixDropdownInteractions();
        
        // Fix 4: Remove blocking overlays
        removeBlockingOverlays();
        
        // Fix 5: Add visual feedback for interactions
        addInteractionFeedback();
        
        // Fix 6: Fix mobile touch interactions
        fixMobileInteractions();
        
        // Fix 7: Improve save feedback
        improveSaveFeedback();
        
        console.log('[PersonalityUX] All fixes applied');
    }
    
    /**
     * Fix 1: Ensure slider visual updates work correctly
     */
    function fixSliderVisuals() {
        // Update slider progress fills on input
        document.addEventListener('input', function(e) {
            if (e.target.classList.contains('tbx-slider-input')) {
                const value = parseFloat(e.target.value);
                const percentage = value * 100;
                
                // Find the progress fill for this slider (use unified system)
                const container = e.target.closest('.tbx-slider-container');
                if (container) {
                    const progressFill = container.querySelector('.slider-progress-fill');
                    if (progressFill) {
                        progressFill.style.width = `${percentage}%`;
                        
                        // Add smooth transition
                        progressFill.style.transition = 'width 0.15s ease-out';
                    }
                    
                    // Update value display
                    const valueDisplay = container.closest('.tbx-trait-slider')?.querySelector('.tbx-trait-value');
                    if (valueDisplay) {
                        valueDisplay.textContent = `${Math.round(percentage)}%`;
                        
                        // Add pulse animation for feedback
                        valueDisplay.style.animation = 'pulse 0.3s ease';
                        timerManager.setTimeout(() => {
                            valueDisplay.style.animation = '';
                        }, 300);
                    }
                }
            }
        });
        
        // Initialize all sliders on page load
        document.querySelectorAll('.tbx-slider-input').forEach(slider => {
            const value = parseFloat(slider.value || 0);
            const percentage = value * 100;
            
            const container = slider.closest('.tbx-slider-container');
            if (container) {
                const progressFill = container.querySelector('.slider-progress-fill');
                if (progressFill) {
                    progressFill.style.width = `${percentage}%`;
                }
            }
        });
        
        console.log('[PersonalityUX] Slider visuals fixed');
    }
    
    /**
     * Fix 2: Fix preset button highlighting
     */
    function fixPresetButtonStates() {
        // Listen for preset changes
        document.addEventListener('preset:applied', function(e) {
            const appliedPreset = e.detail?.preset;
            if (!appliedPreset) return;
            
            console.log('[PersonalityUX] Updating preset buttons for:', appliedPreset);
            
            // Remove active state from all preset cards
            document.querySelectorAll('.tbx-preset-card').forEach(card => {
                card.classList.remove('tbx-preset-active');
                
                // Remove active indicator
                const indicator = card.querySelector('.tbx-preset-active-indicator');
                if (indicator && !card.querySelector(`[x-show*="activePreset"]`)) {
                    indicator.style.display = 'none';
                }
            });
            
            // Add active state to the selected preset
            const activeCard = document.querySelector(`[data-preset="${appliedPreset}"]`);
            if (activeCard) {
                activeCard.classList.add('tbx-preset-active');
                
                // Show active indicator
                const indicator = activeCard.querySelector('.tbx-preset-active-indicator');
                if (indicator) {
                    indicator.style.display = 'flex';
                }
            }
        });
        
        // Fix custom preset card state
        document.addEventListener('trait:changed', function() {
            const customCard = document.querySelector('.tbx-custom-preset-card');
            if (customCard) {
                // Check if we should show custom as active
                const isCustomActive = !document.querySelector('.tbx-preset-card.tbx-preset-active:not(.tbx-custom-preset-card)');
                if (isCustomActive) {
                    customCard.classList.add('tbx-preset-active');
                }
            }
        });
        
        console.log('[PersonalityUX] Preset button states fixed');
    }
    
    /**
     * Fix 3: Fix dropdown interactions
     */
    function fixDropdownInteractions() {
        const dropdown = document.querySelector('.tbx-preset-dropdown');
        if (!dropdown) return;
        
        // Ensure dropdown closes when clicking outside
        document.addEventListener('click', function(e) {
            if (!dropdown.contains(e.target) && dropdown.hasAttribute('open')) {
                dropdown.removeAttribute('open');
            }
        });
        
        // Add keyboard navigation
        dropdown.addEventListener('keydown', function(e) {
            if (e.key === 'Escape' && dropdown.hasAttribute('open')) {
                dropdown.removeAttribute('open');
                dropdown.querySelector('summary')?.focus();
            }
        });
        
        // Fix dropdown arrow rotation
        const summary = dropdown.querySelector('summary');
        if (summary) {
            const icon = summary.querySelector('.tbx-icon');
            if (icon) {
                // Create MutationObserver to watch for open attribute changes
                const observer = new MutationObserver(function(mutations) {
                    mutations.forEach(function(mutation) {
                        if (mutation.attributeName === 'open') {
                            if (dropdown.hasAttribute('open')) {
                                icon.style.transform = 'rotate(180deg)';
                            } else {
                                icon.style.transform = 'rotate(0deg)';
                            }
                        }
                    });
                });
                
                observer.observe(dropdown, { attributes: true });
            }
        }
        
        console.log('[PersonalityUX] Dropdown interactions fixed');
    }
    
    /**
     * Fix 4: Remove blocking overlays
     */
    function removeBlockingOverlays() {
        // Find and remove empty fixed overlays
        document.querySelectorAll('.fixed.inset-0').forEach(element => {
            if (!element.children.length && !element.textContent.trim()) {
                console.log('[PersonalityUX] Removing empty blocking overlay');
                element.style.display = 'none';
                element.style.pointerEvents = 'none';
            }
        });
        
        // Fix x-cloak elements
        document.querySelectorAll('[x-cloak]').forEach(element => {
            // If Alpine has loaded, remove x-cloak
            if (window.Alpine) {
                element.removeAttribute('x-cloak');
            }
        });
        
        // Ensure main content is interactive
        const mainContent = document.querySelector('.tbx-settings-content');
        if (mainContent) {
            mainContent.style.position = 'relative';
            mainContent.style.zIndex = '1';
            mainContent.style.pointerEvents = 'auto';
        }
        
        console.log('[PersonalityUX] Blocking overlays removed');
    }
    
    /**
     * Fix 5: Add visual feedback for interactions
     */
    function addInteractionFeedback() {
        // Add ripple effect to buttons
        document.querySelectorAll('.tbx-button, .tbx-preset-card').forEach(button => {
            button.addEventListener('click', function(e) {
                // Create ripple element
                const ripple = document.createElement('span');
                ripple.className = 'ripple';
                ripple.style.cssText = `
                    position: absolute;
                    border-radius: 50%;
                    background: rgba(255, 255, 255, 0.5);
                    pointer-events: none;
                    transform: scale(0);
                    animation: ripple 0.6s ease-out;
                `;
                
                // Position ripple at click location
                const rect = button.getBoundingClientRect();
                const size = Math.max(rect.width, rect.height);
                const x = e.clientX - rect.left - size / 2;
                const y = e.clientY - rect.top - size / 2;
                
                ripple.style.width = ripple.style.height = size + 'px';
                ripple.style.left = x + 'px';
                ripple.style.top = y + 'px';
                
                button.style.position = 'relative';
                button.style.overflow = 'hidden';
                button.appendChild(ripple);
                
                // Remove ripple after animation
                timerManager.setTimeout(() => ripple.remove(), 600);
            });
        });
        
        // Add hover feedback to interactive elements
        document.querySelectorAll('.tbx-trait-slider').forEach(slider => {
            slider.addEventListener('mouseenter', function() {
                slider.style.transform = 'translateY(-1px)';
                slider.style.transition = 'transform 0.2s ease';
            });
            
            slider.addEventListener('mouseleave', function() {
                slider.style.transform = 'translateY(0)';
            });
        });
        
        console.log('[PersonalityUX] Interaction feedback added');
    }
    
    /**
     * Fix 6: Fix mobile touch interactions
     */
    function fixMobileInteractions() {
        // Detect touch device
        const isTouchDevice = 'ontouchstart' in window;
        
        if (isTouchDevice) {
            // Add touch feedback class
            document.body.classList.add('touch-device');
            
            // Improve slider touch targets
            document.querySelectorAll('.tbx-slider-input').forEach(slider => {
                // Touch target handled by CSS now for proper alignment
                // Removed inline styles that were causing misalignment
                slider.classList.add('touch-optimized');
            });
            
            // Add touch feedback to buttons
            document.querySelectorAll('.tbx-button, .tbx-preset-card').forEach(element => {
                element.addEventListener('touchstart', function() {
                    element.classList.add('touch-active');
                });
                
                element.addEventListener('touchend', function() {
                    timerManager.setTimeout(() => {
                        element.classList.remove('touch-active');
                    }, 200);
                });
            });
        }
        
        console.log('[PersonalityUX] Mobile interactions fixed');
    }
    
    /**
     * Fix 7: Improve save feedback
     */
    function improveSaveFeedback() {
        // Listen for save events
        document.addEventListener('settings:saved', function() {
            // Show success feedback
            showSaveSuccess();
        });
        
        document.addEventListener('settings:error', function(e) {
            // Show error feedback
            showSaveError(e.detail?.message || 'Failed to save settings');
        });
        
        // Create save status container if it doesn't exist
        if (!document.querySelector('.tbx-save-status')) {
            const statusContainer = document.createElement('div');
            statusContainer.className = 'tbx-save-status';
            statusContainer.style.cssText = `
                position: fixed;
                bottom: 2rem;
                right: 2rem;
                z-index: 100;
                pointer-events: none;
            `;
            document.body.appendChild(statusContainer);
        }
        
        console.log('[PersonalityUX] Save feedback improved');
    }
    
    /**
     * Show save success message
     */
    function showSaveSuccess() {
        const container = document.querySelector('.tbx-save-status');
        if (!container) return;
        
        const message = document.createElement('div');
        message.className = 'tbx-status-item tbx-status-success';
        message.innerHTML = `
            <svg class="tbx-icon" style="width: 20px; height: 20px;" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
            </svg>
            <span>Settings saved successfully</span>
        `;
        message.style.cssText = `
            pointer-events: auto;
            padding: 0.75rem 1.25rem;
            border-radius: 8px;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            background: #22c55e;
            color: white;
            font-weight: 500;
            box-shadow: 0 4px 12px rgba(34, 197, 94, 0.3);
            animation: slideInRight 0.3s ease;
            margin-bottom: 0.5rem;
        `;
        
        container.appendChild(message);
        
        // Remove after 3 seconds
        timerManager.setTimeout(() => {
            message.style.animation = 'slideOutRight 0.3s ease';
            timerManager.setTimeout(() => message.remove(), 300);
        }, 3000);
    }
    
    /**
     * Show save error message
     */
    function showSaveError(errorMessage) {
        const container = document.querySelector('.tbx-save-status');
        if (!container) return;
        
        const message = document.createElement('div');
        message.className = 'tbx-status-item tbx-status-error';
        message.innerHTML = `
            <svg class="tbx-icon" style="width: 20px; height: 20px;" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
            </svg>
            <span>${errorMessage}</span>
        `;
        message.style.cssText = `
            pointer-events: auto;
            padding: 0.75rem 1.25rem;
            border-radius: 8px;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            background: #ef4444;
            color: white;
            font-weight: 500;
            box-shadow: 0 4px 12px rgba(239, 68, 68, 0.3);
            animation: slideInRight 0.3s ease;
            margin-bottom: 0.5rem;
        `;
        
        container.appendChild(message);
        
        // Remove after 5 seconds
        timerManager.setTimeout(() => {
            message.style.animation = 'slideOutRight 0.3s ease';
            timerManager.setTimeout(() => message.remove(), 300);
        }, 5000);
    }
    
    // Add animation styles if not already present
    if (!document.getElementById('personality-ux-animations')) {
        const style = document.createElement('style');
        style.id = 'personality-ux-animations';
        style.textContent = `
            @keyframes ripple {
                to {
                    transform: scale(4);
                    opacity: 0;
                }
            }
            
            @keyframes pulse {
                0%, 100% { transform: scale(1); }
                50% { transform: scale(1.1); }
            }
            
            @keyframes slideInRight {
                from {
                    transform: translateX(100%);
                    opacity: 0;
                }
                to {
                    transform: translateX(0);
                    opacity: 1;
                }
            }
            
            @keyframes slideOutRight {
                from {
                    transform: translateX(0);
                    opacity: 1;
                }
                to {
                    transform: translateX(100%);
                    opacity: 0;
                }
            }
            
            .touch-active {
                transform: scale(0.98);
                opacity: 0.9;
            }
        `;
        document.head.appendChild(style);
    }
    
    // Initialize fixes when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initFixes);
    } else {
        // DOM already loaded
        initFixes();
    }
    
    // Also reinitialize when Alpine initializes (for dynamic content)
    if (window.Alpine) {
        document.addEventListener('alpine:initialized', initFixes);
    }
    
})();
// Clean up timers on page unload
if (typeof window !== 'undefined') {
    window.addEventListener('beforeunload', () => {
        if (typeof timerManager !== 'undefined') {
            timerManager.clearAll();
        }
    });
}
