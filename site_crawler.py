"""
Site Crawler - Parcours exhaustif et reconstruction d'arborescence
Version: 3.0 - Async parallélisé
"""

from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import json
import asyncio
from datetime import datetime
import re
import os

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout


class SiteCrawler:
    def __init__(self, base_url, max_workers=20):
        self.base_url = self._ensure_scheme(base_url)
        self.base_url = self.normalize_url(self.base_url)
        self.domain = urlparse(self.base_url).netloc
        self.max_workers = max_workers
        
        self.visited_urls = set()
        self.to_visit = asyncio.Queue()
        self.site_structure = {}
        self.external_links = {}
        
        self.lock = asyncio.Lock()
        self.page_count = 0
        self.active_workers = 0
        self.done = asyncio.Event()
    
    def _ensure_scheme(self, url):
        if not url.startswith(('http://', 'https://')):
            return f'https://{url}'
        return url
    
    def normalize_url(self, url):
        parsed = urlparse(url)
        path = parsed.path.rstrip('/') or '/'
        normalized = f"{parsed.scheme}://{parsed.netloc}{path}"
        if parsed.query:
            normalized += f"?{parsed.query}"
        return normalized
    
    def is_internal_link(self, url):
        parsed = urlparse(url)
        url_domain = parsed.netloc.replace('www.', '')
        base_domain = self.domain.replace('www.', '')
        return url_domain == base_domain
    
    def _is_crawlable_url(self, url):
        skip_extensions = (
            '.pdf', '.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp',
            '.css', '.js', '.ico', '.woff', '.woff2', '.ttf', '.eot',
            '.mp3', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm',
            '.zip', '.rar', '.7z', '.tar', '.gz', '.exe', '.dmg',
            '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx'
        )
        parsed = urlparse(url)
        return not parsed.path.lower().endswith(skip_extensions)
    
    def extract_links(self, soup, current_url, html_content=""):
        internal_links = set()
        external_links = set()
        
        for link in soup.find_all('a', href=True):
            self._process_href(link.get('href'), current_url, internal_links, external_links)
        
        if html_content:
            domain_escaped = re.escape(self.domain.replace('www.', ''))
            for match in re.finditer(rf'https?://(?:www\.)?{domain_escaped}[/\w\-?=&]*', html_content):
                self._process_href(match.group(0), current_url, internal_links, external_links)
            
            for match in re.finditer(r'"pageUriSEO"\s*:\s*"([^"]+)"', html_content):
                page_uri = match.group(1)
                if page_uri and page_uri not in ('home', 'blank'):
                    self._process_href(f"https://{self.domain}/{page_uri}", current_url, internal_links, external_links)
        
        return internal_links, external_links
    
    def _process_href(self, href, current_url, internal_links, external_links):
        if not href or href.startswith(('mailto:', 'tel:', 'javascript:', '#', 'data:', 'blob:')):
            return
        if 'static.wixstatic.com' in href or 'parastorage.com' in href:
            return
        
        absolute_url = urljoin(current_url, href)
        normalized_url = self.normalize_url(absolute_url)
        
        if not self._is_crawlable_url(normalized_url):
            return
        
        if self.is_internal_link(normalized_url):
            internal_links.add(normalized_url)
        else:
            external_links.add(normalized_url)
    
    async def crawl_page(self, page, url):
        try:
            await page.goto(url, wait_until='domcontentloaded', timeout=15000)
            await asyncio.sleep(0.2)
            
            html_content = await page.content()
            final_url = page.url
            
            soup = BeautifulSoup(html_content, 'html.parser')
            title = soup.title.string if soup.title else "Sans titre"
            title = ' '.join(title.split())
            
            internal_links, external_links = self.extract_links(soup, final_url, html_content)
            
            return {
                'url': url, 'final_url': final_url, 'title': title.strip()[:200],
                'status_code': 200, 'internal_links': list(internal_links),
                'external_links': list(external_links), 'crawled_at': datetime.now().isoformat()
            }
        except Exception as e:
            return {
                'url': url, 'title': 'Erreur', 'status_code': None,
                'error': str(e)[:100], 'internal_links': [], 'external_links': [],
                'crawled_at': datetime.now().isoformat()
            }
    
    async def worker(self, context, worker_id):
        # Crée une page réutilisable pour ce worker
        page = await context.new_page()
        
        # Bloque les ressources inutiles (images, CSS, fonts) pour accélérer
        await page.route("**/*.{png,jpg,jpeg,gif,svg,webp,ico,woff,woff2,ttf,eot,css}", 
                        lambda route: route.abort())
        
        try:
            while not self.done.is_set():
                try:
                    url = await asyncio.wait_for(self.to_visit.get(), timeout=2.0)
                except asyncio.TimeoutError:
                    if self.active_workers == 0 and self.to_visit.empty():
                        self.done.set()
                    continue
                
                async with self.lock:
                    if url in self.visited_urls:
                        self.to_visit.task_done()
                        continue
                    self.visited_urls.add(url)
                    self.page_count += 1
                    page_num = self.page_count
                    self.active_workers += 1
                
                print(f"  [{page_num:3d}] W{worker_id:02d} {url[:65]}{'...' if len(url)>65 else ''}", end="", flush=True)
                
                page_info = await self.crawl_page(page, url)
                
                async with self.lock:
                    self.active_workers -= 1
                    self.site_structure[url] = page_info
                    
                    for ext_link in page_info['external_links']:
                        if ext_link not in self.external_links:
                            self.external_links[ext_link] = []
                        self.external_links[ext_link].append(url)
                    
                    new_count = 0
                    for link in page_info['internal_links']:
                        if link not in self.visited_urls:
                            await self.to_visit.put(link)
                            new_count += 1
                
                print(f" → {len(page_info['internal_links'])}i/{len(page_info['external_links'])}e (+{new_count})")
                self.to_visit.task_done()
                
        finally:
            await page.close()
    
    async def crawl_async(self):
        print(f"\n{'='*60}")
        print(f"Crawl de: {self.base_url}")
        print(f"Domaine: {self.domain}")
        print(f"Workers: {self.max_workers}")
        print(f"{'='*60}\n")
        
        start_time = asyncio.get_event_loop().time()
        
        # Ajoute l'URL de départ
        await self.to_visit.put(self.base_url)
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                viewport={'width': 1280, 'height': 720},
                java_script_enabled=True
            )
            
            # Lance tous les workers en parallèle
            workers = [asyncio.create_task(self.worker(context, i)) for i in range(self.max_workers)]
            
            # Attend que tous les workers terminent
            await asyncio.gather(*workers)
            
            await context.close()
            await browser.close()
        
        elapsed = asyncio.get_event_loop().time() - start_time
        pages_per_sec = len(self.visited_urls) / elapsed if elapsed > 0 else 0
        
        print(f"\n{'='*60}")
        print(f"Terminé en {elapsed:.1f}s ({pages_per_sec:.1f} pages/sec)")
        print(f"Pages: {len(self.visited_urls)} | Externes: {len(self.external_links)}")
        print(f"{'='*60}\n")
    
    def crawl(self):
        asyncio.run(self.crawl_async())
    
    def generate_tree_structure(self):
        # Calcul des métriques avancées
        pages = self.site_structure
        
        # Liens entrants par page
        incoming = {url: [] for url in pages}
        for url, data in pages.items():
            for link in data.get('internal_links', []):
                if link in incoming:
                    incoming[link].append(url)
        
        # Détection des pages orphelines (0 liens entrants, sauf racine)
        orphans = [url for url, inc in incoming.items() if len(inc) == 0 and url != self.base_url]
        
        # Détection des culs-de-sac (0 liens sortants internes)
        dead_ends = [url for url, data in pages.items() if len(data.get('internal_links', [])) == 0]
        
        # Liens bidirectionnels
        bidirectional = []
        for url, data in pages.items():
            for link in data.get('internal_links', []):
                if link in pages and url in pages[link].get('internal_links', []):
                    pair = tuple(sorted([url, link]))
                    if pair not in bidirectional:
                        bidirectional.append(pair)
        
        # Construction de l'arborescence URL
        url_tree = {}
        for url in pages:
            parsed = urlparse(url)
            path_parts = [p for p in parsed.path.split('/') if p]
            
            current = url_tree
            for i, part in enumerate(path_parts):
                if part not in current:
                    current[part] = {'_children': {}, '_pages': []}
                if i == len(path_parts) - 1:
                    current[part]['_pages'].append(url)
                current = current[part]['_children']
        
        # Groupes par dossier (premier niveau de path)
        path_groups = {}
        for url in pages:
            parsed = urlparse(url)
            parts = [p for p in parsed.path.split('/') if p]
            group = '/' + parts[0] if parts else '/'
            if group not in path_groups:
                path_groups[group] = []
            path_groups[group].append(url)
        
        return {
            'root': self.base_url,
            'domain': self.domain,
            'crawled_at': datetime.now().isoformat(),
            'statistics': {
                'total_pages': len(self.visited_urls),
                'total_external_links': len(self.external_links),
                'total_internal_links': sum(len(p.get('internal_links', [])) for p in pages.values()),
                'orphan_pages': len(orphans),
                'dead_end_pages': len(dead_ends),
                'bidirectional_links': len(bidirectional)
            },
            'analysis': {
                'orphans': orphans,
                'dead_ends': dead_ends,
                'bidirectional': [list(pair) for pair in bidirectional],
                'path_groups': path_groups
            },
            'url_tree': url_tree,
            'pages': {
                url: {
                    **data,
                    'incoming_links': incoming.get(url, []),
                    'incoming_count': len(incoming.get(url, [])),
                    'outgoing_count': len(data.get('internal_links', []))
                }
                for url, data in pages.items()
            },
            'external_links': {
                link: {'url': link, 'found_on_pages': sources}
                for link, sources in self.external_links.items()
            }
        }
    
    def save_results(self, output_prefix='site_structure'):
        tree = self.generate_tree_structure()
        
        # Crée un dossier dédié au domaine (évite les doublons)
        safe_domain = re.sub(r'[^\w\-.]', '_', self.domain)
        output_dir = os.path.join('crawl_results', safe_domain)
        os.makedirs(output_dir, exist_ok=True)
        
        # Fichiers dans le sous-dossier
        json_file = os.path.join(output_dir, f"{output_prefix}.json")
        txt_file = os.path.join(output_dir, f"{output_prefix}.txt")
        
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(tree, f, indent=2, ensure_ascii=False)
        print(f"JSON sauvegardé: {json_file}")
        
        with open(txt_file, 'w', encoding='utf-8') as f:
            f.write(f"{'='*80}\nARBORESCENCE: {tree['root']}\nCrawlé le: {tree['crawled_at']}\n{'='*80}\n\n")
            s = tree['statistics']
            f.write(f"STATISTIQUES\n{'-'*40}\n")
            f.write(f"Pages crawlées: {s['total_pages']}\n")
            f.write(f"Liens internes: {s['total_internal_links']}\n")
            f.write(f"Liens externes: {s['total_external_links']}\n")
            f.write(f"Pages orphelines: {s['orphan_pages']}\n")
            f.write(f"Culs-de-sac: {s['dead_end_pages']}\n")
            f.write(f"Liens bidirectionnels: {s['bidirectional_links']}\n\n")
            
            a = tree['analysis']
            if a['orphans']:
                f.write(f"PAGES ORPHELINES (aucun lien entrant)\n{'-'*40}\n")
                for url in a['orphans']:
                    f.write(f"  ⚠ {url}\n")
                f.write("\n")
            
            if a['dead_ends']:
                f.write(f"CULS-DE-SAC (aucun lien sortant)\n{'-'*40}\n")
                for url in a['dead_ends']:
                    f.write(f"  ✗ {url}\n")
                f.write("\n")
            
            f.write(f"STRUCTURE PAR DOSSIER\n{'='*80}\n")
            for group, urls in sorted(a['path_groups'].items()):
                f.write(f"\n{group}/ ({len(urls)} pages)\n{'-'*40}\n")
                for url in sorted(urls):
                    page = tree['pages'].get(url, {})
                    f.write(f"  {url}\n")
                    f.write(f"    ↘ {page.get('outgoing_count', 0)} sortants | ↙ {page.get('incoming_count', 0)} entrants\n")
        print(f"TXT sauvegardé: {txt_file}")
        
        return json_file, txt_file, output_dir


