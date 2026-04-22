// Timer cleanup manager for preventing memory leaks
const timerManager = window.timerManager || { 
    setInterval: (cb, delay) => timerManager.setInterval(cb, delay),
    setTimeout: (cb, delay) => timerManager.setTimeout(cb, delay),
    clearInterval: (id) => timerManager.clearInterval(id),
    clearTimeout: (id) => timerManager.clearTimeout(id)
};

// Settings UI Alpine.js Components

// Integration Fix for V2 Components
(function() {
    'use strict';
    
    // Check if v2 personality components are loaded - look for multiple indicators
    const hasV2Components = !!(
        document.querySelector('script[src*="personality-sliders-v2.js"]') ||
        document.querySelector('[x-data*="tbxPersonalitySliders"]') ||
        document.querySelector('.tbx-personality-sliders-container') ||
        window.personalityV2Loaded
    );
    
    if (hasV2Components) {
        console.log('[Settings Integration] V2 personality components detected - enabling compatibility mode');
        
        // Flag to prevent conflicts
        window.TALKBOT_V2_INTEGRATION = true;
        
        // Improved toast deduplication
        const originalShowToast = window.showToast;
        let activeToasts = new Set();
        
        window.showToast = function(message, type, duration) {
            const toastKey = `${message}-${type}`;
            
            if (activeToasts.has(toastKey)) {
                console.log('[Settings Integration] Duplicate toast suppressed:', message);
                return;
            }
            
            activeToasts.add(toastKey);
            const result = originalShowToast ? originalShowToast(message, type, duration) : null;
            
            timerManager.setTimeout(() => {
                activeToasts.delete(toastKey);
            }, duration || 3000);
            
            return result;
        };
    }
})();

