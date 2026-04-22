// Onboarding Wizard JavaScript

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

class OnboardingWizard {
    constructor() {
        this.currentStep = 1;
        this.totalSteps = 6;
        this.data = {
            twitch: {
                username: '',
                token: '',
                channel: ''
            },
            openai: {
                apiKey: '',
                model: 'gpt-4o-mini'
            },
            voice: {
                enabled: true,
                model: 'alloy',
                speed: 1.0
            },
            personality: {
                preset: 'supportive',
                memory: true,
                context: true
            }
        };
        
        this.personalityResponses = {
            supportive: "Hey there! I'm doing great, thanks for asking! How's your day going? 😊",
            hype: "YOOOO what's UP! I'm absolutely CRUSHING it today! How about you, legend?! 🔥",
            chill: "Oh hey, just vibing here. Thanks for asking! What's good with you? 😌",
            roast: "Well, well, well... look who's being polite. I'm doing better than your last attempt at that game 😏"
        };
        
        this.initializeElements();
        this.bindEvents();
        this.updateProgress();
    }

    initializeElements() {
        // Navigation elements
        this.backBtn = document.getElementById('back-btn');
        this.nextBtn = document.getElementById('next-btn');
        this.stepCounter = document.getElementById('step-counter');
        this.progressFill = document.getElementById('progress-fill');
        this.progressSteps = document.getElementById('progress-steps');
        
        // Form elements
        this.twitchUsername = document.getElementById('twitch-username');
        this.twitchToken = document.getElementById('twitch-token');
        this.twitchChannel = document.getElementById('twitch-channel');
        this.openaiKey = document.getElementById('openai-key');
        this.aiModel = document.getElementById('ai-model');
        this.enableVoice = document.getElementById('enable-voice');
        this.voiceModel = document.getElementById('voice-model');
        this.voiceSpeed = document.getElementById('voice-speed');
        this.speedValue = document.getElementById('speed-value');
        this.enableMemory = document.getElementById('enable-memory');
        this.enableContext = document.getElementById('enable-context');
        
        // Test buttons
        this.testTwitchBtn = document.getElementById('test-twitch-connection');
        this.testOpenAIBtn = document.getElementById('test-openai-connection');
        this.testVoiceBtn = document.getElementById('test-voice');
        
        // Status elements
        this.twitchStatus = document.getElementById('twitch-status');
        this.openaiStatus = document.getElementById('openai-status');
        this.voiceTestStatus = document.getElementById('voice-test-status');
        
        // Toggle buttons
        this.toggleTokenBtn = document.getElementById('toggle-token-visibility');
        this.toggleKeyBtn = document.getElementById('toggle-key-visibility');
        
        // Voice settings container
        this.voiceSettings = document.getElementById('voice-settings');
        
        // Preview elements
        this.previewResponse = document.getElementById('preview-response');
        
        // Summary elements
        this.summaryChannel = document.getElementById('summary-channel');
        this.summaryModel = document.getElementById('summary-model');
        this.summaryVoice = document.getElementById('summary-voice');
        this.summaryPersonality = document.getElementById('summary-personality');
        
        // Final buttons
        this.goToDashboardBtn = document.getElementById('go-to-dashboard');
        this.advancedSettingsBtn = document.getElementById('advanced-settings');
    }

