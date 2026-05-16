// Nerd Font loader for JupyterLab terminal
// 1. Loads font via FontFace API for canvas rendering
// 2. Detects theme and writes /tmp/.starship-theme for shell to pick up
(function() {
    // Load font via FontFace API (guaranteed to work for Canvas fillText)
    var regular = new FontFace("FiraCode Nerd Font", "url(/static/lab/fonts/FiraCodeNerdFontMono-Regular.woff2)");
    var bold = new FontFace("FiraCode Nerd Font", "url(/static/lab/fonts/FiraCodeNerdFontMono-Bold.woff2)", {weight: "700"});
    Promise.all([regular.load(), bold.load()]).then(function(fonts) {
        fonts.forEach(function(f) { document.fonts.add(f); });
    });
    document.fonts.load('14px "FiraCode Nerd Font"');

    // Detect theme and write to /tmp/.starship-theme via a hidden terminal
    function getTheme() {
        if (!document.body) return "dark";
        return document.body.getAttribute("data-jp-theme-light") === "false" ? "dark" : "light";
    }

    function writeTheme() {
        var theme = getTheme();
        var xsrf = "";
        document.cookie.split(";").forEach(function(c) {
            c = c.trim();
            if (c.startsWith("_xsrf=")) xsrf = c.substring(6);
        });
        // Create a hidden terminal to write the theme file
        fetch("/api/terminals", {
            method: "POST",
            headers: {"Content-Type": "application/json", "X-XSRFToken": xsrf}
        }).then(function(r) { return r.json(); }).then(function(t) {
            var proto = location.protocol === "https:" ? "wss:" : "ws:";
            var base = location.pathname.replace(/\/lab.*/, "");
            var ws = new WebSocket(proto + "//" + location.host + base + "/terminals/websocket/" + t.name);
            ws.onopen = function() {
                ws.send(JSON.stringify(["stdin", "echo " + theme + " > /tmp/.starship-theme && exit\r"]));
                setTimeout(function() { ws.close(); }, 1000);
            };
        }).catch(function() {});
    }

    function init() {
        writeTheme();
        new MutationObserver(function() {
            writeTheme();
        }).observe(document.body, {
            attributes: true,
            attributeFilter: ["data-jp-theme-light", "data-jp-theme-name"]
        });
    }

    if (document.body) init();
    else document.addEventListener("DOMContentLoaded", init);
})();
