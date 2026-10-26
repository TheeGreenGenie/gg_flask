/* Runs before first paint (loaded without defer) so a saved theme never flashes. */
(function () {
	try {
		var t = window.localStorage.getItem('gg-theme');
		if (t === 'dark' || t === 'light') { document.documentElement.setAttribute('data-theme', t); }
	} catch (e) { /* storage blocked: follow the device setting */ }
})();
