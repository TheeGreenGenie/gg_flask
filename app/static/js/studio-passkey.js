/* Staff Studio phone keys (WebAuthn passkeys).
   [data-gg-key-register]: create a key after the owner confirmed it (Studio > Sign-in key).
   [data-gg-key-approve]:  approve one's own sign-in on the waiting screen.
   Each button carries data-options (GET, JSON options) and data-submit (POST the result). */
(function () {
	'use strict';

	function toBytes(value) {
		var base64 = value.replace(/-/g, '+').replace(/_/g, '/');
		var padded = base64 + '==='.slice((base64.length + 3) % 4);
		var raw = atob(padded);
		var out = new Uint8Array(raw.length);
		for (var i = 0; i < raw.length; i += 1) { out[i] = raw.charCodeAt(i); }
		return out.buffer;
	}

	function toBase64url(buffer) {
		var bytes = new Uint8Array(buffer);
		var text = '';
		for (var i = 0; i < bytes.length; i += 1) { text += String.fromCharCode(bytes[i]); }
		return btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
	}

	function csrf() {
		var meta = document.querySelector('meta[name="csrf-token"]');
		return meta ? meta.getAttribute('content') : '';
	}

	function showError(message) {
		var box = document.querySelector('[data-gg-key-error]');
		if (box) { box.textContent = message; box.hidden = false; }
	}

	function getJSON(url) {
		return fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
			.then(function (r) { return r.json().then(function (d) { if (!r.ok) { throw new Error(d.error || 'Please try again.'); } return d; }); });
	}

	function postJSON(url, body) {
		return fetch(url, {
			method: 'POST',
			credentials: 'same-origin',
			headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-CSRFToken': csrf() },
			body: JSON.stringify(body)
		}).then(function (r) { return r.json().then(function (d) { if (!r.ok) { throw new Error(d.error || 'Please try again.'); } return d; }); });
	}

	function encodeCredential(cred) {
		var out = { id: cred.id, rawId: toBase64url(cred.rawId), type: cred.type, response: {}, clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {} };
		var r = cred.response;
		out.response.clientDataJSON = toBase64url(r.clientDataJSON);
		if (r.attestationObject) {
			out.response.attestationObject = toBase64url(r.attestationObject);
			if (r.getTransports) { out.response.transports = r.getTransports(); }
		} else {
			out.response.authenticatorData = toBase64url(r.authenticatorData);
			out.response.signature = toBase64url(r.signature);
			out.response.userHandle = r.userHandle ? toBase64url(r.userHandle) : null;
		}
		if (cred.authenticatorAttachment) { out.authenticatorAttachment = cred.authenticatorAttachment; }
		return out;
	}

	function run(button, create) {
		if (!window.PublicKeyCredential) {
			showError('This browser cannot use sign-in keys. Use Safari on iPhone or Chrome on Android.');
			return;
		}
		button.disabled = true;
		getJSON(button.dataset.options).then(function (o) {
			o.challenge = toBytes(o.challenge);
			if (create) {
				o.user.id = toBytes(o.user.id);
				(o.excludeCredentials || []).forEach(function (c) { c.id = toBytes(c.id); });
				return navigator.credentials.create({ publicKey: o });
			}
			(o.allowCredentials || []).forEach(function (c) { c.id = toBytes(c.id); });
			return navigator.credentials.get({ publicKey: o });
		}).then(function (cred) {
			if (!cred) { throw new Error('No key was chosen.'); }
			return postJSON(button.dataset.submit, encodeCredential(cred));
		}).then(function (result) {
			window.location.assign(result.next);
		}).catch(function (error) {
			button.disabled = false;
			showError(error && error.name === 'NotAllowedError' ? 'The key request was cancelled or timed out. Try again.' : (error.message || 'Please try again.'));
		});
	}

	document.querySelectorAll('[data-gg-key-register]').forEach(function (button) {
		button.addEventListener('click', function () { run(button, true); });
	});
	document.querySelectorAll('[data-gg-key-approve]').forEach(function (button) {
		button.addEventListener('click', function () { run(button, false); });
	});
})();
