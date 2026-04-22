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

 * Unified Slider System JavaScript
 * Handles ALL slider types in TalkBot with consistent behavior
 * Compatible with Alpine.js and works with Tailwind CDN
 */

(function() {
    'use strict';
    
    console.log('[Unified Slider System] Initializing...');
    
    // Configuration for different slider types
    const SLIDER_CONFIG = {
        personality: {
            warmth: { color: '#ef4444', label: 'Warmth' },
            energy: { color: '#f59e0b', label: 'Energy' },
            humor: { color: '#10b981', label: 'Humor' },
            sass: { color: '#8b5cf6', label: 'Sass' },
            verbosity: { color: '#06b6d4', label: 'Verbosity' }
        },
        deadAir: {
            color: '#3b82f6',
            formatValue: (value) => {
                const minutes = Math.floor(value / 60);
                const seconds = value % 60;
                return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;
            }
        },
        visualization: {
            color: '#ec4899'
        },
        system: {
            color: '#6366f1'
        }
    };
    
    class UnifiedSliderSystem {
        constructor() {
            this.sliders = new Map();
            this.initialized = false;
            this.alpineReady = false;
            this.debounceTimers = new Map();
        }
        
        /**
         * Initialize the system
         */
        init() {
            if (this.initialized) return;
            this.initialized = true;
            
            // Wait for both DOM and Alpine.js
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', () => this.onDOMReady());
            } else {
                this.onDOMReady();
            }
            
            // Listen for Alpine.js if it's available
            if (window.Alpine) {
                this.alpineReady = true;
                this.setupAlpineIntegration();
            } else {
                document.addEventListener('alpine:init', () => {
                    this.alpineReady = true;
                    this.setupAlpineIntegration();
                });
            }
        }
        
        /**
         * Handle DOM ready
         */
        onDOMReady() {
            console.log('[Unified Slider System] DOM Ready, initializing sliders...');
            this.initializeAllSliders();
            this.setupMutationObserver();
            
            // Reinitialize after a delay to catch any late-loading content
            timerManager.setTimeout(() => this.initializeAllSliders(), 500);
            timerManager.setTimeout(() => this.initializeAllSliders(), 1500);
        }
        
        /**
         * Setup Alpine.js integration
         */
        setupAlpineIntegration() {
            console.log('[Unified Slider System] Alpine.js detected, setting up integration...');
            
            // Re-initialize sliders when Alpine components are ready
            document.addEventListener('alpine:initialized', () => {
                timerManager.setTimeout(() => this.initializeAllSliders(), 100);
            });
            
            // Listen for trait updates
            document.addEventListener('traitsLoaded', () => this.updateAllSliders());
            document.addEventListener('presetApplied', () => this.updateAllSliders());
        }
        
        /**
         * Initialize all sliders on the page
         */
        initializeAllSliders() {
            const sliders = document.querySelectorAll('input[type="range"], .tbx-slider, .range-slider, .tbx-range, .tbx-slider-input');
            console.log(`[Unified Slider System] Found ${sliders.length} sliders`);
            
            sliders.forEach(slider => {
                if (!slider.dataset.unifiedSlider) {
                    this.enhanceSlider(slider);
                }
            });
        }
        
        /**
         * Enhance a single slider
         */
        enhanceSlider(slider) {
            const sliderId = slider.id || `slider-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
            if (!slider.id) slider.id = sliderId;
            
            // Mark as enhanced
            slider.dataset.unifiedSlider = 'true';
            
            // Get or create container
            let container = slider.closest('.slider-container, .tbx-slider-container, .audio-slider-container, .viz-slider-container, .system-slider-container');
            if (!container) {
                container = slider.parentElement;
                if (container && !container.classList.contains('slider-container')) {
                    container.classList.add('slider-container');
                }
            }
            
            // Ensure container is positioned
            if (container && getComputedStyle(container).position === 'static') {
                container.style.position = 'relative';
            }
            
            // Detect slider type
            const sliderType = this.detectSliderType(slider, container);
            
            // Create progress fill
            const progressFill = this.createProgressFill(slider, container, sliderType);
            
            // Create value bubble
            const valueBubble = this.createValueBubble(slider, container, sliderType);
            
            // Store slider data
            const sliderData = {
                element: slider,
                container: container,
                type: sliderType,
                progressFill: progressFill,
                valueBubble: valueBubble,
                min: parseFloat(slider.min) || 0,
                max: parseFloat(slider.max) || 100,
                step: parseFloat(slider.step) || 1
            };
            
            this.sliders.set(sliderId, sliderData);
            
            // Setup event listeners
            this.setupEventListeners(sliderData);
            
            // Initial update
            this.updateSlider(sliderData);
            
            console.log(`[Unified Slider System] Enhanced slider: ${sliderId} (type: ${sliderType})`);
        }
        
        /**
         * Detect the type of slider
         */
        detectSliderType(slider, container) {
            // Check for personality traits via x-model attribute
            const xModel = slider.getAttribute('x-model');
            if (xModel && xModel.includes('traits.')) {
                const trait = xModel.split('.')[1];
                return `personality-${trait}`;
            }
            
            // Check for V2 template traits via data-trait attribute on container
            if (container) {
                const dataTrait = container.getAttribute('data-trait') || container.closest('[data-trait]')?.getAttribute('data-trait');
                if (dataTrait) {
                    return `personality-${dataTrait}`;
                }
            }
            
            // Check container classes
            if (container) {
                if (container.classList.contains('dead-air-slider')) return 'dead-air';
                if (container.classList.contains('viz-slider-container')) return 'visualization';
                if (container.classList.contains('system-slider-container')) return 'system';
                if (container.classList.contains('trait-warmth')) return 'personality-warmth';
                if (container.classList.contains('trait-energy')) return 'personality-energy';
                if (container.classList.contains('trait-humor')) return 'personality-humor';
                if (container.classList.contains('trait-sass')) return 'personality-sass';
                if (container.classList.contains('trait-verbosity')) return 'personality-verbosity';
            }
            
            // Check slider ID
            if (slider.id) {
                if (slider.id.includes('dead-air')) return 'dead-air';
                if (slider.id.includes('link') || slider.id.includes('charge') || slider.id.includes('force')) return 'visualization';
            }
            
            return 'default';
        }
        
        /**
         * Create progress fill element
         */
        createProgressFill(slider, container, sliderType) {
            // Check if progress fill already exists
            let progressFill = container.querySelector('.slider-progress-fill');
            if (!progressFill) {
                progressFill = document.createElement('div');
                progressFill.className = 'slider-progress-fill';
                container.insertBefore(progressFill, slider.nextSibling);
            }
            
            // Set color based on type
            const color = this.getSliderColor(sliderType);
            progressFill.style.background = color;
            
            return progressFill;
        }
        
        /**
         * Create value bubble element
         */
        createValueBubble(slider, container, sliderType) {
            // Check if bubble already exists
            let bubble = container.querySelector('.slider-value-bubble, .tbx-slider-bubble');
            if (!bubble) {
                bubble = document.createElement('div');
                bubble.className = 'slider-value-bubble';
                container.appendChild(bubble);
            }
            
            return bubble;
        }
        
        /**
         * Get color for slider type
         */
        getSliderColor(sliderType) {
            if (sliderType.startsWith('personality-')) {
                const trait = sliderType.replace('personality-', '');
                return SLIDER_CONFIG.personality[trait]?.color || '#9333ea';
            }
            
            if (sliderType === 'dead-air') return SLIDER_CONFIG.deadAir.color;
            if (sliderType === 'visualization') return SLIDER_CONFIG.visualization.color;
            if (sliderType === 'system') return SLIDER_CONFIG.system.color;
            
            return '#9333ea'; // Default purple
        }
        
        /**
         * Get accessible label for slider type
         */
        getSliderLabel(sliderType) {
            if (sliderType.startsWith('personality-')) {
                const trait = sliderType.replace('personality-', '');
                const traitInfo = SLIDER_CONFIG.personality[trait];
                return traitInfo ? `${traitInfo.label} level` : `${trait} slider`;
            }
            
            switch (sliderType) {
                case 'dead-air': 
                    return 'Dead air timeout duration';
                case 'visualization': 
                    return 'Visualization parameter';
                case 'system': 
                    return 'System setting';
                default: 
                    return 'Slider control';
            }
        }
        
        /**
         * Setup event listeners for a slider
         */
        setupEventListeners(sliderData) {
            const { element, container } = sliderData;
            
            // Force enable pointer events
            element.style.pointerEvents = 'auto';
            element.style.zIndex = '100';
            element.style.position = 'relative';
            
            // Clear any existing listeners to prevent duplicates
            const newElement = element.cloneNode(true);
            element.parentNode.replaceChild(newElement, element);
            sliderData.element = newElement;
            
            // Input event for real-time updates
            newElement.addEventListener('input', (e) => {
                console.log(`[Unified Slider System] Input event fired for ${newElement.id}:`, e.target.value);
                this.handleSliderInput(sliderData);
                
                // Dispatch custom event for Alpine.js integration
                newElement.dispatchEvent(new CustomEvent('slider:input', {
                    detail: { value: e.target.value, slider: sliderData }
                }));
            }, { passive: false });
            
            // Change event for final value
            newElement.addEventListener('change', (e) => {
                console.log(`[Unified Slider System] Change event fired for ${newElement.id}:`, e.target.value);
                this.handleSliderChange(sliderData);
                
                // Dispatch custom event for Alpine.js integration
                newElement.dispatchEvent(new CustomEvent('slider:change', {
                    detail: { value: e.target.value, slider: sliderData }
                }));
            }, { passive: false });
            
            // Mouse events for bubble visibility and debug
            newElement.addEventListener('mouseenter', (e) => {
                console.log(`[Unified Slider System] Mouse enter on ${newElement.id}`);
                this.showValueBubble(sliderData);
            });
            
            newElement.addEventListener('mouseleave', () => {
                this.hideValueBubble(sliderData);
            });
            
            newElement.addEventListener('mousedown', (e) => {
                console.log(`[Unified Slider System] Mouse down on ${newElement.id}`, e);
                this.showValueBubble(sliderData);
            });
            
            newElement.addEventListener('focus', () => {
                console.log(`[Unified Slider System] Focus on ${newElement.id}`);
                this.showValueBubble(sliderData);
            });
            
            newElement.addEventListener('blur', () => {
                this.hideValueBubble(sliderData);
            });
            
            // Touch events for mobile - Enhanced for better touch experience
            newElement.addEventListener('touchstart', (e) => {
                console.log(`[Unified Slider System] Touch start on ${newElement.id}`, e);
                // Prevent page scrolling while dragging slider
                e.preventDefault();
                this.showValueBubble(sliderData);
                
                // Add active class for visual feedback
                sliderData.container.classList.add('slider-updating');
            }, { passive: false });
            
            newElement.addEventListener('touchmove', (e) => {
                console.log(`[Unified Slider System] Touch move on ${newElement.id}`, e);
                // Update slider value and visual feedback during drag
                this.handleSliderInput(sliderData);
                
                // Keep bubble visible during drag
                this.showValueBubble(sliderData);
            }, { passive: false });
            
            newElement.addEventListener('touchend', (e) => {
                console.log(`[Unified Slider System] Touch end on ${newElement.id}`);
                // Remove active class
                sliderData.container.classList.remove('slider-updating');
                
                // Hide bubble after delay
                timerManager.setTimeout(() => this.hideValueBubble(sliderData), 1200);
                
                // Trigger change event for final value
                this.handleSliderChange(sliderData);
            });
            
            // Enhanced mobile drag prevention
            newElement.addEventListener('touchcancel', () => {
                sliderData.container.classList.remove('slider-updating');
                this.hideValueBubble(sliderData);
            });
            
            // Keyboard navigation for accessibility
            newElement.addEventListener('keydown', (e) => {
                const { min, max, step } = sliderData;
                const currentValue = parseFloat(newElement.value);
                let newValue = currentValue;
                
                switch(e.key) {
                    case 'ArrowLeft':
                    case 'ArrowDown':
                        e.preventDefault();
                        newValue = Math.max(min, currentValue - step);
                        break;
                    case 'ArrowRight':
                    case 'ArrowUp':
                        e.preventDefault();
                        newValue = Math.min(max, currentValue + step);
                        break;
                    case 'Home':
                        e.preventDefault();
                        newValue = min;
                        break;
                    case 'End':
                        e.preventDefault();
                        newValue = max;
                        break;
                    case 'PageDown':
                        e.preventDefault();
                        newValue = Math.max(min, currentValue - (step * 10));
                        break;
                    case 'PageUp':
                        e.preventDefault();
                        newValue = Math.min(max, currentValue + (step * 10));
                        break;
                }
                
                if (newValue !== currentValue) {
                    newElement.value = newValue;
                    this.handleSliderInput(sliderData);
                    this.showValueBubble(sliderData);
                    
                    // Hide bubble after short delay for keyboard navigation
                    timerManager.setTimeout(() => this.hideValueBubble(sliderData), 800);
                }
            });
            
            // Debug click event
            newElement.addEventListener('click', (e) => {
                console.log(`[Unified Slider System] Click on ${newElement.id}`, e);
            });
            
            console.log(`[Unified Slider System] Event listeners attached to ${newElement.id}`, {
                disabled: newElement.disabled,
                readonly: newElement.readOnly,
                pointerEvents: getComputedStyle(newElement).pointerEvents,
                zIndex: getComputedStyle(newElement).zIndex,
                position: getComputedStyle(newElement).position
            });
        }
        
        /**
         * Handle slider input event
         */
        handleSliderInput(sliderData) {
            this.updateSlider(sliderData);
            this.showValueBubble(sliderData);
        }
        
        /**
         * Handle slider change event
         */
        handleSliderChange(sliderData) {
            this.updateSlider(sliderData);
            timerManager.setTimeout(() => this.hideValueBubble(sliderData), 1000);
        }
        
        /**
         * Update slider visual state
         */
        updateSlider(sliderData) {
            const { element, progressFill, valueBubble, min, max, type } = sliderData;
            const value = parseFloat(element.value);
            
            // Calculate percentage
            const percentage = ((value - min) / (max - min)) * 100;
            const clampedPercentage = Math.max(0, Math.min(100, percentage));
            
            // Update progress fill
            if (progressFill) {
                progressFill.style.width = `${clampedPercentage}%`;
            }
            
            // Update value bubble
            if (valueBubble) {
                // Format value based on type
                let displayValue = value;
                if (type === 'dead-air' && SLIDER_CONFIG.deadAir.formatValue) {
                    displayValue = SLIDER_CONFIG.deadAir.formatValue(value);
                } else if (type.startsWith('personality-')) {
                    displayValue = `${Math.round(value * 100)}%`;
                } else if (min === 0 && max === 1) {
                    displayValue = `${Math.round(value * 100)}%`;
                } else {
                    displayValue = Math.round(value);
                }
                
                valueBubble.textContent = displayValue;
                valueBubble.style.left = `${clampedPercentage}%`;
            }
            
            // Update ARIA attributes for accessibility
            element.setAttribute('aria-valuenow', value);
            element.setAttribute('aria-valuemin', min);
            element.setAttribute('aria-valuemax', max);
            element.setAttribute('aria-valuetext', valueBubble?.textContent || value);
            
            // Add descriptive role and labels
            if (!element.getAttribute('role')) {
                element.setAttribute('role', 'slider');
            }
            
            // Add label if not present
            if (!element.getAttribute('aria-label') && !element.getAttribute('aria-labelledby')) {
                const label = this.getSliderLabel(type);
                if (label) {
                    element.setAttribute('aria-label', label);
                }
            }
        }
        
        /**
         * Show value bubble
         */
        showValueBubble(sliderData) {
            if (sliderData.valueBubble) {
                sliderData.valueBubble.classList.add('active');
                sliderData.valueBubble.style.opacity = '1';
            }
        }
        
        /**
         * Hide value bubble
         */
        hideValueBubble(sliderData) {
            if (sliderData.valueBubble) {
                sliderData.valueBubble.classList.remove('active');
                sliderData.valueBubble.style.opacity = '0';
            }
        }
        
        /**
         * Update all sliders (useful after data changes)
         */
        updateAllSliders() {
            this.sliders.forEach(sliderData => {
                this.updateSlider(sliderData);
            });
        }
        
        /**
         * Setup mutation observer for dynamic content
         */
        setupMutationObserver() {
            const observer = new MutationObserver((mutations) => {
                mutations.forEach((mutation) => {
                    mutation.addedNodes.forEach((node) => {
                        if (node.nodeType === 1) { // Element node
                            const sliders = node.querySelectorAll ? 
                                node.querySelectorAll('input[type="range"]') : [];
                            sliders.forEach(slider => {
                                if (!slider.dataset.unifiedSlider) {
                                    this.enhanceSlider(slider);
                                }
                            });
                            
                            // Also check if the node itself is a slider
                            if (node.tagName === 'INPUT' && node.type === 'range' && !node.dataset.unifiedSlider) {
                                this.enhanceSlider(node);
                            }
                        }
                    });
                });
            });
            
            observer.observe(document.body, { 
                childList: true, 
                subtree: true,
                attributes: false 
            });
        }
        
        /**
         * Destroy the slider system (cleanup)
         */
        destroy() {
            this.sliders.forEach(sliderData => {
                // Remove event listeners
                const { element } = sliderData;
                element.replaceWith(element.cloneNode(true));
            });
            
            this.sliders.clear();
            this.initialized = false;
        }
    }
    
    // Initialize the system
    const sliderSystem = new UnifiedSliderSystem();
    sliderSystem.init();
    
    // Expose globally for debugging
    window.UnifiedSliderSystem = sliderSystem;
    
    // Also provide a manual refresh function
    window.refreshSliders = () => {
        console.log('[Unified Slider System] Manual refresh triggered');
        sliderSystem.initializeAllSliders();
    };
    
    console.log('[Unified Slider System] Ready. Use window.refreshSliders() to manually refresh.');
    
})();
// Clean up timers on page unload
if (typeof window !== 'undefined') {
    window.addEventListener('beforeunload', () => {
        if (typeof timerManager !== 'undefined') {
            timerManager.clearAll();
        }
    });
}