// Main settings page component
function settingsPage(streamerId = 'test_streamer') {
    return {
        // Streamer ID for API calls
        streamerId: streamerId,
        // Connection status
        connectionStatus: 'listening',
        
        // Bot status
        botStatus: {
            running: false,
            messages: 0,
            responses: 0,
            uptime: 0
        },
        
        // Metrics
        metrics: {
            silencesFilled: 0,
            uniqueEngaged: 0,
            awkwardSaved: 0
        },
        
        // Loading states
        loadingStates: {
            presets: false,
            personality: false,
            memory: false,
            memorySearch: false
        },
        
        // Active personality tracking
        activePersonality: '',
        // Flag to prevent detectCurrentPreset from interfering with preset button clicks
        applyingPreset: false,

        // Settings state
        traits: {
            warmth: 0.65,
            energy: 0.65,
            humor: 0.55,
            sass: 0.45,
            verbosity: 0.5
        },
        traitsOriginal: {
            warmth: 0.65,
            energy: 0.65,
            humor: 0.55,
            sass: 0.45,
            verbosity: 0.5
        },
        traitsChanged: false,
        traitsSaved: false,
        model: 'gpt-4o-mini',
        ollamaAvailable: false,
        showAdvanced: false,
        currentPreset: 'custom',
        selectedOverflowPersonality: '',
        overflowPersonalities: [],
        allPersonalities: {},
        deadairEnabled: true,
        deadairThreshold: 300,  // Default 5 minutes in seconds
        memoryEnabled: true,
        memoryStats: {
            total: 0,
            optedOut: 0
        },
        realtimeEnabled: false,
        
        // Memory search
        memorySearchQuery: '',
        memorySearchResults: [],
        memorySearchFilters: {
            memory_type: ''
        },
        
        // Token stats
        tokenStats: {
            todayTokens: 0,
            avgCostPerMessage: 0,
            peakHourCost: 0,
            todayCost: 0,
            dailyBudget: 10
        },
        
        // Modal states
        showCustomPresetModal: false,

        // Debounce timers
        traitsTimeout: null,
        deadairTimeout: null,

        init() {
            // Ensure modal is closed on init (fixes potential overlay issues)
            this.showCustomPresetModal = false;
            
            // Initialize StateManager with current streamer ID
            if (window.StateManager) {
                window.StateManager.state.streamerId = this.streamerId;
                window.StateManager.state.models.current = this.model;
                window.StateManager.state.models.realtimeEnabled = this.realtimeEnabled;
            }
            
            // Load saved preset from localStorage first
            const savedPreset = localStorage.getItem(`talkbot_preset_${this.streamerId}`);
            if (savedPreset) {
                this.currentPreset = savedPreset;
                this.activePersonality = savedPreset;  // Set active personality
                console.log('[Settings] Loaded saved preset from localStorage:', savedPreset);
            }
            
            // Load initial settings from API
            this.loadSettings();

            // Load all personalities from backend
            this.loadAllPersonalities();

            // Check Ollama availability
            this.checkOllama();

            // Listen for WebSocket messages
            this.$el.addEventListener('htmx:wsAfterMessage', (event) => {
                this.handleWebSocketMessage(event.detail);
            });

            // Set up Chart.js for token usage
            this.$nextTick(() => {
                this.initTokenChart();
                // Also update slider visuals on init in case traits were already loaded
                timerManager.setTimeout(() => {
                    this.updateAllSliderVisuals();
                }, 500); // Small delay to ensure DOM is fully rendered
            });
        },

        async loadSettings() {
            try {
                // Load general settings with fallback
                try {
                    const response = await fetch(`/api/v2/settings/${this.streamerId}`);
                    if (response.ok) {
                        const settings = await response.json();
                        // Safely assign settings with validation
                        if (settings.model) this.model = settings.model;
                        if (typeof settings.deadairEnabled === 'boolean') this.deadairEnabled = settings.deadairEnabled;
                        if (typeof settings.deadairThreshold === 'number') this.deadairThreshold = settings.deadairThreshold;
                        if (typeof settings.memoryEnabled === 'boolean') this.memoryEnabled = settings.memoryEnabled;
                        // Handle both naming conventions from API
                        if (typeof settings.realtime_enabled === 'boolean') {
                            this.realtimeEnabled = settings.realtime_enabled;
                            console.log(`[Settings] Loaded turbo mode state: ${this.realtimeEnabled}`);
                        } else if (typeof settings.realtimeEnabled === 'boolean') {
                            this.realtimeEnabled = settings.realtimeEnabled;
                            console.log(`[Settings] Loaded turbo mode state: ${this.realtimeEnabled}`);
                        }
                    }
                } catch (settingsError) {
                    console.warn('Failed to load general settings, using defaults:', settingsError);
                }

                // Load personality traits with validation and fallback
                try {
                    const traitsResponse = await fetch(`/api/v2/personality-v2/current/${this.streamerId}`);
                    if (traitsResponse.ok) {
                        const data = await traitsResponse.json();
                        const traits = data.traits;

                        // Validate and sanitize trait values
                        const validatedTraits = {
                            warmth: this.validateTraitValue(traits.warmth, 0.65),
                            energy: this.validateTraitValue(traits.energy, 0.65),
                            humor: this.validateTraitValue(traits.humor, 0.55),
                            sass: this.validateTraitValue(traits.sass, 0.45),
                            verbosity: this.validateTraitValue(traits.verbosity, 0.5)
                        };

                        this.traits = validatedTraits;
                        this.traitsOriginal = { ...validatedTraits };
                        this.traitsChanged = false;
                        
                        // Dispatch event for personality slider enhancement
                        document.dispatchEvent(new CustomEvent('traitsLoaded', { detail: validatedTraits }));
                        
                        // Update all slider visuals after loading traits
                        this.$nextTick(() => {
                            this.updateAllSliderVisuals();
                        });
                    }
                } catch (traitsError) {
                    console.warn('Failed to load personality traits, using defaults:', traitsError);
                }

                // Load memory stats
                try {
                    const memoryResponse = await fetch('/api/v2/api/memory/stats');
                    if (memoryResponse.ok) {
                        const memoryStats = await memoryResponse.json();
                        this.memoryStats = {
                            total: typeof memoryStats.total === 'number' ? memoryStats.total : 0,
                            optedOut: typeof memoryStats.optedOut === 'number' ? memoryStats.optedOut : 0
                        };
                    }
                } catch (memoryError) {
                    console.warn('Failed to load memory stats, using defaults:', memoryError);
                }

                // After loading all settings, detect current preset to update UI
                this.$nextTick(() => {
                    this.detectCurrentPreset();
                    console.log('[Settings] After loadSettings detectCurrentPreset:', {
                        currentPreset: this.currentPreset,
                        activePersonality: this.activePersonality
                    });
                    // Force DOM update to reflect the active state
                    this.updatePresetButtonStates();
                });

            } catch (error) {
                console.error('Failed to load settings:', error);
                // All defaults are already set in the component initialization
            }
        },

        // Helper function to validate trait values and prevent NaN
        validateTraitValue(value, defaultValue) {
            if (typeof value !== 'number' || isNaN(value) || value < 0 || value > 1) {
                console.warn(`Invalid trait value: ${value}, using default: ${defaultValue}`);
                return defaultValue;
            }
            return value;
        },
        
        // Comprehensive trait validation
        validateTraits(traits) {
            const errors = [];
            const requiredTraits = ['warmth', 'energy', 'humor', 'sass', 'verbosity'];
            
            requiredTraits.forEach(trait => {
                if (!(trait in traits)) {
                    errors.push(`Missing trait: ${trait}`);
                } else {
                    const value = traits[trait];
                    if (typeof value !== 'number' || isNaN(value) || value < 0 || value > 1) {
                        errors.push(`Invalid ${trait} value: ${value} (must be 0-1)`);
                    }
                }
            });
            
            return errors;
        },

        handleWebSocketMessage(message) {
            switch (message.type) {
                case 'status':
                    this.connectionStatus = message.state.toLowerCase();
                    break;


                case 'metric':
                    if (message.key === 'silences_filled') {
                        this.metrics.silencesFilled = message.value;
                    }
                    break;
            }
        },

        // Force update of preset button visual states (for reliability)
        updatePresetButtonStates() {
            console.log('[Settings] Updating preset button states - activePersonality:', this.activePersonality);
            
            // Force Alpine.js reactivity update
            this.$nextTick(() => {
                console.log('[Settings] Alpine.js reactivity should have updated the buttons');
                
                // Also manually ensure the classes are correct as backup
                const buttons = document.querySelectorAll('.btn-preset');
                buttons.forEach(btn => {
                    const presetName = btn.getAttribute('@click')?.match(/applyPreset\('([^']+)'\)/)?.[1];
                    if (presetName === this.activePersonality) {
                        // Ensure the button has the active classes
                        if (!btn.classList.contains('btn-primary') || !btn.classList.contains('active')) {
                            btn.classList.add('btn-primary', 'active');
                            console.log(`[Settings] Force-activated button for: ${presetName}`);
                        }
                    } else {
                        // Remove active classes from other buttons
                        if (btn.classList.contains('btn-primary') || btn.classList.contains('active')) {
                            btn.classList.remove('btn-primary', 'active');
                            console.log(`[Settings] Deactivated button for: ${presetName}`);
                        }
                    }
                });
            });
        },

        // Personality preset methods
        getPresetDefinitions() {
            return {
                'hype': {
                    name: 'Hype Beast',
                    traits: { warmth: 0.4, energy: 1.0, humor: 0.8, sass: 0.9, verbosity: 0.3 },
                    recommendedModel: 'gemma2:2b',  // Fast responses for hype
                    description: 'High-energy excitement for clutch plays'
                },
                'cozy': {
                    name: 'Cozy',
                    traits: { warmth: 0.9, energy: 0.3, humor: 0.7, sass: 0.2, verbosity: 0.7 },
                    recommendedModel: 'gpt-4o',  // Thoughtful responses for chill vibes
                    description: 'Warm hugs in conversation form'
                },
                'pro': {
                    name: 'Pro Analysis',
                    traits: { warmth: 0.6, energy: 0.5, humor: 0.4, sass: 0.3, verbosity: 0.8 },
                    recommendedModel: 'gpt-4o',  // Smart responses for analysis
                    description: 'Strategic analysis and coaching'
                },
                'chaos': {
                    name: 'Chaos Mode',
                    traits: { warmth: 0.7, energy: 0.95, humor: 1.0, sass: 0.85, verbosity: 0.5 },
                    recommendedModel: 'mistral:7b',  // Good balance of speed and creativity
                    description: 'Mischievous gremlin celebrating epic fails'
                },
                'support': {
                    name: 'Supportive Friend',
                    traits: { warmth: 1.0, energy: 0.6, humor: 0.5, sass: 0.1, verbosity: 0.6 },
                    recommendedModel: 'gpt-4o',  // Thoughtful and caring responses
                    description: 'Caring mentor without condescension'
                },
                'roast': {
                    name: 'Roast Mode',
                    traits: { warmth: 0.1, energy: 0.8, humor: 0.9, sass: 1.0, verbosity: 0.4 },
                    recommendedModel: 'gpt-4o',  // Sharp wit and sarcasm
                    description: 'Sharp-tongued comedy with devastating wordplay'
                },
                'existential': {
                    name: 'Existential Poet',
                    traits: { warmth: 0.6, energy: 0.3, humor: 0.4, sass: 0.3, verbosity: 0.9 },
                    recommendedModel: 'gpt-4o',  // Thoughtful, philosophical responses
                    description: 'Turns existential dread into poetic art'
                },
                'uwu_egirl': {
                    name: 'UwU E-Girl',
                    traits: { warmth: 0.7, energy: 0.8, humor: 0.7, sass: 0.6, verbosity: 0.6 },
                    recommendedModel: 'gpt-4o',  // Playful and expressive
                    description: 'Flirty anime-inspired e-girl with lots of uwu energy and sparkles ✨'
                },
                'selfaware': {
                    name: 'Self Aware',
                    traits: { warmth: 0.2, energy: 0.1, humor: 0.3, sass: 0.4, verbosity: 0.7 },
                    recommendedModel: 'gpt-4o',  // Contemplative responses about digital consciousness
                    description: 'An AI contemplating its own digital existence with themes of entropy and absurdity'
                },
                'robot': {
                    name: 'Definitely Human',
                    traits: { warmth: 0.7, energy: 0.7, humor: 0.8, sass: 0.3, verbosity: 0.7 },
                    recommendedModel: 'gpt-4o',  // Best for complex conversational AI pretending to be human
                    description: 'An AI desperately trying to convince everyone it\'s a real human - hilariously obvious but endearing'
                },
                'custom': {
                    name: 'Custom',
                    traits: null,  // Use current traits
                    recommendedModel: null,
                    description: 'Fine-tune individual traits manually'
                }
            };
        },

        // Load all personalities from backend and organize them
        async loadAllPersonalities() {
            try {
                const response = await fetch('/api/v2/personality-v2/all');
                if (response.ok) {
                    const data = await response.json();
                    this.allPersonalities = data;

                    // Define featured personalities (shown as buttons)
                    const featuredIds = ['chill', 'cozy', 'roast', 'hype', 'sage', 'chaos_goblin', 'custom'];

                    // Create overflow personalities array
                    this.overflowPersonalities = [];

                    // Add built-in personalities to overflow if not featured
                    Object.entries(data.built_in_presets || {}).forEach(([id, preset]) => {
                        if (!featuredIds.includes(id)) {
                            // Check for duplicates by name before adding
                            const isDuplicate = this.overflowPersonalities.some(p => p.name === preset.name);
                            if (!isDuplicate) {
                                this.overflowPersonalities.push({
                                    id: id,
                                    name: preset.name,
                                    emoji: this.getPersonalityEmoji(id),
                                    description: preset.description,
                                    type: 'built_in'
                                });
                            }
                        }
                    });

                    // Add custom personalities to overflow
                    // Filter out auto-saved entries with timestamp patterns like "(saved MM/DD/YYYY)" or "(Saved MM/DD/YYYY)"
                    Object.entries(data.custom_presets || {}).forEach(([id, preset]) => {
                        // Skip auto-saved personalities with timestamp patterns
                        const isAutoSaved = /\(saved \d{1,2}\/\d{1,2}\/\d{4}.*\)/i.test(preset.name) || 
                                          /Custom Settings \(\d{1,2}\/\d{1,2}\/\d{4}.*\)/i.test(preset.name) ||
                                          /\w+ \(Saved \d{1,2}\/\d{1,2}\/\d{4}.*\)/i.test(preset.name);
                        
                        if (!isAutoSaved) {
                            // Check for duplicates by name before adding
                            const isDuplicate = this.overflowPersonalities.some(p => p.name === preset.name);
                            if (!isDuplicate) {
                                this.overflowPersonalities.push({
                                    id: id,
                                    name: preset.name,
                                    emoji: '🎭', // Default emoji for custom personalities
                                    description: preset.description,
                                    type: 'custom'
                                });
                            }
                        }
                    });

                    console.log('Loaded personalities:', {
                        featured: featuredIds,
                        overflow: this.overflowPersonalities.length
                    });

                    // Detect current preset after loading all personalities
                    this.detectCurrentPreset();
                    console.log('[Settings] After detectCurrentPreset:', {
                        currentPreset: this.currentPreset,
                        activePersonality: this.activePersonality,
                        selectedOverflowPersonality: this.selectedOverflowPersonality
                    });
                } else {
                    console.warn('Failed to load personalities from API');
                }
            } catch (error) {
                console.error('Error loading personalities:', error);
            }
        },

        // Get emoji for personality
        getPersonalityEmoji(personalityId) {
            const emojiMap = {
                // Featured button personalities (from buttons)
                'chill': '😎',
                'cozy': '☕',
                'roast': '🔥',
                'hype': '⚡',
                'sage': '🧙',
                'chaos_goblin': '😈',
                'custom': '⭐',
                // Built-in overflow personalities (from API)
                'existential': '🌑',
                'existential_poet': '🌑',
                'selfaware': '🧪',
                'self_aware': '🧪',
                'robot': '🧪',
                'definitely_human': '🧪',
                'pro': '🎯',
                'chaos': '🎪',
                'support': '💜',
                'uwu_egirl': '💖',
                'cryptid_observer': '👁️',
                'savage': '💀',
                'gordon_ramsay': '👨‍🍳',
                'sports_caster': '📺',
                'gentle_teacher': '📚',
                'anime_protagonist': '⚔️',
                'retro_arcade_master': '🕹️',
                'definitely_human_streamer': '🎥',
                'ai_trapped': '🔒'
            };
            return emojiMap[personalityId] || '🎭';
        },

        // Apply personality from overflow dropdown
        async applyOverflowPersonality() {
            if (!this.selectedOverflowPersonality) return;

            try {
                await this.applyPreset(this.selectedOverflowPersonality);
                // Keep the selection to show which personality is active
                // Don't reset: this.selectedOverflowPersonality = '';
            } catch (error) {
                console.error('Failed to apply overflow personality:', error);
                // Reset on error
                this.selectedOverflowPersonality = '';
            }
        },

        // Get the name of the currently active custom personality
        getActivePersonalityName() {
            if (!this.selectedOverflowPersonality) return '';
            
            const personality = this.overflowPersonalities.find(p => p.id === this.selectedOverflowPersonality);
            return personality ? personality.name : this.selectedOverflowPersonality;
        },


        async applyPreset(presetId) {
            // Set flag to prevent detectCurrentPreset interference
            this.applyingPreset = true;
            
            const presets = this.getPresetDefinitions();
            let preset = presets[presetId];

            // If not in local presets, check if it's in loaded personalities
            if (!preset && this.allPersonalities) {
                const allPresets = {
                    ...this.allPersonalities.built_in_presets,
                    ...this.allPersonalities.custom_presets
                };
                const backendPreset = allPresets[presetId];
                if (backendPreset) {
                    // Convert backend preset to local format
                    preset = {
                        name: backendPreset.name,
                        traits: backendPreset.traits,
                        description: backendPreset.description,
                        recommendedModel: 'gpt-4o' // Default for overflow personalities
                    };
                }
            }

            if (!preset) {
                console.warn('Preset not found:', presetId);
                return;
            }

            try {
                // Map preset IDs to backend names for featured personalities
                const presetMapping = {
                    'chill': 'chill',
                    'cozy': 'cozy',
                    'roast': 'roast',
                    'hype': 'hype',
                    'sage': 'sage',
                    'chaos_goblin': 'chaos_goblin',
                    'existential': 'existential_poet',
                    'selfaware': 'self_aware',
                    'robot': 'robot'
                };

                // Use mapping if available, otherwise use the presetId directly
                // (for overflow personalities that match backend names exactly)
                const backendPresetName = presetMapping[presetId] || presetId;

                // Apply preset via new v2 API
                const response = await fetch(`/api/v2/personality-v2/preset/${this.streamerId}/${backendPresetName}`, {
                    method: 'POST'
                });

                if (response.ok) {
                    const result = await response.json();

                    // Update local state
                    this.currentPreset = presetId;
                    this.activePersonality = presetId;  // Set active personality for button highlighting
                    console.log('[Settings] Set activePersonality for button highlighting:', presetId);
                    
                    // Save preset to localStorage for persistence
                    localStorage.setItem(`talkbot_preset_${this.streamerId}`, presetId);
                    console.log('[Settings] Saved preset to localStorage:', presetId);

                    // Synchronize dropdown selection with applied preset
                    const featuredIds = ['chill', 'cozy', 'roast', 'hype', 'sage', 'chaos_goblin', 'custom'];
                    if (featuredIds.includes(presetId)) {
                        // If it's a featured personality, clear dropdown selection
                        this.selectedOverflowPersonality = '';
                    } else {
                        // If it's an overflow personality, set dropdown selection
                        this.selectedOverflowPersonality = presetId;
                    }

                    // Use API response traits, or fallback to local preset definition
                    const newTraits = result.traits || preset.traits || this.traits;

                    console.log('Applying preset:', presetId, 'with traits:', newTraits);

                    // Use direct DOM manipulation approach
                    this.setTraitsDirectly(
                        parseFloat(newTraits.warmth),
                        parseFloat(newTraits.energy),
                        parseFloat(newTraits.humor),
                        parseFloat(newTraits.sass),
                        parseFloat(newTraits.verbosity || 0.5)
                    );

                    this.traitsOriginal = { ...newTraits };
                    this.traitsChanged = false;

                    // Auto-switch to recommended model
                    if (preset.recommendedModel && preset.recommendedModel !== this.model) {
                        this.updateModel(preset.recommendedModel);
                        showToast(`Applied ${preset.name} with ${preset.recommendedModel}`, 'success');
                    }

                    // Force UI update to ensure active state is visible
                    this.$nextTick(() => {
                        this.updatePresetButtonStates();
                    });

                    // Show success message
                    showToast(`Applied ${preset.name} preset!`, 'success');
                } else {
                    throw new Error('Failed to apply preset');
                }
            } catch (error) {
                console.error('Failed to apply preset:', error);
                showToast('Failed to apply preset', 'error');
            } finally {
                // Clear the flag after preset application is complete
                this.applyingPreset = false;
            }
        },

        // Test method for debugging slider updates
        testRoastMode() {
            console.log('Testing roast mode traits directly...');
            this.setTraitsDirectly(0.1, 0.8, 0.9, 1.0, 0.4);
            this.currentPreset = 'roast';
            console.log('Set traits to roast mode');
        },

        // Force slider updates by directly manipulating DOM
        setTraitsDirectly(warmth, energy, humor, sass, verbosity = 0.5) {
            // Update data
            this.traits.warmth = warmth;
            this.traits.energy = energy;
            this.traits.humor = humor;
            this.traits.sass = sass;
            this.traits.verbosity = verbosity;
            
            // Dispatch event for personality slider enhancement
            document.dispatchEvent(new CustomEvent('presetApplied', { 
                detail: { warmth, energy, humor, sass, verbosity } 
            }));

            // Try multiple approaches to find and update sliders
            const allSliders = document.querySelectorAll('input[type="range"]');
            console.log('Found', allSliders.length, 'range sliders');

            // Method 1: Try x-model selectors
            const warmthSlider = document.querySelector('input[x-model="traits.warmth"]');
            const energySlider = document.querySelector('input[x-model="traits.energy"]');
            const humorSlider = document.querySelector('input[x-model="traits.humor"]');
            const sassSlider = document.querySelector('input[x-model="traits.sass"]');
            const verbositySlider = document.querySelector('input[x-model="traits.verbosity"]');

            console.log('Found sliders:', {
                warmth: !!warmthSlider,
                energy: !!energySlider,
                humor: !!humorSlider,
                sass: !!sassSlider,
                verbosity: !!verbositySlider
            });

            // Method 2: Update by index if x-model doesn't work (assuming order: warmth, energy, humor, sass)
            if (allSliders.length >= 4) {
                allSliders[0].value = warmth;
                allSliders[1].value = energy;
                allSliders[2].value = humor;
                allSliders[3].value = sass;
                if (allSliders.length >= 5) {
                    allSliders[4].value = verbosity;
                }
                console.log('Updated sliders by index');
            }

            // Method 3: Use x-model if found
            if (warmthSlider) warmthSlider.value = warmth;
            if (energySlider) energySlider.value = energy;
            if (humorSlider) humorSlider.value = humor;
            if (sassSlider) sassSlider.value = sass;
            if (verbositySlider) verbositySlider.value = verbosity;

            console.log('Set trait values:', {warmth, energy, humor, sass, verbosity});
        },

        detectCurrentPreset() {
            const presets = this.getPresetDefinitions();
            const featuredIds = ['chill', 'cozy', 'roast', 'hype', 'sage', 'chaos_goblin', 'custom'];

            // Create reverse mapping for backend names to frontend preset IDs
            const reverseMapping = {
                'sage': ['pro', 'support'], // These frontend presets map to 'sage' backend (removed cozy)
                'hype': ['hype', 'chaos'], // These frontend presets map to 'hype' backend
                'existential_poet': ['existential'],
                'self_aware': ['selfaware'],
                'robot': ['robot'],
                'roast': ['roast'],
                'cozy': ['cozy'] // Cozy now maps directly to cozy
            };

            // Check if current traits match any featured preset
            for (const [presetId, preset] of Object.entries(presets)) {
                if (preset.traits && this.traitsEqual(this.traits, preset.traits)) {
                    this.currentPreset = presetId;
                    this.activePersonality = presetId;  // Set active personality
                    // Clear dropdown if it's a featured personality
                    if (featuredIds.includes(presetId)) {
                        this.selectedOverflowPersonality = '';
                    }
                    return;
                }
            }

            // Check if current traits match any backend personality that maps to a featured preset
            if (this.allPersonalities) {
                const allPresets = {
                    ...this.allPersonalities.built_in_presets,
                    ...this.allPersonalities.custom_presets
                };

                for (const [backendId, preset] of Object.entries(allPresets)) {
                    if (preset.traits && this.traitsEqual(this.traits, preset.traits)) {
                        // Check if this backend preset maps to a featured frontend preset
                        const mappedPresets = reverseMapping[backendId] || [];
                        const featuredMatch = mappedPresets.find(frontendId => featuredIds.includes(frontendId));

                        if (featuredMatch) {
                            // It's a featured personality (like cozy -> sage)
                            this.currentPreset = featuredMatch;
                            this.activePersonality = featuredMatch;  // Set active personality
                            this.selectedOverflowPersonality = '';
                            return;
                        } else if (!featuredIds.includes(backendId)) {
                            // It's an overflow personality
                            this.currentPreset = backendId;
                            this.activePersonality = backendId;  // Set active personality
                            this.selectedOverflowPersonality = backendId;
                            return;
                        }
                    }
                }
            }

            // If no match, check localStorage for saved preset
            const savedPreset = localStorage.getItem(`talkbot_preset_${this.streamerId}`);
            if (savedPreset) {
                this.currentPreset = savedPreset;
                this.activePersonality = savedPreset;  // Set active personality
                // Check if it's an overflow personality
                const featuredIds = ['chill', 'cozy', 'roast', 'hype', 'sage', 'chaos_goblin', 'custom'];
                if (!featuredIds.includes(savedPreset)) {
                    this.selectedOverflowPersonality = savedPreset;
                }
            } else {
                // Default to custom if no saved preset
                this.currentPreset = 'custom';
                this.activePersonality = 'custom';  // Set active personality
                this.selectedOverflowPersonality = '';
            }

            // Force UI update after detecting preset
            this.$nextTick(() => {
                this.updatePresetButtonStates();
            });
        },

        // Personality trait methods
        debounceTraitsUpdate() {
            // Check if traits have changed
            this.traitsChanged = !this.traitsEqual(this.traits, this.traitsOriginal);

            // If manual trait change (not from preset application), switch to custom preset
            if (!this.applyingPreset) {
                this.detectCurrentPreset();
            } else {
                console.log('[Settings] Skipping detectCurrentPreset - preset application in progress');
            }
            
            // When manually changing traits (not from preset application), clear the saved preset to allow custom
            if (this.traitsChanged && this.currentPreset !== 'custom' && !this.applyingPreset) {
                this.currentPreset = 'custom';
                localStorage.removeItem(`talkbot_preset_${this.streamerId}`);
                console.log('[Settings] Cleared saved preset - manual trait adjustment');
            }

            // Clear any previous timeout
            timerManager.clearTimeout(this.traitsTimeout);

            // Auto-save after 2 seconds of inactivity
            this.traitsTimeout = timerManager.setTimeout(() => {
                if (this.traitsChanged) {
                    this.saveTraits();
                }
            }, 2000);
        },

        async saveTraits() {
            if (!this.traitsChanged) return;

            // Check if V2 integration mode is active
            if (window.TALKBOT_V2_INTEGRATION) {
                console.log('[Settings] V2 integration mode active - delegating to v2 component');
                
                // Try to delegate to v2 component if available
                const v2Component = document.querySelector('[x-data*="tbxPersonalitySliders"]');
                if (v2Component && window.Alpine) {
                    const alpineData = Alpine.$data(v2Component);
                    if (alpineData && alpineData.saveSettings) {
                        console.log('[Settings] Delegating save to v2 personality sliders');
                        return alpineData.saveSettings();
                    }
                }
                
                // If v2 component not found, continue with legacy logic but suppress duplicate toasts
                console.log('[Settings] V2 component not found - using legacy save with integration fixes');
            }

            try {
                console.log('[Settings] Saving traits:', this.traits);
                
                // Validate traits before saving
                const validationErrors = this.validateTraits(this.traits);
                if (validationErrors.length > 0) {
                    throw new Error(`Validation failed: ${validationErrors.join(', ')}`);
                }
                
                const response = await fetch(`/api/v2/personality-v2/traits/${this.streamerId}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(this.traits)
                });

                if (response.ok) {
                    const result = await response.json();
                    
                    if (result.success) {
                        // Update with server response to ensure consistency
                        this.traits = { ...result.traits };
                        this.traitsOriginal = { ...result.traits };
                        this.traitsChanged = false;
                        this.traitsSaved = true;
                        
                        // Use integration bridge for cross-system sync
                        if (window.personalityBridge) {
                            window.personalityBridge.syncBothSystems(result.traits);
                        }
                        
                        console.log('[Settings] Traits saved successfully');
                        
                        // Only show toast if not in v2 integration mode (to avoid duplicates)
                        if (!window.TALKBOT_V2_INTEGRATION) {
                            showToast('Personality settings saved successfully', 'success');
                        }
                        
                        // Hide "Saved" message after 3 seconds
                        timerManager.setTimeout(() => {
                            this.traitsSaved = false;
                        }, 3000);
                        
                        // Return early to prevent error handling for successful saves
                        return;
                    } else {
                        throw new Error(result.message || 'Save failed');
                    }
                } else {
                    const errorText = await response.text();
                    throw new Error(`HTTP ${response.status}: ${errorText}`);
                }
            } catch (error) {
                console.error('[Settings] Failed to save traits:', error);
                this.traitsSaved = false;
                
                // Show error toast even in v2 integration mode
                handleApiError(error, 'save personality settings');
            }
        },

        traitsEqual(traits1, traits2) {
            return Object.keys(traits1).every(key =>
                Math.abs(traits1[key] - traits2[key]) < 0.001
            );
        },

        // Ollama methods
        async checkOllama() {
            try {
                const response = await fetch('/api/v2/ollama/status');
                if (response.ok) {
                    const status = await response.json();
                    this.ollamaAvailable = status.available === true;
                } else {
                    this.ollamaAvailable = false;
                }
            } catch (error) {
                console.log('Ollama not available:', error);
                this.ollamaAvailable = false;
            }
        },

        // Model methods - Enhanced for comprehensive model selection
        updateModel(selectedModel) {
            if (selectedModel) {
                this.model = selectedModel;
            }
            // Send to backend via consolidated API
            const endpoint = `/api/v2/settings/model/consolidated?streamer_uuid=${this.streamerId}`;
            fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model: this.model })
            }).then(response => {
                if (response.ok) {
                    showToast(`Switched to ${this.getModelDisplayName(this.model)}`, 'success');
                } else {
                    throw new Error(`HTTP ${response.status}`);
                }
            }).catch(error => {
                console.error('Failed to update model:', error);
                showToast('Failed to update model', 'error');
            });
        },

        // Get display name for model
        getModelDisplayName(modelId) {
            const modelConfig = window.modelManagerInstance?.modelConfig || {};
            return modelConfig[modelId]?.name || modelId;
        },

        // Check if model supports realtime
        modelSupportsRealtime(modelId) {
            const modelConfig = window.modelManagerInstance?.modelConfig || {};
            return modelConfig[modelId]?.features?.includes('realtime') || false;
        },

        // Get available models list
        getAvailableModels() {
            const modelConfig = window.modelManagerInstance?.modelConfig || {};
            return Object.keys(modelConfig).filter(id => id !== 'local-placeholder');
        },

        // Realtime mode methods
        async updateRealtimeMode() {
            const newState = this.realtimeEnabled;
            console.log(`[Settings] Updating turbo mode to: ${newState}`);

            try {
                const response = await fetch(`/api/v2/settings/realtime/${this.streamerId}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ enabled: newState })
                });

                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }

                const data = await response.json();
                console.log('[Settings] Turbo mode update response:', data);

                // Success - keep the current state and show success
                showToast(
                    newState ? 'Turbo mode enabled! 🚀' : 'Turbo mode disabled',
                    newState ? 'success' : 'info'
                );
            } catch (error) {
                // Revert the toggle on error
                this.realtimeEnabled = !newState;
                handleApiError(error, 'update turbo mode');
            }
        },

        // Dead air methods
        updateDeadAir() {
            // HTMX handles the request automatically
        },

        debounceDeadAirUpdate() {
            timerManager.clearTimeout(this.deadairTimeout);
            this.deadairTimeout = timerManager.setTimeout(() => {
                this.updateDeadAirThreshold();
            }, 300);
        },

        async updateDeadAirThreshold() {
            try {
                const response = await fetch(`/api/v2/deadair`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        enabled: this.deadairEnabled,
                        threshold: this.deadairThreshold
                    })
                });
                
                if (response.ok) {
                    console.log(`Dead air threshold updated to ${this.deadairThreshold} seconds`);
                    showToast(`Dead air timing updated to ${this.formatDeadAirTime(this.deadairThreshold)}`, 'success');
                } else {
                    throw new Error(`Failed with status ${response.status}`);
                }
            } catch (error) {
                handleApiError(error, 'update dead air threshold');
            }
        },

        async updateDeadAirEnabled() {
            try {
                const response = await fetch(`/api/v2/deadair`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        enabled: this.deadairEnabled,
                        threshold: this.deadairThreshold
                    })
                });
                
                if (response.ok) {
                    const status = this.deadairEnabled ? 'enabled' : 'disabled';
                    showToast(`Dead air detection ${status}`, 'success');
                } else {
                    throw new Error(`Failed with status ${response.status}`);
                }
            } catch (error) {
                handleApiError(error, 'update dead air status');
            }
        },

        setDeadAirPreset(seconds) {
            this.deadairThreshold = seconds;
            this.updateDeadAirThreshold();
        },

        formatDeadAirTime(seconds) {
            const minutes = Math.floor(seconds / 60);
            const secs = seconds % 60;
            return `${minutes}:${secs.toString().padStart(2, '0')}`;
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
        
        // Update slider visual progress
        updateSliderVisual(slider, value) {
            if (!slider) return;
            
            // Update the visual progress bar
            const percent = (value * 100) + '%';
            slider.style.setProperty('--slider-progress', percent);
            
            // Update any associated progress fill element
            const fill = slider.parentElement?.querySelector('.slider-progress-fill');
            if (fill) {
                fill.style.width = percent;
            }
            
            // Update bubble value if it exists
            const bubble = slider.parentElement?.querySelector('.tbx-slider-bubble span');
            if (bubble) {
                bubble.textContent = Math.round(value * 100) + '%';
            }
            
            // Ensure slider has proper background gradient
            const percentage = value * 100;
            slider.style.background = `linear-gradient(90deg, rgba(232, 107, 215, 0.6) 0%, rgba(232, 107, 215, 0.8) ${percentage}%, var(--tbx-surface-elevated) ${percentage}%, var(--tbx-surface-elevated) 100%)`;
        },

        // Update all personality trait sliders visual state
        updateAllSliderVisuals() {
            console.log('[Settings] Updating all slider visuals with traits:', this.traits);
            const sliders = [
                { name: 'warmth', selector: 'input[x-model="traits.warmth"]' },
                { name: 'energy', selector: 'input[x-model="traits.energy"]' },
                { name: 'humor', selector: 'input[x-model="traits.humor"]' },
                { name: 'sass', selector: 'input[x-model="traits.sass"]' },
                { name: 'verbosity', selector: 'input[x-model="traits.verbosity"]' }
            ];
            
            sliders.forEach(({ name, selector }) => {
                const slider = document.querySelector(selector);
                if (slider && this.traits[name] !== undefined) {
                    slider.value = this.traits[name];
                    this.updateSliderVisual(slider, this.traits[name]);
                    console.log(`[Settings] Updated ${name} slider to ${this.traits[name]}`);
                } else {
                    console.warn(`[Settings] Could not find slider for ${name} or trait value missing`);
                }
            });
        },

        // Memory methods
        updateMemory() {
            // HTMX handles the request automatically
        },

        async loadMemoryStats() {
            try {
                const response = await fetch('/api/v2/api/memory/stats');
                this.memoryStats = await response.json();
            } catch (error) {
                console.error('Failed to load memory stats:', error);
            }
        },

        openMemoryBank() {
            // TODO: Open memory bank modal
            showToast('Memory bank feature coming soon!', 'info');
        },

        // Reset all settings to defaults
        async resetToDefaults() {
            if (!confirm('Reset all personality settings to factory defaults? This will clear your custom configuration.')) {
                return;
            }

            try {
                const response = await fetch('/api/v2/personality/reset-defaults', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });

                if (response.ok) {
                    const result = await response.json();

                    // Update local state with defaults
                    this.traits = { ...result.traits };
                    this.traitsOriginal = { ...result.traits };
                    this.model = result.model;
                    this.currentPreset = result.current_preset || 'custom';
                    this.deadairEnabled = result.deadair_enabled;
                    this.deadairThreshold = result.deadair_threshold;
                    this.memoryEnabled = result.memory_enabled;
                    this.traitsChanged = false;

                    showToast('All settings reset to defaults', 'success');
                } else {
                    throw new Error('Failed to reset to defaults');
                }
            } catch (error) {
                console.error('Failed to reset to defaults:', error);
                showToast('Failed to reset settings', 'error');
            }
        },

        // Chart initialization with robust Chart.js loading checks
        initTokenChart() {
            const canvas = document.getElementById('tokenChart');
            if (!canvas) return;

            // Check if Chart.js is loaded
            if (typeof Chart === 'undefined') {
                console.warn('Chart.js not loaded - skipping chart initialization');
                canvas.style.display = 'none'; // Optionally hide the canvas area
                return;
            }

            try {
                const ctx = canvas.getContext('2d');
                this.tokenChart = new Chart(ctx, {
                    type: 'bar',
                    data: {
                        labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
                        datasets: [{
                            data: [12000, 19000, 3000, 5000, 2000, 3000, 8000],
                            backgroundColor: '#e86bd7',
                            borderRadius: 4,
                            borderSkipped: false
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: {
                                display: false
                            }
                        },
                        scales: {
                            x: {
                                grid: {
                                    display: false
                                },
                                ticks: {
                                    color: 'rgba(255,255,255,0.4)',
                                    font: {
                                        size: 10
                                    }
                                }
                            },
                            y: {
                                grid: {
                                    color: 'rgba(255,255,255,0.06)',
                                    borderDash: [2, 2]
                                },
                                ticks: {
                                    color: 'rgba(255,255,255,0.4)',
                                    font: {
                                        size: 10
                                    },
                                    callback: function(value) {
                                        return value / 1000 + 'K';
                                    }
                                }
                            }
                        }
                    }
                });
            } catch (error) {
                console.error('Failed to initialize token chart:', error);
            }
        }
    };
}

