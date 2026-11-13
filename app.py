"""
Gemini Watermark Remover - static site + fully client-side removal engine.

All watermark removal runs in the visitor's browser (OpenCV.js + WebCodecs):
photos and videos are cleaned on-device, nothing is ever uploaded anywhere.
The server only hosts the localized pages and static assets.
"""
import os
import time

from flask import Flask, request, render_template, Response, redirect
from flask_cors import CORS

from translations import LANGS, META, merged

app = Flask(__name__, static_folder="static")
CORS(app)


# ---------------- i18n: multi-language multi-page site ----------------
SEO_PAGES = {
    "how-it-works": "how-it-works.html",
    "features": "features.html",
    "guides": "guides.html",
    "guides/remove-gemini-watermark-from-photos": "guide-photo.html",
    "guides/remove-gemini-watermark-from-videos": "guide-video.html",
    "guides/what-is-the-gemini-watermark": "guide-what.html",
    "faq": "faq.html",
    "about": "about.html",
    "privacy": "privacy.html",
    "terms": "terms.html",
}


def detect_lang():
    """Pick the best language from the Accept-Language header."""
    header = request.headers.get("Accept-Language", "")
    best, bestq = "en", 0.0
    for part in header.split(","):
        seg = part.strip().split(";q=")
        base = seg[0].strip().lower().split("-")[0]
        try:
            q = float(seg[1]) if len(seg) > 1 else 1.0
        except ValueError:
            q = 0.0
        if base in LANGS and q > 0 and q > bestq:
            best, bestq = base, q
    return best


@app.context_processor
def i18n_context():
    """Expose the merged translation dict, language prefix and hreflang
    alternates for the current path to every template."""
    parts = request.path.split("/")
    lang = parts[1] if len(parts) > 1 and parts[1] in LANGS else "en"
    rest = "/" + "/".join(parts[2:]) if lang in LANGS and len(parts) > 2 else request.path
    if lang not in LANGS:
        rest = request.path
    root = request.url_root.rstrip("/")
    if rest == "/" or rest == "":
        alt = {l: f"{root}/{l}/" for l in LANGS}
        self_url = f"{root}/{lang}/"
    else:
        alt = {l: f"{root}/{l}{rest}" for l in LANGS}
        self_url = f"{root}/{lang}{rest}"
    return {
        "lang": lang,
        "lang_prefix": "/" + lang,
        "t": merged(lang),
        "langs": LANGS,
        "meta_l": META,
        "alt": alt,
        "self_url": self_url,
        "site_url": root,
        "year": time.strftime("%Y"),
    }


# landing page: /<lang>/
@app.route("/<lang>/")
def landing(lang):
    if lang not in LANGS:
        return render_template("404.html"), 404
    return render_template("landing.html")


# tool page: /<lang>/remover
@app.route("/<lang>/remover")
def remover_lang(lang):
    if lang not in LANGS:
        return render_template("404.html"), 404
    return render_template("tool.html")


# /remover serves the English tool page
@app.route("/remover")
def remover_en():
    return render_template("tool.html")


# legacy English-only SEO routes -> permanent redirects to /en/...
def _legacy(page):
    def red():
        return redirect(f"/en/{page}", code=301)
    return red


# localized SEO pages
def _page(tmpl):
    def view(**kwargs):
        return render_template(tmpl)
    return view


for _page_path, _tmpl in SEO_PAGES.items():
    app.add_url_rule(f"/<lang>/{_page_path}",
                     f"page_{_page_path.replace('/', '_').replace('-', '_')}",
                     _page(_tmpl))
    app.add_url_rule(f"/{_page_path}",
                     f"legacy_{_page_path.replace('/', '_').replace('-', '_')}",
                     _legacy(_page_path))


# root: detect the visitor's language and 302 to the localized landing page
@app.route("/")
def detect():
    resp = redirect(f"/{detect_lang()}/", code=302)
    resp.headers["Vary"] = "Accept-Language"
    return resp


@app.route("/sitemap.xml")
def sitemap_xml():
    root = request.url_root.rstrip("/")
    # (path, changefreq, priority) — "" is the localized landing page
    entries = [
        ("", "daily", "1.0"),
        ("remover", "weekly", "0.9"),
        ("how-it-works", "weekly", "0.8"),
        ("features", "weekly", "0.8"),
        ("guides", "weekly", "0.8"),
        ("guides/remove-gemini-watermark-from-photos", "monthly", "0.8"),
        ("guides/remove-gemini-watermark-from-videos", "monthly", "0.8"),
        ("guides/what-is-the-gemini-watermark", "monthly", "0.7"),
        ("faq", "monthly", "0.8"),
        ("about", "monthly", "0.4"),
        ("privacy", "yearly", "0.3"),
        ("terms", "yearly", "0.3"),
    ]
    urls = []
    for path, freq, prio in entries:
        for l in LANGS:
            loc = f"{root}/{l}/" if not path else f"{root}/{l}/{path}"
            alts = {a: (f"{root}/{a}/" if not path else f"{root}/{a}/{path}")
                    for a in LANGS}
            urls.append({"loc": loc, "freq": freq, "prio": prio, "alts": alts})
    xml = render_template("sitemap.xml", urls=urls)
    return Response(xml, mimetype="application/xml")


@app.route("/healthz")
def healthz():
    return Response("ok", mimetype="text/plain")


@app.route("/robots.txt")
def robots_txt():
    root = request.url_root.rstrip("/")
    body = ("User-agent: *\n"
            "Allow: /\n\n"
            f"Sitemap: {root}/sitemap.xml\n")
    return Response(body, mimetype="text/plain")


@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"Gemini Watermark Remover running at http://127.0.0.1:{port}")
    app.run(host=host, port=port, threaded=True)