def generate_visualization_html(json_data, output_dir='.'):
    html_template = '''<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Arborescence - {domain}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', sans-serif; background: #0a0a0a; color: #fff; overflow: hidden; }}
        #container {{ width: 100vw; height: 100vh; }}
        .panel {{ position: absolute; background: rgba(15,15,15,0.95); padding: 15px; border-radius: 8px; border: 1px solid #333; z-index: 100; font-size: 12px; }}
        #controls {{ top: 15px; left: 15px; width: 280px; }}
        #controls h2 {{ margin-bottom: 10px; font-size: 15px; color: #4a9eff; }}
        .stat-row {{ display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px solid #222; }}
        .stat-label {{ color: #888; }}
        .stat-value {{ color: #4a9eff; font-weight: bold; }}
        .stat-value.warning {{ color: #ffaa44; }}
        .stat-value.danger {{ color: #ff4444; }}
        .section {{ margin-top: 12px; padding-top: 10px; border-top: 1px solid #333; }}
        .section h3 {{ font-size: 11px; color: #666; margin-bottom: 8px; text-transform: uppercase; }}
        #groups {{ max-height: 150px; overflow-y: auto; }}
        .group-item {{ display: flex; align-items: center; padding: 3px 0; cursor: pointer; }}
        .group-item:hover {{ background: rgba(74,158,255,0.1); }}
        .group-color {{ width: 12px; height: 12px; border-radius: 3px; margin-right: 8px; }}
        .group-name {{ flex: 1; color: #aaa; font-size: 11px; }}
        .group-count {{ color: #666; font-size: 10px; }}
        #file-input {{ top: 15px; right: 15px; }}
        #file-input label {{ display: block; margin-bottom: 8px; color: #4a9eff; font-size: 11px; }}
        .legend {{ bottom: 15px; right: 15px; }}
        .legend-item {{ display: flex; align-items: center; margin: 4px 0; font-size: 11px; }}
        .legend-color {{ width: 14px; height: 14px; margin-right: 8px; border-radius: 50%; }}
        .legend-line {{ width: 20px; height: 2px; margin-right: 8px; }}
        #info {{ bottom: 15px; left: 15px; width: 350px; display: none; }}
        #info h3 {{ font-size: 13px; color: #4a9eff; margin-bottom: 8px; word-break: break-all; }}
        #info p {{ margin: 3px 0; color: #aaa; font-size: 11px; }}
        #info a {{ color: #4a9eff; }}
        #info .tag {{ display: inline-block; padding: 2px 6px; border-radius: 3px; font-size: 10px; margin: 2px; }}
        #info .tag.orphan {{ background: #442200; color: #ffaa44; }}
        #info .tag.deadend {{ background: #440000; color: #ff6666; }}
        #info .tag.hub {{ background: #003344; color: #44ddff; }}
        .toggle {{ display: flex; align-items: center; gap: 8px; margin: 6px 0; }}
        .toggle label {{ cursor: pointer; color: #888; font-size: 11px; }}
        .toggle input {{ cursor: pointer; }}
        button {{ background: #333; border: 1px solid #555; color: #fff; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 11px; margin: 4px 2px; }}
        button:hover {{ background: #444; }}
        button.active {{ background: #4a9eff; border-color: #4a9eff; }}
    </style>
</head>
<body>
    <div id="container"></div>
    <div id="controls" class="panel">
        <h2>🌐 {domain}</h2>
        <div class="stat-row"><span class="stat-label">Pages</span><span class="stat-value" id="s-pages">0</span></div>
        <div class="stat-row"><span class="stat-label">Liens internes</span><span class="stat-value" id="s-links">0</span></div>
        <div class="stat-row"><span class="stat-label">Pages orphelines</span><span class="stat-value warning" id="s-orphans">0</span></div>
        <div class="stat-row"><span class="stat-label">Culs-de-sac</span><span class="stat-value danger" id="s-deadends">0</span></div>
        <div class="stat-row"><span class="stat-label">Liens bidirectionnels</span><span class="stat-value" id="s-bidir">0</span></div>
        <div class="section">
            <h3>Affichage</h3>
            <div class="toggle"><input type="checkbox" id="showArrows" checked><label for="showArrows">Flèches de direction</label></div>
            <div class="toggle"><input type="checkbox" id="showLabels"><label for="showLabels">Noms des pages</label></div>
            <div class="toggle"><input type="checkbox" id="autoRotate" checked><label for="autoRotate">Rotation auto</label></div>
        </div>
        <div class="section">
            <h3>Dossiers</h3>
            <div id="groups"></div>
        </div>
    </div>
    <div id="file-input" class="panel">
        <label>Charger un JSON :</label>
        <input type="file" id="jsonFile" accept=".json">
    </div>
    <div class="legend panel">
        <div class="legend-item"><div class="legend-color" style="background:#ff4444;"></div>Racine</div>
        <div class="legend-item"><div class="legend-color" style="background:#4a9eff;"></div>Page normale</div>
        <div class="legend-item"><div class="legend-color" style="background:#44ffdd;"></div>Hub (>10 liens)</div>
        <div class="legend-item"><div class="legend-color" style="background:#ffaa44;"></div>Orpheline</div>
        <div class="legend-item"><div class="legend-color" style="background:#ff6666;"></div>Cul-de-sac</div>
        <div class="legend-item"><div class="legend-line" style="background:#4a9eff;"></div>Lien normal</div>
        <div class="legend-item"><div class="legend-line" style="background:#44ff44;"></div>Bidirectionnel</div>
    </div>
    <div id="info" class="panel"></div>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
    <script>
        const DATA = {json_data};
        let scene, camera, renderer, nodes = [], links = [], labels = [], arrows = [];
        let selectedNode = null, autoRotate = true, showArrows = true, showLabels = false;
        const ctrl = {{ drag: false, pan: false, prev: {{x:0,y:0}}, sph: {{t:0, p:Math.PI/3, r:600}}, tgt: new THREE.Vector3() }};
        const groupColors = {{}};
        const colorPalette = [0x4a9eff, 0xff6b9d, 0x44dd88, 0xffaa44, 0xaa66ff, 0x44ddff, 0xff6644, 0x88ff44, 0xff44aa, 0x44aaff];
        
        function init() {{
            scene = new THREE.Scene();
            scene.background = new THREE.Color(0x0a0a0a);
            camera = new THREE.PerspectiveCamera(60, innerWidth/innerHeight, 0.1, 10000);
            updateCam();
            renderer = new THREE.WebGLRenderer({{antialias:true}});
            renderer.setSize(innerWidth, innerHeight);
            document.getElementById('container').appendChild(renderer.domElement);
            scene.add(new THREE.AmbientLight(0xffffff, 0.5));
            const dl = new THREE.DirectionalLight(0xffffff, 0.5);
            dl.position.set(100,100,100);
            scene.add(dl);
            
            const cv = renderer.domElement;
            cv.addEventListener('contextmenu', e => e.preventDefault());
            cv.addEventListener('pointerdown', onPointerDown);
            cv.addEventListener('pointermove', onPointerMove);
            cv.addEventListener('pointerup', () => {{ ctrl.drag=false; ctrl.pan=false; }});
            cv.addEventListener('wheel', onWheel, {{passive:false}});
            addEventListener('resize', onResize);
            
            document.getElementById('jsonFile').addEventListener('change', loadFile);
            document.getElementById('autoRotate').addEventListener('change', e => autoRotate = e.target.checked);
            document.getElementById('showArrows').addEventListener('change', e => {{ showArrows = e.target.checked; arrows.forEach(a => a.visible = showArrows); }});
            document.getElementById('showLabels').addEventListener('change', e => {{ showLabels = e.target.checked; labels.forEach(l => l.visible = showLabels); }});
            
            animate();
            if(DATA) visualize(DATA);
        }}
        
        function updateCam() {{
            const {{t,p,r}} = ctrl.sph;
            camera.position.set(ctrl.tgt.x + r*Math.sin(p)*Math.cos(t), ctrl.tgt.y + r*Math.cos(p), ctrl.tgt.z + r*Math.sin(p)*Math.sin(t));
            camera.lookAt(ctrl.tgt);
        }}
        
        function onPointerDown(e) {{
            ctrl.prev = {{x:e.clientX, y:e.clientY}};
            if(e.button === 0) {{ ctrl.drag = true; checkClick(e); }}
            else if(e.button === 2) ctrl.pan = true;
        }}
        
        function onPointerMove(e) {{
            const dx = e.clientX - ctrl.prev.x, dy = e.clientY - ctrl.prev.y;
            if(ctrl.drag) {{
                ctrl.sph.t -= dx * 0.005;
                ctrl.sph.p = Math.max(0.1, Math.min(Math.PI-0.1, ctrl.sph.p - dy*0.005));
                updateCam();
            }} else if(ctrl.pan) {{
                const s = ctrl.sph.r * 0.001;
                const r = new THREE.Vector3();
                camera.getWorldDirection(r);
                r.cross(new THREE.Vector3(0,1,0)).normalize();
                ctrl.tgt.addScaledVector(r, -dx*s);
                ctrl.tgt.y += dy*s;
                updateCam();
            }}
            ctrl.prev = {{x:e.clientX, y:e.clientY}};
        }}
        
        function onWheel(e) {{
            e.preventDefault();
            ctrl.sph.r = Math.max(50, Math.min(3000, ctrl.sph.r * (e.deltaY > 0 ? 1.1 : 0.9)));
            updateCam();
        }}
        
        function onResize() {{
            camera.aspect = innerWidth/innerHeight;
            camera.updateProjectionMatrix();
            renderer.setSize(innerWidth, innerHeight);
        }}
        
        function checkClick(e) {{
            const m = new THREE.Vector2((e.clientX/innerWidth)*2-1, -(e.clientY/innerHeight)*2+1);
            const rc = new THREE.Raycaster();
            rc.setFromCamera(m, camera);
            const hit = rc.intersectObjects(nodes.map(n => n.mesh));
            if(hit.length) {{ showInfo(hit[0].object); ctrl.drag = false; }}
            else hideInfo();
        }}
        
        function loadFile(e) {{
            const f = e.target.files[0];
            if(f) {{
                const r = new FileReader();
                r.onload = ev => visualize(JSON.parse(ev.target.result));
                r.readAsText(f);
            }}
        }}
        
        function getPathGroup(url) {{
            try {{
                const path = new URL(url).pathname;
                const parts = path.split('/').filter(p => p);
                return '/' + (parts[0] || '');
            }} catch {{ return '/'; }}
        }}
        
        function visualize(data) {{
            // Nettoie
            nodes.forEach(n => scene.remove(n.mesh));
            links.forEach(l => scene.remove(l));
            labels.forEach(l => scene.remove(l));
            arrows.forEach(a => scene.remove(a));
            nodes = []; links = []; labels = []; arrows = [];
            
            // Stats
            const stats = data.statistics || {{}};
            document.getElementById('s-pages').textContent = stats.total_pages || 0;
            document.getElementById('s-links').textContent = stats.total_internal_links || 0;
            document.getElementById('s-orphans').textContent = stats.orphan_pages || 0;
            document.getElementById('s-deadends').textContent = stats.dead_end_pages || 0;
            document.getElementById('s-bidir').textContent = stats.bidirectional_links || 0;
            
            const pages = Object.values(data.pages || {{}});
            const analysis = data.analysis || {{}};
            const orphans = new Set(analysis.orphans || []);
            const deadEnds = new Set(analysis.dead_ends || []);
            const bidir = new Set((analysis.bidirectional || []).flat());
            const pathGroups = analysis.path_groups || {{}};
            
            // Couleurs par groupe
            let colorIdx = 0;
            Object.keys(pathGroups).forEach(g => {{
                groupColors[g] = colorPalette[colorIdx % colorPalette.length];
                colorIdx++;
            }});
            
            // UI groupes
            const groupsDiv = document.getElementById('groups');
            groupsDiv.innerHTML = '';
            Object.entries(pathGroups).forEach(([g, urls]) => {{
                const div = document.createElement('div');
                div.className = 'group-item';
                div.innerHTML = `<div class="group-color" style="background:#${{groupColors[g].toString(16).padStart(6,'0')}}"></div><span class="group-name">${{g}}</span><span class="group-count">${{urls.length}}</span>`;
                groupsDiv.appendChild(div);
            }});
            
            // Positions avec force-directed simplifié par groupe
            const pos = new Map();
            const root = data.root;
            pos.set(root, new THREE.Vector3(0, 0, 0));
            
            let groupIdx = 0;
            const groupCount = Object.keys(pathGroups).length || 1;
            Object.entries(pathGroups).forEach(([group, urls]) => {{
                const angle = (groupIdx / groupCount) * Math.PI * 2;
                const groupRadius = 150 + urls.length * 5;
                const groupCenter = new THREE.Vector3(Math.cos(angle) * groupRadius, 0, Math.sin(angle) * groupRadius);
                
                urls.forEach((url, i) => {{
                    if(url === root) return;
                    const localAngle = (i / urls.length) * Math.PI * 2;
                    const localRadius = 20 + Math.sqrt(urls.length) * 15;
                    const y = (Math.random() - 0.5) * 50;
                    pos.set(url, new THREE.Vector3(
                        groupCenter.x + Math.cos(localAngle) * localRadius,
                        y,
                        groupCenter.z + Math.sin(localAngle) * localRadius
                    ));
                }});
                groupIdx++;
            }});
            
            // Nœuds
            const geo = new THREE.SphereGeometry(1, 16, 16);
            pages.forEach(p => {{
                const url = p.url;
                const position = pos.get(url) || new THREE.Vector3((Math.random()-0.5)*200, (Math.random()-0.5)*50, (Math.random()-0.5)*200);
                const isRoot = url === root;
                const isOrphan = orphans.has(url);
                const isDeadEnd = deadEnds.has(url);
                const totalLinks = (p.incoming_count || 0) + (p.outgoing_count || 0);
                const isHub = totalLinks > 10;
                
                let color;
                if(isRoot) color = 0xff4444;
                else if(isOrphan) color = 0xffaa44;
                else if(isDeadEnd && !isOrphan) color = 0xff6666;
                else if(isHub) color = 0x44ffdd;
                else color = groupColors[getPathGroup(url)] || 0x4a9eff;
                
                const size = isRoot ? 8 : (3 + Math.min(totalLinks * 0.3, 7));
                const mat = new THREE.MeshPhongMaterial({{color, emissive: color, emissiveIntensity: 0.2}});
                const mesh = new THREE.Mesh(geo, mat);
                mesh.scale.setScalar(size);
                mesh.position.copy(position);
                mesh.userData = {{
                    url, title: p.title, 
                    incoming: p.incoming_count || 0, 
                    outgoing: p.outgoing_count || 0,
                    isOrphan, isDeadEnd, isHub
                }};
                scene.add(mesh);
                nodes.push({{mesh, url}});
            }});
            
            // Liens avec flèches
            const bidirSet = new Set((analysis.bidirectional || []).map(p => p.sort().join('|')));
            pages.forEach(p => {{
                const from = pos.get(p.url);
                if(!from) return;
                (p.internal_links || []).forEach(targetUrl => {{
                    const to = pos.get(targetUrl);
                    if(!to) return;
                    
                    const isBidir = bidirSet.has([p.url, targetUrl].sort().join('|'));
                    const color = isBidir ? 0x44ff44 : 0x4a88cc;
                    const opacity = isBidir ? 0.6 : 0.3;
                    
                    // Ligne
                    const lmat = new THREE.LineBasicMaterial({{color, transparent: true, opacity}});
                    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([from, to]), lmat);
                    scene.add(line);
                    links.push(line);
                    
                    // Flèche
                    const dir = new THREE.Vector3().subVectors(to, from).normalize();
                    const mid = new THREE.Vector3().addVectors(from, to).multiplyScalar(0.5);
                    const arrowGeo = new THREE.ConeGeometry(1.5, 4, 6);
                    const arrowMat = new THREE.MeshBasicMaterial({{color, transparent: true, opacity: opacity + 0.2}});
                    const arrow = new THREE.Mesh(arrowGeo, arrowMat);
                    arrow.position.copy(mid);
                    arrow.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0), dir);
                    arrow.visible = showArrows;
                    scene.add(arrow);
                    arrows.push(arrow);
                }});
            }});
            
            ctrl.sph.r = Math.max(300, 100 + pages.length * 3);
            updateCam();
        }}
        
        function showInfo(n) {{
            const i = document.getElementById('info');
            const d = n.userData;
            let tags = '';
            if(d.isOrphan) tags += '<span class="tag orphan">Orpheline</span>';
            if(d.isDeadEnd) tags += '<span class="tag deadend">Cul-de-sac</span>';
            if(d.isHub) tags += '<span class="tag hub">Hub</span>';
            i.innerHTML = `
                <h3>${{d.title || 'Sans titre'}}</h3>
                <p><a href="${{d.url}}" target="_blank">${{d.url}}</a></p>
                <p>↘ Liens sortants: ${{d.outgoing}} | ↙ Liens entrants: ${{d.incoming}}</p>
                ${{tags}}
            `;
            i.style.display = 'block';
            if(selectedNode) selectedNode.material.emissiveIntensity = 0.2;
            n.material.emissiveIntensity = 0.7;
            selectedNode = n;
        }}
        
        function hideInfo() {{
            document.getElementById('info').style.display = 'none';
            if(selectedNode) {{ selectedNode.material.emissiveIntensity = 0.2; selectedNode = null; }}
        }}
        
        function animate() {{
            requestAnimationFrame(animate);
            if(autoRotate && !ctrl.drag && !ctrl.pan) {{
                ctrl.sph.t += 0.0008;
                updateCam();
            }}
            renderer.render(scene, camera);
        }}
        
        init();
    </script>
</body>
</html>'''
    output_file = os.path.join(output_dir, 'visualize.html')
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html_template.format(domain=json_data.get('domain','Site'), json_data=json.dumps(json_data, ensure_ascii=False)))
    return output_file


def main():
    import webbrowser
    
    print("\n" + "=" * 60)
    print("SITE CRAWLER - Ultra-rapide")
    print("=" * 60 + "\n")
    
    target_url = input("URL : ").strip()
    if not target_url:
        print("Erreur : URL vide.")
        return
    
    crawler = SiteCrawler(target_url, max_workers=20)
    crawler.crawl()
    
    json_file, txt_file, output_dir = crawler.save_results()
    html_file = generate_visualization_html(crawler.generate_tree_structure(), output_dir)
    
    print(f"\nFichiers dans: {output_dir}/")
    print(f"  - site_structure.json")
    print(f"  - site_structure.txt")
    print(f"  - visualize.html")
    
    webbrowser.open('file://' + os.path.abspath(html_file))
    print("\nTERMINÉ")


if __name__ == "__main__":
    main()