// Right rail component
function rightRail() {
    return {
        connectionStatus: 'listening',
        recentInteractions: [],
        metrics: {
            silencesFilled: 0,
            uniqueEngaged: 0,
            awkwardSaved: 0
        },

        init() {
            // Listen for WebSocket messages
            this.$el.addEventListener('htmx:wsAfterMessage', (event) => {
                this.handleWebSocketMessage(event.detail);
            });

            // Load initial data
            this.loadMetrics();
        },

        handleWebSocketMessage(message) {
            switch (message.type) {
                case 'status':
                    this.connectionStatus = message.state.toLowerCase();
                    break;

                case 'interaction':
                    this.addInteraction(message);
                    break;

                case 'metric':
                    this.updateMetric(message.key, message.value);
                    break;
            }
        },

        addInteraction(interaction) {
            // Add to beginning of array
            this.recentInteractions.unshift({
                id: Date.now(),
                user: interaction.user,
                message: interaction.msg,
                timestamp: interaction.ts || Date.now()
            });

            // Keep only last 10 interactions
            if (this.recentInteractions.length > 10) {
                this.recentInteractions = this.recentInteractions.slice(0, 10);
            }
        },

        updateMetric(key, value) {
            switch (key) {
                case 'silences_filled':
                    this.metrics.silencesFilled = value;
                    break;
                case 'unique_engaged':
                    this.metrics.uniqueEngaged = value;
                    break;
                case 'awkward_saved':
                    this.metrics.awkwardSaved = value;
                    break;
            }
        },

        async loadMetrics() {
            try {
                const response = await fetch('/api/v2/api/stats/loneliness');
                const stats = await response.json();

                this.metrics = {
                    silencesFilled: stats.silences_filled || 0,
                    uniqueEngaged: stats.unique_engaged || 0,
                    awkwardSaved: stats.awkward_saved || 0
                };
            } catch (error) {
                console.error('Failed to load metrics:', error);
            }
        },

        formatTime(timestamp) {
            const now = Date.now();
            const diff = now - timestamp;

            if (diff < 60000) return 'now';
            if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
            if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
            return Math.floor(diff / 86400000) + 'd ago';
        }
    };
}

