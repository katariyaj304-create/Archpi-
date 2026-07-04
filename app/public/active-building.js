/*
 * ArchPi global active building.
 *
 * One free-text building name drives the whole app. Selecting a structure
 * anywhere (home search, globe pin, or opening any page with ?q=) persists
 * it in localStorage; every page then falls back to it when opened without
 * a ?q= parameter — so the analysis follows the user from page to page
 * until they manually pick a different structure.
 *
 * Usage on a page:
 *     var buildingName = ArchPiBuilding.resolve();   // ?q= wins, else stored
 *     ArchPiBuilding.set('Taj Mahal');               // manual selection
 *
 * Elements with class "js-active-building" are filled with the current name.
 */
window.ArchPiBuilding = (function () {
    var KEY = 'archpi.activeBuilding';

    function get() {
        try { return (localStorage.getItem(KEY) || '').trim(); } catch (e) { return ''; }
    }

    function set(name) {
        name = (name || '').trim();
        if (!name) return;
        try { localStorage.setItem(KEY, name); } catch (e) { /* private mode */ }
        render();
    }

    function clear() {
        try { localStorage.removeItem(KEY); } catch (e) { /* private mode */ }
        render();
    }

    // The page's ?q= parameter wins and becomes the new global selection;
    // otherwise the stored building carries over from previous pages.
    function resolve() {
        var q = new URLSearchParams(window.location.search).get('q');
        if (q && q.trim()) { set(q); return q.trim(); }
        return get();
    }

    function render() {
        var name = get();
        document.querySelectorAll('.js-active-building').forEach(function (el) {
            el.textContent = name || 'None selected';
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', render);
    } else {
        render();
    }

    return { get: get, set: set, clear: clear, resolve: resolve };
})();
