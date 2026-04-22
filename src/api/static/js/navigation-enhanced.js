// Navigation Enhanced JavaScript
// Provides enhanced navigation functionality for TalkBot dashboard

document.addEventListener('DOMContentLoaded', () => {
    // Get current path
    const currentPath = window.location.pathname;
    
    // Find all navigation links
    const navLinks = document.querySelectorAll('.nav-link, .tbx-nav-link, a[href^="/"]');
    
    navLinks.forEach(link => {
        // Check if link matches current path
        const linkPath = link.getAttribute('href');
        if (linkPath === currentPath) {
            link.classList.add('active');
            link.classList.add('nav-active');
        }
        
        // Add click handler for smooth navigation
        link.addEventListener('click', (e) => {
            // Remove active class from all links
            navLinks.forEach(l => {
                l.classList.remove('active', 'nav-active');
            });
            // Add active class to clicked link
            link.classList.add('active', 'nav-active');
        });
    });
    
    // Handle mobile menu toggle
    const menuToggle = document.querySelector('.menu-toggle, .nav-toggle');
    const navMenu = document.querySelector('.nav-menu, .tbx-nav-menu, nav');
    
    if (menuToggle && navMenu) {
        menuToggle.addEventListener('click', () => {
            navMenu.classList.toggle('open');
            navMenu.classList.toggle('nav-open');
            menuToggle.classList.toggle('active');
        });
    }
});