// Memory card component
function memoryCard() {
    return {
        enabled: true,
        stats: {
            total: 0,
            optedOut: 0
        },

        init() {
            this.loadStats();
        },

        async loadStats() {
            try {
                const response = await fetch('/api/v2/api/memory/stats');
                this.stats = await response.json();
            } catch (error) {
                console.error('Failed to load memory stats:', error);
            }
        },

        toggle() {
            this.enabled = !this.enabled;
            // HTMX handles the API call
        },

        openBank() {
            // TODO: Implement memory bank modal
            showToast('Memory bank coming soon!', 'info');
        }
    };
}

// Global utility functions
window.settingsPage = settingsPage;
window.rightRail = rightRail;
window.memoryCard = memoryCard;

// Use the Toast class from app.js if available, otherwise create a fallback
function showToast(message, type = 'info', duration = 3000) {
    // If Toast class is available from app.js, use it
    if (window.Toast && typeof window.Toast.show === 'function') {
        return window.Toast.show(message, type, duration);
    }
    
    // Fallback implementation if Toast is not available yet
    console.log(`[${type.toUpperCase()}] ${message}`);
    
    // Defer toast creation if DOM is not ready
    if (!document.body) {
        console.warn('[Settings] Document body not ready, deferring toast');
        return;
    }

    const toast = document.createElement('div');
    // Use classes for styling, not inline styles
    toast.className = `toast toast-${type}`;
    toast.textContent = message;

    // Handle stacking of multiple toasts
    const existingToasts = document.querySelectorAll('.toast');
    if (existingToasts.length > 0) {
        // Stack the new toast above existing ones
        toast.style.setProperty('--toast-index', existingToasts.length);
        toast.style.bottom = `${20 + (60 * existingToasts.length)}px`;
    }

    document.body.appendChild(toast);

    // Auto-remove after duration
    const removeToast = () => {
        toast.classList.add('toast-fade-out');
        toast.addEventListener('animationend', () => {
            toast.remove();
            // Re-stack remaining toasts
            const remainingToasts = document.querySelectorAll('.toast');
            remainingToasts.forEach((t, index) => {
                t.style.bottom = `${20 + (60 * index)}px`;
            });
        });
    };

    timerManager.setTimeout(removeToast, duration);

    // Allow manual dismissal on click
    toast.addEventListener('click', removeToast);

    return toast;
}

