/* Homepage enhancement, ported from the WordPress child theme (assets/js/gg-homepage.js).
   Header offset measurement moved to site.js. */
(function () {
	'use strict';

	var home = document.querySelector('[data-gg-home]');
	if (!home) {
		return;
	}

	var root = document.documentElement;
	var reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

	function activateTab(group, buttons, panels, nextIndex, moveFocus) {
		buttons.forEach(function (button, index) {
			var selected = index === nextIndex;
			button.setAttribute('aria-selected', selected ? 'true' : 'false');
			button.setAttribute('tabindex', selected ? '0' : '-1');
			if (panels[index]) {
				panels[index].hidden = !selected;
			}
		});
		group.dataset.ggActiveTab = String(nextIndex);
		if (moveFocus && buttons[nextIndex]) {
			buttons[nextIndex].focus();
		}
	}

	function initTabs() {
		var groups = Array.prototype.slice.call(home.querySelectorAll('[data-gg-tabs]'));
		var enhancedGroups = 0;

		groups.forEach(function (group, groupIndex) {
			var list = group.querySelector('[data-gg-tabs-list]');
			var buttons = list ? Array.prototype.slice.call(list.querySelectorAll('[data-gg-tab-target]')) : [];
			var panels = Array.prototype.slice.call(group.querySelectorAll(':scope > [data-gg-tab-panel]'));

			if (!list || !buttons.length || buttons.length !== panels.length) {
				return;
			}

			list.hidden = false;
			list.setAttribute('role', 'tablist');
			list.setAttribute('aria-label', list.dataset.ggTabsLabel || 'Choose a view');

			buttons.forEach(function (button, buttonIndex) {
				var panelId = button.dataset.ggTabTarget;
				var panel = document.getElementById(panelId);
				var buttonId = 'gg-home-tab-' + groupIndex + '-' + buttonIndex;

				if (!panel || panels.indexOf(panel) === -1) {
					return;
				}

				button.id = buttonId;
				button.setAttribute('role', 'tab');
				button.setAttribute('aria-controls', panelId);
				panel.setAttribute('role', 'tabpanel');
				panel.setAttribute('aria-labelledby', buttonId);

				button.addEventListener('click', function () {
					activateTab(group, buttons, panels, buttonIndex, false);
				});

				button.addEventListener('keydown', function (event) {
					var currentIndex = buttons.indexOf(event.currentTarget);
					var nextIndex = currentIndex;

					switch (event.key) {
						case 'ArrowRight':
						case 'ArrowDown':
							nextIndex = (currentIndex + 1) % buttons.length;
							break;
						case 'ArrowLeft':
						case 'ArrowUp':
							nextIndex = (currentIndex - 1 + buttons.length) % buttons.length;
							break;
						case 'Home':
							nextIndex = 0;
							break;
						case 'End':
							nextIndex = buttons.length - 1;
							break;
						default:
							return;
					}

					event.preventDefault();
					activateTab(group, buttons, panels, nextIndex, true);
				});
			});

			activateTab(group, buttons, panels, 0, false);
			enhancedGroups += 1;
		});

		if (enhancedGroups) {
			home.classList.add('gg-home--tabs-ready');
		}
	}

	function setCurrentSection(sectionId) {
		home.querySelectorAll('[data-gg-section-link]').forEach(function (link) {
			if (link.dataset.ggSectionLink === sectionId) {
				link.setAttribute('aria-current', 'location');
			} else {
				link.removeAttribute('aria-current');
			}
		});
	}

	function initSectionNavigation() {
		var sections = Array.prototype.slice.call(home.querySelectorAll('[data-gg-section]'));
		var links = Array.prototype.slice.call(home.querySelectorAll('[data-gg-section-link]'));

		if (!sections.length || !links.length || !('IntersectionObserver' in window)) {
			return false;
		}

		links.forEach(function (link) {
			link.addEventListener('click', function () {
				setCurrentSection(link.dataset.ggSectionLink);
			});
		});

		var visibility = new Map();
		var observer = new IntersectionObserver(
			function (entries) {
				entries.forEach(function (entry) {
					visibility.set(entry.target.id, entry.isIntersecting ? entry.intersectionRatio : 0);
				});

				var currentId = '';
				var currentRatio = 0;
				sections.forEach(function (section) {
					var ratio = visibility.get(section.id) || 0;
					if (ratio > currentRatio) {
						currentRatio = ratio;
						currentId = section.id;
					}
				});

				if (currentId) {
					setCurrentSection(currentId);
				}
			},
			{ root: null, rootMargin: '-20% 0px -55% 0px', threshold: [0, 0.1, 0.25, 0.5, 0.75] }
		);

		sections.forEach(function (section) {
			observer.observe(section);
		});
		return true;
	}

	function initVideoFacades() {
		home.querySelectorAll('[data-gg-video]').forEach(function (facade) {
			facade.addEventListener(
				'click',
				function () {
					var videoId = facade.dataset.ggVideoId;
					if (!videoId || !/^[A-Za-z0-9_-]{6,20}$/.test(videoId)) {
						return;
					}
					var frame = document.createElement('iframe');
					frame.src = 'https://www.youtube-nocookie.com/embed/' + encodeURIComponent(videoId) + '?autoplay=1&rel=0';
					frame.title = 'GG Afrikan Eleganz brand story';
					frame.allow = 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share';
					frame.allowFullscreen = true;
					frame.referrerPolicy = 'strict-origin-when-cross-origin';
					facade.replaceWith(frame);
					frame.focus();
				},
				{ once: true }
			);
		});
	}

	/* Hero image slider: crossfade every 6s; pausable; no autoplay with reduced motion. */
	function initSlider() {
		var stage = home.querySelector('[data-gg-slider]');
		var controls = home.querySelector('[data-gg-slider-controls]');
		if (!stage || !controls) {
			return;
		}
		var slides = Array.prototype.slice.call(stage.querySelectorAll('[data-gg-slide]'));
		var dots = Array.prototype.slice.call(controls.querySelectorAll('[data-gg-slider-dot]'));
		var pause = controls.querySelector('[data-gg-slider-pause]');
		if (slides.length < 2) {
			return;
		}
		var current = 0;
		var timer = null;
		var paused = reducedMotion.matches;

		slides.forEach(function (img) { img.loading = 'eager'; });

		function show(index) {
			current = (index + slides.length) % slides.length;
			slides.forEach(function (img, i) { img.classList.toggle('is-active', i === current); });
			dots.forEach(function (dot, i) {
				if (i === current) { dot.setAttribute('aria-current', 'true'); } else { dot.removeAttribute('aria-current'); }
			});
		}

		function schedule() {
			window.clearInterval(timer);
			timer = paused ? null : window.setInterval(function () { show(current + 1); }, 6000);
			pause.setAttribute('aria-pressed', paused ? 'true' : 'false');
			pause.textContent = paused ? 'Play slideshow' : 'Pause slideshow';
		}

		dots.forEach(function (dot, i) {
			dot.addEventListener('click', function () { show(i); schedule(); });
		});
		pause.addEventListener('click', function () { paused = !paused; schedule(); });
		document.addEventListener('visibilitychange', function () {
			if (document.hidden) { window.clearInterval(timer); } else { schedule(); }
		});

		controls.hidden = false;
		schedule();
	}

	try {
		initSlider();
		initTabs();
		initVideoFacades();
		if (initSectionNavigation()) {
			root.classList.add('gg-home-enhanced');
		}
		root.classList.toggle('gg-home-reduced-motion', reducedMotion.matches);
		if (reducedMotion.addEventListener) {
			reducedMotion.addEventListener('change', function () {
				root.classList.toggle('gg-home-reduced-motion', reducedMotion.matches);
			});
		}
	} catch (error) {
		root.classList.remove('gg-home-enhanced');
		if (window.console && typeof window.console.error === 'function') {
			window.console.error('GG homepage enhancement failed safely.', error);
		}
	}
})();
