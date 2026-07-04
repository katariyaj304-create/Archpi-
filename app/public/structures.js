/*
 * ArchPi shared structure selector.
 *
 * One selected building drives every page. This module fetches the registry
 * from the forensic backend (:8010), remembers the choice in localStorage so
 * it persists across pages, populates any <select class="js-structure-select">
 * on the page, and notifies subscribers when the selection changes.
 *
 * Usage on a page:
 *     ArchPiStructure.onChange(site => { ... drive the page with site ... });
 *     ArchPiStructure.init();          // after the DOM (and selects) exist
 *
 * `site` is { site_id, name, location, period, blurb, lat, lon }.
 */
window.ArchPiStructure = (function () {
    const API = 'http://localhost:8010';
    const KEY = 'archpi.structure';

    let sites = [];
    let currentId = null;
    const listeners = [];

    function current() {
        return sites.find(s => s.site_id === currentId) || null;
    }

    function emit() {
        const site = current();
        if (site) listeners.forEach(cb => cb(site));
    }

    function syncSelects() {
        document.querySelectorAll('.js-structure-select').forEach(sel => {
            if (sel.value !== currentId) sel.value = currentId;
        });
    }

    function set(id) {
        if (!sites.some(s => s.site_id === id) || id === currentId) return;
        currentId = id;
        try { localStorage.setItem(KEY, id); } catch (e) { /* private mode */ }
        syncSelects();
        emit();
    }

    function populate() {
        const opts = sites
            .map(s => '<option value="' + s.site_id + '">' + s.name + '</option>')
            .join('');
        document.querySelectorAll('.js-structure-select').forEach(sel => {
            sel.innerHTML = opts;
            sel.value = currentId;
            sel.addEventListener('change', () => set(sel.value));
        });
    }

    function onChange(cb) {
        listeners.push(cb);
        if (current()) cb(current());   // fire immediately if already loaded
    }

    let started = false;
    async function init() {
        if (started) { populate(); syncSelects(); return sites; }
        started = true;
        const res = await fetch(API + '/api/v1/sites');
        if (!res.ok) throw new Error('sites endpoint returned HTTP ' + res.status);
        sites = (await res.json()).sites;
        if (!sites.length) throw new Error('no structures registered');

        let stored = null;
        try { stored = localStorage.getItem(KEY); } catch (e) { /* private mode */ }
        currentId = sites.some(s => s.site_id === stored) ? stored : sites[0].site_id;

        populate();
        emit();

        // Keep pages in sync if the choice changes in another tab.
        window.addEventListener('storage', ev => {
            if (ev.key === KEY && ev.newValue && ev.newValue !== currentId) {
                currentId = ev.newValue;
                syncSelects();
                emit();
            }
        });
        return sites;
    }

    return { init, set, onChange, current, list: () => sites };
})();