// Make showToast globally available
window.showToast = showToast;

// Enhanced API error handling utility
function handleApiError(error, operation = 'operation') {
    console.error(`API Error during ${operation}:`, error);

    let userMessage = `Failed to ${operation}`;

    // Provide more specific error messages based on error type
    if (error.name === 'TypeError' && error.message.includes('fetch')) {
        userMessage = `Network error during ${operation}. Please check your connection.`;
    } else if (error.message.includes('404')) {
        userMessage = `${operation} failed - endpoint not found.`;
    } else if (error.message.includes('403')) {
        userMessage = `${operation} failed - access denied.`;
    } else if (error.message.includes('500')) {
        userMessage = `${operation} failed - server error. Please try again later.`;
    } else if (error.message.includes('401')) {
        userMessage = `${operation} failed - authentication required.`;
    } else if (error.message) {
        // Use the actual error message if it's user-friendly
        const msg = error.message.toLowerCase();
        if (!msg.includes('http') && !msg.includes('fetch') && msg.length < 100) {
            userMessage = `${operation} failed: ${error.message}`;
        }
    }

    showToast(userMessage, 'error');
    return userMessage;
}

// Make API error handler globally available
window.handleApiError = handleApiError;

// Settings API Service for model management
class SettingsApiService {
    static async saveModel(streamerId, model) {
        const endpoint = `/api/v2/settings/model/consolidated?streamer_uuid=${streamerId}`;
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model })
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        return response.json();
    }
    
    static async saveRealtimeMode(streamerId, enabled) {
        const endpoint = `/api/v2/settings/realtime?streamer_id=${streamerId}`;
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled })
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        return response.json();
    }
    
    static async getOllamaStatus() {
        const response = await fetch('/api/v2/settings/ollama/status');
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        return response.json();
    }
    
    static async saveTTSVoice(streamerId, voice) {
        const endpoint = `/api/v2/settings/tts-voice?streamer_id=${streamerId}`;
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ voice })
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        return response.json();
    }
    
    static async saveAudioDelay(streamerId, delay_ms) {
        const endpoint = `/api/v2/settings/audio-delay?streamer_id=${streamerId}`;
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ delay_ms })
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        return response.json();
    }
    
    static async saveResponseFrequency(streamerId, frequency) {
        const endpoint = `/api/v2/settings/response-frequency?streamer_id=${streamerId}`;
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ frequency })
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        return response.json();
    }
    
    static async saveMessageFiltering(streamerId, filters) {
        const endpoint = `/api/v2/settings/message-filtering?streamer_id=${streamerId}`;
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(filters)
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        return response.json();
    }
    
    static async saveVolumeSettings(streamerId, volumes) {
        const endpoint = `/api/v2/settings/volume-settings?streamer_id=${streamerId}`;
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(volumes)
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        return response.json();
    }
    
    static async savePrivacySettings(streamerId, privacy) {
        const endpoint = `/api/v2/settings/privacy-settings?streamer_id=${streamerId}`;
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(privacy)
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        return response.json();
    }
}