    bindEvents() {
        // Navigation
        this.backBtn?.addEventListener('click', () => this.previousStep());
        this.nextBtn?.addEventListener('click', () => this.nextStep());
        
        // Form inputs
        this.twitchUsername?.addEventListener('input', () => this.validateCurrentStep());
        this.twitchToken?.addEventListener('input', () => this.validateCurrentStep());
        this.twitchChannel?.addEventListener('input', () => this.validateCurrentStep());
        this.openaiKey?.addEventListener('input', () => this.validateCurrentStep());
        this.aiModel?.addEventListener('change', () => this.data.openai.model = this.aiModel.value);
        
        // Voice settings
        this.enableVoice?.addEventListener('change', () => this.toggleVoiceSettings());
        this.voiceModel?.addEventListener('change', () => this.data.voice.model = this.voiceModel.value);
        this.voiceSpeed?.addEventListener('input', () => this.updateVoiceSpeed());
        
        // Personality selection
        document.querySelectorAll('input[name="personality"]').forEach(radio => {
            radio.addEventListener('change', () => this.updatePersonalityPreview());
        });
        
        // Memory and context settings
        this.enableMemory?.addEventListener('change', () => this.data.personality.memory = this.enableMemory.checked);
        this.enableContext?.addEventListener('change', () => this.data.personality.context = this.enableContext.checked);
        
        // Test buttons
        this.testTwitchBtn?.addEventListener('click', () => this.testTwitchConnection());
        this.testOpenAIBtn?.addEventListener('click', () => this.testOpenAIConnection());
        this.testVoiceBtn?.addEventListener('click', () => this.testVoice());
        
        // Toggle visibility buttons
        this.toggleTokenBtn?.addEventListener('click', () => this.toggleVisibility('twitch-token', 'toggle-token-visibility'));
        this.toggleKeyBtn?.addEventListener('click', () => this.toggleVisibility('openai-key', 'toggle-key-visibility'));
        
        // Final action buttons
        this.goToDashboardBtn?.addEventListener('click', () => this.goToDashboard());
        this.advancedSettingsBtn?.addEventListener('click', () => this.goToAdvancedSettings());
        
        // Keyboard navigation
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                if (this.currentStep < this.totalSteps) {
                    this.nextStep();
                }
            } else if (e.key === 'Escape') {
                if (this.currentStep > 1) {
                    this.previousStep();
                }
            }
        });
    }

    updateProgress() {
        const progressPercent = (this.currentStep / this.totalSteps) * 100;
        this.progressFill.style.width = `${progressPercent}%`;
        this.stepCounter.textContent = this.currentStep;
        
        // Update step indicators
        this.progressSteps.querySelectorAll('.tbx-progress-step').forEach((step, index) => {
            const stepNumber = index + 1;
            step.classList.remove('tbx-progress-step--active', 'tbx-progress-step--completed');
            
            if (stepNumber === this.currentStep) {
                step.classList.add('tbx-progress-step--active');
            } else if (stepNumber < this.currentStep) {
                step.classList.add('tbx-progress-step--completed');
            }
        });
        
        // Update navigation buttons
        this.backBtn.disabled = this.currentStep === 1;
        
        if (this.currentStep === this.totalSteps) {
            this.nextBtn.style.display = 'none';
        } else {
            this.nextBtn.style.display = 'flex';
            this.nextBtn.disabled = !this.isStepValid(this.currentStep);
        }
    }

    showStep(stepNumber) {
        // Hide all steps
        document.querySelectorAll('.tbx-onboarding__step').forEach(step => {
            step.classList.add('tbx-onboarding__step--hidden');
        });
        
        // Show current step
        const currentStepEl = document.getElementById(`step-${stepNumber}`);
        if (currentStepEl) {
            currentStepEl.classList.remove('tbx-onboarding__step--hidden');
        }
        
        // Focus on first input of the step
        const firstInput = currentStepEl?.querySelector('input, select, textarea');
        if (firstInput && stepNumber > 1) {
            timerManager.setTimeout(() => firstInput.focus(), 100);
        }
    }

    nextStep() {
        if (this.currentStep >= this.totalSteps) return;
        
        if (!this.isStepValid(this.currentStep)) {
            this.showToast('Please complete all required fields', 'warning');
            return;
        }
        
        // Save current step data
        this.saveStepData();
        
        this.currentStep++;
        this.showStep(this.currentStep);
        this.updateProgress();
        
        // Handle step-specific logic
        if (this.currentStep === 6) {
            this.populateSummary();
        }
    }

    previousStep() {
        if (this.currentStep <= 1) return;
        
        this.currentStep--;
        this.showStep(this.currentStep);
        this.updateProgress();
    }

    isStepValid(stepNumber) {
        switch (stepNumber) {
            case 1:
                return true; // Welcome step - always valid
            
            case 2:
                return this.twitchUsername?.value.trim() && 
                       this.twitchToken?.value.trim() && 
                       this.twitchChannel?.value.trim();
            
            case 3:
                return this.openaiKey?.value.trim();
            
            case 4:
            case 5:
                return true; // Optional configurations
            
            case 6:
                return true; // Completion step
            
            default:
                return false;
        }
    }

    validateCurrentStep() {
        const isValid = this.isStepValid(this.currentStep);
        this.nextBtn.disabled = !isValid;
        return isValid;
    }

    saveStepData() {
        switch (this.currentStep) {
            case 2:
                this.data.twitch = {
                    username: this.twitchUsername.value.trim(),
                    token: this.twitchToken.value.trim(),
                    channel: this.twitchChannel.value.trim()
                };
                break;
            
            case 3:
                this.data.openai = {
                    apiKey: this.openaiKey.value.trim(),
                    model: this.aiModel.value
                };
                break;
            
            case 4:
                this.data.voice = {
                    enabled: this.enableVoice.checked,
                    model: this.voiceModel.value,
                    speed: parseFloat(this.voiceSpeed.value)
                };
                break;
            
            case 5:
                const selectedPersonality = document.querySelector('input[name="personality"]:checked');
                this.data.personality = {
                    preset: selectedPersonality?.value || 'supportive',
                    memory: this.enableMemory.checked,
                    context: this.enableContext.checked
                };
                break;
        }
    }

    async testTwitchConnection() {
        const username = this.twitchUsername.value.trim();
        const token = this.twitchToken.value.trim();
        const channel = this.twitchChannel.value.trim();
        
        if (!username || !token || !channel) {
            this.showConnectionStatus('twitch-status', 'Please fill in all Twitch fields', 'error');
            return;
        }
        
        this.showConnectionStatus('twitch-status', 'Testing connection...', 'testing');
        
        try {
            const response = await fetch('/api/v2/setup/test-twitch', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ username, token, channel })
            });
            
            if (response.ok) {
                this.showConnectionStatus('twitch-status', 'Connection successful!', 'success');
            } else {
                const error = await response.json();
                this.showConnectionStatus('twitch-status', error.detail || 'Connection failed', 'error');
            }
        } catch (error) {
            this.showConnectionStatus('twitch-status', 'Network error', 'error');
        }
    }

    async testOpenAIConnection() {
        const apiKey = this.openaiKey.value.trim();
        const model = this.aiModel.value;
        
        if (!apiKey) {
            this.showConnectionStatus('openai-status', 'Please enter your API key', 'error');
            return;
        }
        
        this.showConnectionStatus('openai-status', 'Testing API key...', 'testing');
        
        try {
            const response = await fetch('/api/v2/setup/test-openai', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ apiKey, model })
            });
            
            if (response.ok) {
                this.showConnectionStatus('openai-status', 'API key valid!', 'success');
            } else {
                const error = await response.json();
                this.showConnectionStatus('openai-status', error.detail || 'Invalid API key', 'error');
            }
        } catch (error) {
            this.showConnectionStatus('openai-status', 'Network error', 'error');
        }
    }

    async testVoice() {
        if (!this.enableVoice.checked) {
            this.voiceTestStatus.textContent = 'Voice is disabled';
            return;
        }
        
        const model = this.voiceModel.value;
        const speed = parseFloat(this.voiceSpeed.value);
        
        this.voiceTestStatus.textContent = 'Testing voice...';
        
        try {
            const response = await fetch('/api/v2/setup/test-voice', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ 
                    model, 
                    speed,
                    text: 'Hello! This is a test of your TalkBot voice settings.' 
                })
            });
            
            if (response.ok) {
                const audioBlob = await response.blob();
                const audioUrl = URL.createObjectURL(audioBlob);
                const audio = new Audio(audioUrl);
                
                audio.play();
                this.voiceTestStatus.textContent = 'Playing test audio...';
                
                audio.onended = () => {
                    this.voiceTestStatus.textContent = 'Voice test completed!';
                    URL.revokeObjectURL(audioUrl);
                };
            } else {
                this.voiceTestStatus.textContent = 'Voice test failed';
            }
        } catch (error) {
            this.voiceTestStatus.textContent = 'Network error';
        }
    }

    toggleVoiceSettings() {
        const isEnabled = this.enableVoice.checked;
        this.voiceSettings.style.display = isEnabled ? 'flex' : 'none';
        this.data.voice.enabled = isEnabled;
    }

    updateVoiceSpeed() {
        const speed = parseFloat(this.voiceSpeed.value);
        this.speedValue.textContent = `${speed}x`;
        this.data.voice.speed = speed;
    }

    updatePersonalityPreview() {
        const selectedPersonality = document.querySelector('input[name="personality"]:checked');
        if (selectedPersonality && this.previewResponse) {
            const preset = selectedPersonality.value;
            this.previewResponse.textContent = this.personalityResponses[preset];
            this.data.personality.preset = preset;
        }
    }

    toggleVisibility(inputId, buttonId) {
        const input = document.getElementById(inputId);
        const button = document.getElementById(buttonId);
        const showIcon = button.querySelector('.tbx-icon-show');
        const hideIcon = button.querySelector('.tbx-icon-hide');
        
        if (input.type === 'password') {
            input.type = 'text';
            showIcon.style.display = 'none';
            hideIcon.style.display = 'block';
        } else {
            input.type = 'password';
            showIcon.style.display = 'block';
            hideIcon.style.display = 'none';
        }
    }

    populateSummary() {
        this.summaryChannel.textContent = this.data.twitch.channel;
        this.summaryModel.textContent = this.data.openai.model;
        this.summaryVoice.textContent = this.data.voice.enabled ? 
            `${this.data.voice.model} (${this.data.voice.speed}x)` : 'Disabled';
        this.summaryPersonality.textContent = this.capitalizeFirst(this.data.personality.preset);
    }

    async completeSetup() {
        try {
            this.showToast('Saving configuration...', 'info');
            
            const response = await fetch('/api/v2/setup/complete', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(this.data)
            });
            
            if (response.ok) {
                this.showToast('Setup completed successfully!', 'success');
                return true;
            } else {
                const error = await response.json();
                this.showToast(error.detail || 'Setup failed', 'error');
                return false;
            }
        } catch (error) {
            this.showToast('Network error during setup', 'error');
            return false;
        }
    }

    async goToDashboard() {
        const success = await this.completeSetup();
        if (success) {
            // Redirect to dashboard with the configured streamer ID
            const streamerId = this.data.twitch.channel;
            window.location.href = `/ui/v2/dashboard/${streamerId}`;
        }
    }

    async goToAdvancedSettings() {
        const success = await this.completeSetup();
        if (success) {
            // Redirect to settings page
            const streamerId = this.data.twitch.channel;
            window.location.href = `/ui/v2/settings/${streamerId}`;
        }
    }

    showConnectionStatus(elementId, message, type) {
        const element = document.getElementById(elementId);
        if (!element) return;
        
        element.textContent = message;
        element.className = `tbx-connection-status tbx-connection-status--${type}`;
    }

    showToast(message, type = 'info') {
        const toast = document.createElement('div');
        toast.className = `tbx-toast tbx-toast--${type}`;
        toast.innerHTML = `
            <div class="tbx-toast__content">
                <span class="tbx-toast__message">${message}</span>
                <button class="tbx-toast__close">
                    <svg viewBox="0 0 24 24" fill="currentColor">
                        <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/>
                    </svg>
                </button>
            </div>
        `;
        
        const container = document.getElementById('toast-container');
        container.appendChild(toast);
        
        // Auto remove after 5 seconds
        timerManager.setTimeout(() => {
            if (toast.parentNode) {
                container.removeChild(toast);
            }
        }, 5000);
        
        // Manual close button
        toast.querySelector('.tbx-toast__close').addEventListener('click', () => {
            if (toast.parentNode) {
                container.removeChild(toast);
            }
        });
    }

    capitalizeFirst(str) {
        return str.charAt(0).toUpperCase() + str.slice(1);
    }
}

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    new OnboardingWizard();
});

