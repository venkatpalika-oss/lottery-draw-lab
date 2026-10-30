const C='drawlab-v10',F=['./','index.html','manifest.json','icon-v2.svg','icon-192-v2.png','icon-512-v2.png','privacy.html','fonts/bricolage-grotesque-var.woff2','fonts/plex-sans-400.woff2','fonts/plex-sans-500.woff2','fonts/plex-sans-600.woff2','fonts/plex-mono-500.woff2','fonts/plex-mono-600.woff2'];
// Install: fetch every file from the network, bypassing the browser's HTTP cache (GitHub Pages lets
// browsers keep pages for up to 10 minutes, so a plain fetch right after a deploy can return the old page).
// addAll is all-or-nothing; if any file fails, the half-built cache is deleted and the install fails,
// so the previous worker and its complete cache stay in charge.
self.addEventListener('install',e=>e.waitUntil(caches.open(C)
  .then(c=>c.addAll(F.map(u=>new Request(u,{cache:'reload'}))))
  .catch(err=>caches.delete(C).then(()=>{throw err}))
  .then(()=>self.skipWaiting())));
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(k=>Promise.all(k.filter(x=>x!==C).map(x=>caches.delete(x)))).then(()=>self.clients.claim())));
// Network first. Pages are revalidated with the server (cache 'no-cache') so a stale copy from the
// browser's HTTP cache is never shown or stored; only successful same-origin responses are stored.
const isPage=r=>r.mode==='navigate'||/\.html$|\/$/.test(new URL(r.url).pathname);
self.addEventListener('fetch',e=>{const q=e.request;if(q.method!=='GET')return;
  const net=isPage(q)?fetch(new Request(q,{cache:'no-cache'})):fetch(q);
  e.respondWith(net.then(r=>{if(r.ok&&r.type==='basic'){const cp=r.clone();caches.open(C).then(c=>c.put(q,cp))}return r})
    .catch(()=>caches.match(q).then(m=>m||(isPage(q)?caches.match('./'):undefined))))});