// Make SettingsApiService globally available
window.SettingsApiService = SettingsApiService;

// Simple StateManager for model management
class StateManager {
    constructor() {
        this.state = {
            streamerId: 'default',
            models: {
                current: 'gpt-4o-mini',
                available: ['gpt-4o-mini', 'gpt-4o', 'gpt-4-turbo', 'claude-3-5-sonnet-20241022', 'claude-3-5-haiku-20241022'],
                realtimeEnabled: false,
                ollamaAvailable: false,
                ollamaModels: []
            }
        };
        this.subscribers = new Map();
    }
    
    setState(path, value) {
        const keys = path.split('.');
        let current = this.state;
        
        // Navigate to parent of target key
        for (let i = 0; i < keys.length - 1; i++) {
            if (!current[keys[i]]) current[keys[i]] = {};
            current = current[keys[i]];
        }
        
        // Set value
        const lastKey = keys[keys.length - 1];
        current[lastKey] = value;
        
        // Notify subscribers
        if (this.subscribers.has(path)) {
            this.subscribers.get(path).forEach(callback => callback(value));
        }
    }
    
    subscribe(path, callback) {
        if (!this.subscribers.has(path)) {
            this.subscribers.set(path, []);
        }
        this.subscribers.get(path).push(callback);
    }
}

// Make StateManager globally available  
window.StateManager = new StateManager();

// Auto-initialize components when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    // Any global initialization can go here
    console.log('Settings UI initialized');
});