// Auto-fill development data (remove in production)
if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
    document.addEventListener('DOMContentLoaded', () => {
        timerManager.setTimeout(() => {
            const devButton = document.createElement('button');
            devButton.textContent = 'Fill Dev Data';
            devButton.className = 'tbx-btn tbx-btn--secondary';
            devButton.style.position = 'fixed';
            devButton.style.top = '10px';
            devButton.style.right = '10px';
            devButton.style.zIndex = '9999';
            devButton.style.fontSize = '12px';
            devButton.style.padding = '0.5rem';
            
            devButton.onclick = () => {
                // Fill development data
                const twitchUsername = document.getElementById('twitch-username');
                const twitchToken = document.getElementById('twitch-token');
                const twitchChannel = document.getElementById('twitch-channel');
                const openaiKey = document.getElementById('openai-key');
                
                if (twitchUsername) twitchUsername.value = 'dev_bot';
                if (twitchToken) twitchToken.value = 'oauth:dev_token_123';
                if (twitchChannel) twitchChannel.value = 'dev_channel';
                if (openaiKey) openaiKey.value = 'sk-dev_key_123';
                
                // Trigger validation
                const event = new Event('input', { bubbles: true });
                [twitchUsername, twitchToken, twitchChannel, openaiKey].forEach(el => {
                    if (el) el.dispatchEvent(event);
                });
            };
            
            document.body.appendChild(devButton);
        }, 1000);
    });
}
// Clean up timers on page unload
if (typeof window !== 'undefined') {
    window.addEventListener('beforeunload', () => {
        if (typeof timerManager !== 'undefined') {
            timerManager.clearAll();
        }
    });
}
