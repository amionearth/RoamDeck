TRAVEL TICKET STUDIO

Files:
- index.html: local browser editor
- templates/travel-ticket-portrait.svg: portrait SVG template
- templates/travel-ticket-landscape.svg: landscape SVG template

How to run:
1. Extract the ZIP to a folder.
2. Open a terminal/CMD in that folder.
3. Run: python -m http.server 8000
4. Open http://localhost:8000 in your browser.

Do not open index.html directly as a file: the app fetches the SVG templates, so use the local server.

Features:
- Choose portrait or landscape ticket.
- Edit destination, distance, time taken, rating, date, tagline and accent color.
- Upload a photo or map screenshot.
- Add a URL and export a QR code embedded in the SVG.
- Export SVG or PNG.

QR note:
QR export uses api.qrserver.com and needs an internet connection. The rest of the editor runs locally. Uploaded photos are embedded in the exported SVG. For the cleanest results, use a wide-ish photo for landscape and portrait-oriented image for portrait tickets.

The exported SVG is editable in Inkscape, Illustrator, Figma (SVG import), or a text editor.
