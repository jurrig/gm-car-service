// Register service worker for PWA
if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        navigator.serviceWorker
            .register('/sw.js')
            .then(reg => console.log('Service Worker registered:', reg.scope))
            .catch(err => console.error('SW registration failed:', err));
    });
}

console.log('GM Car Service app loaded');
