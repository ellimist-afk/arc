// Memory System JavaScript

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

class MemorySystem {
    constructor() {
        this.streamerId = this.getStreamerIdFromUrl();
        this.memories = [];
        this.filteredMemories = [];
        this.currentPage = 1;
        this.pageSize = 10;
        this.totalPages = 1;
        this.filters = {
            search: '',
            type: 'all',
            timeRange: 'all'
        };
        
        this.initializeElements();
        this.bindEvents();
        this.loadMemories();
        this.loadMemoryStats();
        this.loadMemoryConfig();
    }

    getStreamerIdFromUrl() {
        const pathParts = window.location.pathname.split('/');
        return pathParts[pathParts.length - 1] || 'confusedamish';
    }

    initializeElements() {
        // Search and filter elements
        this.searchInput = document.getElementById('memory-search');
        this.typeFilter = document.getElementById('memory-type-filter');
        this.timeFilter = document.getElementById('memory-time-filter');
        this.clearFiltersBtn = document.getElementById('clear-filters');
        
        // Memory list elements
        this.memoryList = document.getElementById('memory-list');
        this.memoryCount = document.getElementById('memory-count');
        this.loadingState = document.getElementById('memory-loading');
        this.emptyState = document.getElementById('memory-empty');
        
        // Pagination elements
        this.pagination = document.getElementById('memory-pagination');
        this.prevPageBtn = document.getElementById('prev-page');
        this.nextPageBtn = document.getElementById('next-page');
        this.currentPageSpan = document.getElementById('current-page');
        this.totalPagesSpan = document.getElementById('total-pages');
        
        // Modal elements
        this.modal = document.getElementById('memory-modal');
        this.modalTitle = document.getElementById('memory-modal-title');
        this.modalBody = document.getElementById('memory-modal-body');
        this.deleteMemoryBtn = document.getElementById('delete-memory');
        
        // Action buttons
        this.exportBtn = document.getElementById('export-memory');
        this.importBtn = document.getElementById('import-memory');
        this.clearAllBtn = document.getElementById('clear-all-memories');
        this.saveConfigBtn = document.getElementById('save-memory-config');
        
        // Stats elements
        this.totalMemoriesSpan = document.getElementById('total-memories');
        this.shortTermSpan = document.getElementById('short-term-memories');
        this.longTermSpan = document.getElementById('long-term-memories');
        this.storageUsedSpan = document.getElementById('storage-used');
        
        // Config elements
        this.retentionSelect = document.getElementById('memory-retention');
        this.limitInput = document.getElementById('memory-limit');
        this.autoConsolidationToggle = document.getElementById('auto-consolidation');
        this.privacyModeToggle = document.getElementById('privacy-mode');
    }

