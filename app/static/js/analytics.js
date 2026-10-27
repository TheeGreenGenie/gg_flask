/* Google Analytics 4, loaded only after the visitor accepts analytics cookies.
   - No Google request is made before consent, after "Decline", or when the browser sends
     Global Privacy Control (treated as a decline).
   - Pages describe ecommerce events with [data-gg-analytics-event] elements (JSON); they are
     sent once GA has loaded. window.GGAnalytics.event() lets other scripts add events. */
(function () {
	'use strict';

	var meta = document.querySelector('meta[name="gg-ga4"]');
	var id = meta && meta.content;
	if (!id || !/^G-[A-Z0-9]+$/.test(id)) { return; }

	var KEY = 'gg-analytics-consent';
	var banner = document.querySelector('[data-gg-consent]');
	var queue = [];
	var loaded = false;

	function getChoice() {
		try { return window.localStorage.getItem(KEY); } catch (e) { return null; }
	}
	function setChoice(value) {
		try { window.localStorage.setItem(KEY, value); } catch (e) { /* ignore */ }
	}

	function gtag() { window.dataLayer.push(arguments); }

	function load() {
		if (loaded) { return; }
		loaded = true;
		window.dataLayer = window.dataLayer || [];
		window.gtag = gtag;
		gtag('js', new Date());
		gtag('config', id);
		var s = document.createElement('script');
		s.async = true;
		s.src = 'https://www.googletagmanager.com/gtag/js?id=' + encodeURIComponent(id);
		document.head.appendChild(s);
		queue.splice(0).forEach(function (e) { gtag('event', e[0], e[1]); });
	}

	function event(name, params) {
		if (loaded) { gtag('event', name, params || {}); }
		else if (getChoice() !== 'denied') { queue.push([name, params || {}]); }
	}
	window.GGAnalytics = { event: event };

	document.querySelectorAll('[data-gg-analytics-event]').forEach(function (el) {
		try {
			var data = JSON.parse(el.getAttribute('data-gg-analytics-event'));
			if (data && data.name) {
				if (data.once) {
					// e.g. purchase: never report the same order twice on reload.
					var seenKey = 'gg-ga-sent-' + data.once;
					try {
						if (window.localStorage.getItem(seenKey)) { return; }
						window.localStorage.setItem(seenKey, '1');
					} catch (e) { /* ignore */ }
				}
				event(data.name, data.params);
			}
		} catch (e) { /* ignore malformed data */ }
	});

	function showBanner() { if (banner) { banner.hidden = false; } }
	function hideBanner() { if (banner) { banner.hidden = true; } }

	if (banner) {
		banner.querySelector('[data-gg-consent-accept]').addEventListener('click', function () {
			setChoice('granted'); hideBanner(); load();
		});
		banner.querySelector('[data-gg-consent-decline]').addEventListener('click', function () {
			setChoice('denied'); hideBanner(); queue = [];
		});
	}
	document.querySelectorAll('[data-gg-consent-open]').forEach(function (link) {
		link.addEventListener('click', function (e) { e.preventDefault(); showBanner(); });
	});

	var choice = getChoice();
	if (navigator.globalPrivacyControl === true && choice !== 'granted') {
		setChoice('denied');
		return;
	}
	if (choice === 'granted') { load(); }
	else if (choice !== 'denied') { showBanner(); }
})();
