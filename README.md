# 🕷️ Site Crawler

**Full website crawling with interactive 3D visualization of site architecture.**

An ultra-fast asynchronous Python tool that crawls entire websites, analyzes their link structure, and generates an interactive 3D visualization in the browser.

---

## ✨ Features

- **Parallel crawling** — Up to 20 simultaneous workers powered by Playwright
- **Complete analysis** — Internal/external links, orphan pages, dead-ends detection
- **Smart detection** — Bidirectional links, navigation hubs, folder-based grouping
- **3D visualization** — Three.js interface with rotation, zoom, pan and node selection
- **Multi-format export** — Structured JSON + TXT report + HTML visualization

---

## 📦 Installation

### Prerequisites

- Python 3.8+
- pip

### Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### Or one-click (Windows)

```bash
run.bat
```

---

## 🚀 Usage

### Launch

```bash
python site-crawler-fixed\ v37.py
```

Enter the target URL when prompted:

```
URL: example.com
```

### Output

Files are generated in `crawl_results/<domain>/`:

| File | Description |
|------|-------------|
| `site_structure.json` | Complete data (pages, links, metrics) |
| `site_structure.txt` | Human-readable report with statistics |
| `visualize.html` | Interactive 3D visualization |

---

## 🎮 Visualization Controls

| Action | Control |
|--------|---------|
| Rotate | Left click + drag |
| Zoom | Mouse wheel |
| Pan | Right click + drag |
| Select node | Left click on sphere |

### Display options

- **Direction arrows** — Show link direction
- **Page names** — Display labels above nodes
- **Auto-rotate** — Continuous camera rotation

---

## 🎨 Node Legend

| Color | Meaning |
|-------|---------|
| 🔴 Red | Root page |
| 🔵 Blue | Normal page |
| 🩵 Cyan | Hub (>10 links) |
| 🟠 Orange | Orphan (no incoming links) |
| 🔴 Light red | Dead-end (no outgoing links) |

| Link Color | Meaning |
|------------|---------|
| Blue | Normal link |
| Green | Bidirectional link |

---

## 📊 JSON Structure

```json
{
  "root": "https://example.com",
  "domain": "example.com",
  "statistics": {
    "total_pages": 42,
    "total_internal_links": 156,
    "total_external_links": 23,
    "orphan_pages": 2,
    "dead_end_pages": 5,
    "bidirectional_links": 12
  },
  "analysis": {
    "orphans": ["..."],
    "dead_ends": ["..."],
    "bidirectional": [["url1", "url2"]],
    "path_groups": {"/blog": ["..."], "/docs": ["..."]}
  },
  "pages": {
    "https://example.com/page": {
      "title": "Page Title",
      "internal_links": ["..."],
      "external_links": ["..."],
      "incoming_count": 5,
      "outgoing_count": 12
    }
  }
}
```

---

## ⚙️ Configuration

Edit these constants in the Python file:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_workers` | 20 | Number of parallel crawlers |
| `timeout` | 15000 | Page load timeout (ms) |

---

## 📝 License

MIT

---

## 🤝 Contributing

Pull requests welcome. For major changes, please open an issue first.