    bindEvents() {
        // Search and filter events
        this.searchInput?.addEventListener('input', debounce(() => this.handleSearch(), 300));
        this.typeFilter?.addEventListener('change', () => this.handleFilterChange());
        this.timeFilter?.addEventListener('change', () => this.handleFilterChange());
        this.clearFiltersBtn?.addEventListener('click', () => this.clearFilters());
        
        // Pagination events
        this.prevPageBtn?.addEventListener('click', () => this.goToPreviousPage());
        this.nextPageBtn?.addEventListener('click', () => this.goToNextPage());
        
        // Modal events
        this.modal?.addEventListener('click', (e) => {
            if (e.target === this.modal || e.target.classList.contains('tbx-modal__backdrop')) {
                this.closeModal();
            }
        });
        
        document.querySelectorAll('.tbx-modal__close').forEach(btn => {
            btn.addEventListener('click', () => this.closeModal());
        });
        
        this.deleteMemoryBtn?.addEventListener('click', () => this.deleteCurrentMemory());
        
        // Action button events
        this.exportBtn?.addEventListener('click', () => this.exportMemories());
        this.importBtn?.addEventListener('click', () => this.importMemories());
        this.clearAllBtn?.addEventListener('click', () => this.clearAllMemories());
        this.saveConfigBtn?.addEventListener('click', () => this.saveMemoryConfig());
        
        // Keyboard events
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.modal.style.display !== 'none') {
                this.closeModal();
            }
        });
    }

    async loadMemories() {
        try {
            this.showLoading();
            
            const response = await fetch(`/api/v2/memory/${this.streamerId}/entries?page=${this.currentPage}&limit=${this.pageSize}`);
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const data = await response.json();
            this.memories = data.memories || [];
            this.totalPages = Math.ceil((data.total || 0) / this.pageSize);
            
            this.applyFilters();
            this.renderMemories();
            this.updatePagination();
            
        } catch (error) {
            console.error('Failed to load memories:', error);
            this.showError('Failed to load memories');
        } finally {
            this.hideLoading();
        }
    }

    async loadMemoryStats() {
        try {
            const response = await fetch(`/api/v2/memory/${this.streamerId}/stats`);
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const stats = await response.json();
            
            this.totalMemoriesSpan.textContent = stats.total || 0;
            this.shortTermSpan.textContent = stats.shortTerm || 0;
            this.longTermSpan.textContent = stats.longTerm || 0;
            this.storageUsedSpan.textContent = `${(stats.storageUsed || 0).toFixed(1)} MB`;
            
        } catch (error) {
            console.error('Failed to load memory stats:', error);
        }
    }

    async loadMemoryConfig() {
        try {
            const response = await fetch(`/api/v2/memory/${this.streamerId}/config`);
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const config = await response.json();
            
            this.retentionSelect.value = config.retention || 30;
            this.limitInput.value = config.limit || 1000;
            this.autoConsolidationToggle.checked = config.autoConsolidation !== false;
            this.privacyModeToggle.checked = config.privacyMode === true;
            
        } catch (error) {
            console.error('Failed to load memory config:', error);
        }
    }

    handleSearch() {
        this.filters.search = this.searchInput.value.trim().toLowerCase();
        this.applyFilters();
        this.renderMemories();
        this.resetPagination();
    }

    handleFilterChange() {
        this.filters.type = this.typeFilter.value;
        this.filters.timeRange = this.timeFilter.value;
        this.applyFilters();
        this.renderMemories();
        this.resetPagination();
    }

    clearFilters() {
        this.searchInput.value = '';
        this.typeFilter.value = 'all';
        this.timeFilter.value = 'all';
        
        this.filters = {
            search: '',
            type: 'all',
            timeRange: 'all'
        };
        
        this.applyFilters();
        this.renderMemories();
        this.resetPagination();
    }

    applyFilters() {
        this.filteredMemories = this.memories.filter(memory => {
            // Search filter
            if (this.filters.search && !memory.content.toLowerCase().includes(this.filters.search)) {
                return false;
            }
            
            // Type filter
            if (this.filters.type !== 'all' && memory.type !== this.filters.type) {
                return false;
            }
            
            // Time range filter
            if (this.filters.timeRange !== 'all') {
                const memoryDate = new Date(memory.timestamp);
                const now = new Date();
                const diffDays = (now - memoryDate) / (1000 * 60 * 60 * 24);
                
                switch (this.filters.timeRange) {
                    case 'today':
                        if (diffDays > 1) return false;
                        break;
                    case 'week':
                        if (diffDays > 7) return false;
                        break;
                    case 'month':
                        if (diffDays > 30) return false;
                        break;
                    case 'year':
                        if (diffDays > 365) return false;
                        break;
                }
            }
            
            return true;
        });
        
        this.memoryCount.textContent = `${this.filteredMemories.length} ${this.filteredMemories.length === 1 ? 'entry' : 'entries'}`;
    }

    renderMemories() {
        if (this.filteredMemories.length === 0) {
            this.showEmptyState();
            return;
        }
        
        const startIndex = (this.currentPage - 1) * this.pageSize;
        const endIndex = startIndex + this.pageSize;
        const pageMemories = this.filteredMemories.slice(startIndex, endIndex);
        
        this.memoryList.innerHTML = pageMemories.map(memory => this.createMemoryHTML(memory)).join('');
        
        // Bind click events to memory items
        this.memoryList.querySelectorAll('.tbx-memory-item').forEach(item => {
            item.addEventListener('click', () => {
                const memoryId = item.dataset.memoryId;
                const memory = this.memories.find(m => m.id === memoryId);
                if (memory) {
                    this.showMemoryDetail(memory);
                }
            });
        });
        
        // Bind action button events
        this.memoryList.querySelectorAll('.tbx-memory-item__action--delete').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const memoryId = btn.closest('.tbx-memory-item').dataset.memoryId;
                this.deleteMemory(memoryId);
            });
        });
        
        this.hideEmptyState();
    }

    createMemoryHTML(memory) {
        const timestamp = new Date(memory.timestamp).toLocaleDateString('en-US', {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        });
        
        const tags = memory.tags || [];
        const preview = memory.content.length > 200 ? 
            memory.content.substring(0, 200) + '...' : 
            memory.content;
        
        return `
            <div class="tbx-memory-item" data-memory-id="${memory.id}">
                <div class="tbx-memory-item__header">
                    <div class="tbx-memory-item__meta">
                        <div class="tbx-memory-item__type tbx-memory-item__type--${memory.type}">
                            ${memory.type}
                        </div>
                        <div class="tbx-memory-item__timestamp">${timestamp}</div>
                    </div>
                    <div class="tbx-memory-item__actions">
                        <button class="tbx-memory-item__action tbx-memory-item__action--delete" title="Delete memory">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                                <path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/>
                            </svg>
                        </button>
                    </div>
                </div>
                <div class="tbx-memory-item__content">
                    <div class="tbx-memory-item__text">${this.escapeHtml(preview)}</div>
                </div>
                <div class="tbx-memory-item__footer">
                    <div class="tbx-memory-item__tags">
                        ${tags.map(tag => `<span class="tbx-memory-item__tag">${this.escapeHtml(tag)}</span>`).join('')}
                    </div>
                    <div class="tbx-memory-item__confidence" title="Confidence: ${memory.confidence || 0}%">
                        ${this.getConfidenceIndicator(memory.confidence || 0)}
                    </div>
                </div>
            </div>
        `;
    }

    getConfidenceIndicator(confidence) {
        const bars = 5;
        const filledBars = Math.round((confidence / 100) * bars);
        let html = '<div class="tbx-confidence-indicator">';
        
        for (let i = 0; i < bars; i++) {
            const filled = i < filledBars ? 'filled' : '';
            html += `<div class="tbx-confidence-bar ${filled}"></div>`;
        }
        
        html += '</div>';
        return html;
    }

    showMemoryDetail(memory) {
        this.currentMemory = memory;
        this.modalTitle.textContent = `Memory Details - ${memory.type}`;
        
        const timestamp = new Date(memory.timestamp).toLocaleDateString('en-US', {
            year: 'numeric',
            month: 'long',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        });
        
        this.modalBody.innerHTML = `
            <div class="tbx-memory-detail">
                <div class="tbx-memory-detail__section">
                    <h4>Content</h4>
                    <div class="tbx-memory-detail__content">${this.escapeHtml(memory.content)}</div>
                </div>
                
                <div class="tbx-memory-detail__section">
                    <h4>Metadata</h4>
                    <div class="tbx-memory-detail__metadata">
                        <div class="tbx-memory-detail__meta-item">
                            <div class="tbx-memory-detail__meta-label">Type</div>
                            <div class="tbx-memory-detail__meta-value">${memory.type}</div>
                        </div>
                        <div class="tbx-memory-detail__meta-item">
                            <div class="tbx-memory-detail__meta-label">Created</div>
                            <div class="tbx-memory-detail__meta-value">${timestamp}</div>
                        </div>
                        <div class="tbx-memory-detail__meta-item">
                            <div class="tbx-memory-detail__meta-label">Confidence</div>
                            <div class="tbx-memory-detail__meta-value">${memory.confidence || 0}%</div>
                        </div>
                        <div class="tbx-memory-detail__meta-item">
                            <div class="tbx-memory-detail__meta-label">Source</div>
                            <div class="tbx-memory-detail__meta-value">${memory.source || 'Unknown'}</div>
                        </div>
                    </div>
                </div>
                
                ${memory.tags && memory.tags.length > 0 ? `
                <div class="tbx-memory-detail__section">
                    <h4>Tags</h4>
                    <div class="tbx-memory-item__tags">
                        ${memory.tags.map(tag => `<span class="tbx-memory-item__tag">${this.escapeHtml(tag)}</span>`).join('')}
                    </div>
                </div>
                ` : ''}
                
                ${memory.context ? `
                <div class="tbx-memory-detail__section">
                    <h4>Context</h4>
                    <div class="tbx-memory-detail__content">${this.escapeHtml(memory.context)}</div>
                </div>
                ` : ''}
            </div>
        `;
        
        this.modal.style.display = 'flex';
    }

    closeModal() {
        this.modal.style.display = 'none';
        this.currentMemory = null;
    }

    async deleteCurrentMemory() {
        if (!this.currentMemory) return;
        
        if (!confirm('Are you sure you want to delete this memory? This action cannot be undone.')) {
            return;
        }
        
        await this.deleteMemory(this.currentMemory.id);
        this.closeModal();
    }

    async deleteMemory(memoryId) {
        try {
            const response = await fetch(`/api/v2/memory/${this.streamerId}/entries/${memoryId}`, {
                method: 'DELETE'
            });
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            // Remove from local arrays
            this.memories = this.memories.filter(m => m.id !== memoryId);
            this.applyFilters();
            this.renderMemories();
            this.loadMemoryStats();
            
            this.showToast('Memory deleted successfully', 'success');
            
        } catch (error) {
            console.error('Failed to delete memory:', error);
            this.showToast('Failed to delete memory', 'error');
        }
    }

    async clearAllMemories() {
        if (!confirm('Are you sure you want to clear ALL memories? This action cannot be undone.')) {
            return;
        }
        
        const confirmText = prompt('Type "DELETE ALL" to confirm:');
        if (confirmText !== 'DELETE ALL') {
            return;
        }
        
        try {
            const response = await fetch(`/api/v2/memory/${this.streamerId}/clear`, {
                method: 'POST'
            });
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            this.memories = [];
            this.filteredMemories = [];
            this.renderMemories();
            this.loadMemoryStats();
            
            this.showToast('All memories cleared', 'success');
            
        } catch (error) {
            console.error('Failed to clear memories:', error);
            this.showToast('Failed to clear memories', 'error');
        }
    }

    async exportMemories() {
        try {
            const response = await fetch(`/api/v2/memory/${this.streamerId}/export`);
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `memories_${this.streamerId}_${new Date().toISOString().split('T')[0]}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
            
            this.showToast('Memories exported successfully', 'success');
            
        } catch (error) {
            console.error('Failed to export memories:', error);
            this.showToast('Failed to export memories', 'error');
        }
    }

    importMemories() {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = '.json';
        
        input.onchange = async (e) => {
            const file = e.target.files[0];
            if (!file) return;
            
            try {
                const text = await file.text();
                const data = JSON.parse(text);
                
                const response = await fetch(`/api/v2/memory/${this.streamerId}/import`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(data)
                });
                
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                
                this.loadMemories();
                this.loadMemoryStats();
                
                this.showToast('Memories imported successfully', 'success');
                
            } catch (error) {
                console.error('Failed to import memories:', error);
                this.showToast('Failed to import memories', 'error');
            }
        };
        
        input.click();
    }

    async saveMemoryConfig() {
        try {
            const config = {
                retention: parseInt(this.retentionSelect.value),
                limit: parseInt(this.limitInput.value),
                autoConsolidation: this.autoConsolidationToggle.checked,
                privacyMode: this.privacyModeToggle.checked
            };
            
            const response = await fetch(`/api/v2/memory/${this.streamerId}/config`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(config)
            });
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            this.showToast('Memory configuration saved', 'success');
            
        } catch (error) {
            console.error('Failed to save config:', error);
            this.showToast('Failed to save configuration', 'error');
        }
    }

    // Pagination methods
    goToPreviousPage() {
        if (this.currentPage > 1) {
            this.currentPage--;
            this.renderMemories();
            this.updatePagination();
        }
    }

    goToNextPage() {
        if (this.currentPage < this.totalPages) {
            this.currentPage++;
            this.renderMemories();
            this.updatePagination();
        }
    }

    resetPagination() {
        this.currentPage = 1;
        this.totalPages = Math.ceil(this.filteredMemories.length / this.pageSize);
        this.updatePagination();
    }

    updatePagination() {
        this.totalPages = Math.ceil(this.filteredMemories.length / this.pageSize);
        
        this.currentPageSpan.textContent = this.currentPage;
        this.totalPagesSpan.textContent = this.totalPages;
        
        this.prevPageBtn.disabled = this.currentPage <= 1;
        this.nextPageBtn.disabled = this.currentPage >= this.totalPages;
        
        this.pagination.style.display = this.totalPages > 1 ? 'flex' : 'none';
    }

    // UI state methods
    showLoading() {
        this.loadingState.style.display = 'flex';
        this.emptyState.style.display = 'none';
    }

    hideLoading() {
        this.loadingState.style.display = 'none';
    }

    showEmptyState() {
        this.emptyState.style.display = 'flex';
        this.memoryList.innerHTML = '';
    }

    hideEmptyState() {
        this.emptyState.style.display = 'none';
    }

    showError(message) {
        this.hideLoading();
        this.memoryList.innerHTML = `
            <div class="tbx-error-state">
                <svg class="tbx-error-state__icon" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/>
                </svg>
                <h3 class="tbx-heading tbx-heading--md">Error Loading Memories</h3>
                <p class="tbx-text tbx-text--muted">${message}</p>
                <button class="tbx-btn tbx-btn--primary" onclick="location.reload()">
                    Retry
                </button>
            </div>
        `;
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

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Utility function for debouncing
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            timerManager.clearTimeout(timeout);
            func(...args);
        };
        timerManager.clearTimeout(timeout);
        timeout = timerManager.setTimeout(later, wait);
    };
}

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    new MemorySystem();
});
// Clean up timers on page unload
if (typeof window !== 'undefined') {
    window.addEventListener('beforeunload', () => {
        if (typeof timerManager !== 'undefined') {
            timerManager.clearAll();
        }
    });
}
