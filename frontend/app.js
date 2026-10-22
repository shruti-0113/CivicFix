const $ = id => document.getElementById(id);

/* ---------- Indian states ---------- */
const INDIAN_STATES = [
  "Andhra Pradesh","Arunachal Pradesh","Assam","Bihar","Chhattisgarh","Goa","Gujarat",
  "Haryana","Himachal Pradesh","Jharkhand","Karnataka","Kerala","Madhya Pradesh",
  "Maharashtra","Manipur","Meghalaya","Mizoram","Nagaland","Odisha","Punjab","Rajasthan",
  "Sikkim","Tamil Nadu","Telangana","Tripura","Uttar Pradesh","Uttarakhand","West Bengal",
  "Andaman & Nicobar Islands","Chandigarh","Dadra & Nagar Haveli and Daman & Diu","Delhi",
  "Jammu & Kashmir","Ladakh","Lakshadweep","Puducherry"
];

function populateStates(){
  for(const selId of ["stateSelect", "stateFilter"]){
    const sel = $(selId);
    INDIAN_STATES.forEach(s => {
      const opt = document.createElement("option");
      opt.value = s; opt.textContent = s;
      sel.appendChild(opt);
    });
  }
}
populateStates();

/* ---------- Map engine (Google Maps if key configured, else OpenStreetMap) ---------- */
const MAP_CENTER = [22.5726, 88.3639];
const STATUS_COLORS = { "Reported": "#f43f5e", "In Progress": "#f59e0b", "Resolved": "#10b981" };
let mapObj = null, issueMarkers = [], lastMapItems = [];
let pickMapObj = null, pickMarker = null;

let cfgPromise = null;
function getConfig(){
  if(!cfgPromise) cfgPromise = fetch("/api/config").then(r => r.json());
  return cfgPromise;
}

function loadScriptOnce(src){
  return new Promise((resolve, reject) => {
    const old = document.querySelector(`script[data-src="${src}"]`);
    if(old) return old.addEventListener("load", resolve), old.addEventListener("error", reject);
    const s = document.createElement("script");
    s.src = src; s.async = true; s.dataset.src = src;
    s.onload = resolve; s.onerror = () => reject(new Error("Failed to load " + src));
    document.head.appendChild(s);
  });
}

function loadCssOnce(href){
  if(document.querySelector(`link[href="${href}"]`)) return;
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = href;
  document.head.appendChild(l);
}

const withTimeout = (p, ms) => Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error("timeout")), ms))]);

function loadGoogleMaps(key){
  return withTimeout(new Promise((resolve, reject) => {
    if(window.google && google.maps) return resolve();
    window.__gmapReady = resolve;
    loadScriptOnce(`https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&callback=__gmapReady&loading=async`).catch(reject);
  }), 7000);
}

function loadLeaflet(){
  return withTimeout((async () => {
    loadCssOnce("https://unpkg.com/leaflet@1.9.4/dist/leaflet.css");
    await loadScriptOnce("https://unpkg.com/leaflet@1.9.4/dist/leaflet.js");
    if(!window.L) throw new Error("Leaflet unavailable");
  })(), 7000);
}

async function createMap(el, opts){
  try{
    const cfg = await getConfig();
    if(cfg.google_maps_key){
      await loadGoogleMaps(cfg.google_maps_key);
      const g = new google.maps.Map(el, {
        center:{lat:opts.center[0], lng:opts.center[1]},
        zoom:opts.zoom, mapTypeControl:false, streetViewControl:false, fullscreenControl:false
      });
      return { kind:"google", map:g };
    }
  }catch(e){ /* fall back to free maps */ }
  await loadLeaflet();
  const l = L.map(el, { center:opts.center, zoom:opts.zoom, scrollWheelZoom:!!opts.scrollWheelZoom });
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    { attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors', maxZoom:19 }).addTo(l);
  return { kind:"leaflet", map:l };
}

function popupHtml(x){
  return `<div class="map-popup">
    <div class="badges"><span class="badge">${escapeHtml(x.category)}</span><span class="badge ${statusClass(x.status)}">${escapeHtml(x.status)}</span></div>
    <h4>${escapeHtml(x.title)}</h4>
    <p>${escapeHtml(String(x.description).slice(0,110))}${String(x.description).length > 110 ? "..." : ""}</p>
    <p>📍 ${escapeHtml(x.location)} · ▲ ${x.upvotes ?? 0} · Priority ${x.priority}/100 · #${x.id}</p>
  </div>`;
}

function addIssueMarker(issue){
  const color = STATUS_COLORS[issue.status] || "#6575e8";
  const pos = { lat:issue.latitude, lng:issue.longitude };
  if(mapObj.kind === "google"){
    const marker = new google.maps.Marker({
      position:pos, map:mapObj.map, title:issue.title,
      icon:{ path:google.maps.SymbolPath.CIRCLE, scale:9, fillColor:color, fillOpacity:1, strokeColor:"#ffffff", strokeWeight:2 }
    });
    const info = new google.maps.InfoWindow({ content:popupHtml(issue) });
    marker.addListener("click", () => info.open({ anchor:marker, map:mapObj.map }));
    return marker;
  }
  const marker = L.circleMarker([issue.latitude, issue.longitude],
    { radius:9, color:"#ffffff", weight:2, fillColor:color, fillOpacity:1 }).addTo(mapObj.map);
  marker.bindPopup(popupHtml(issue));
  return marker;
}

function clearIssueMarkers(){
  issueMarkers.forEach(m => mapObj.kind === "google" ? m.setMap(null) : mapObj.map.removeLayer(m));
  issueMarkers = [];
}

function renderMapMarkers(items){
  lastMapItems = items;
  if(!mapObj) return;
  clearIssueMarkers();
  lastMapItems.filter(i => i.latitude != null && i.longitude != null)
              .forEach(i => issueMarkers.push(addIssueMarker(i)));
}

async function initMainMap(){
  mapObj = await createMap($("map"), { center:MAP_CENTER, zoom:11, scrollWheelZoom:true });
  renderMapMarkers(lastMapItems);
}

/* ---------- Map helpers (work on both Google Maps and Leaflet) ---------- */
function mapCenter(){
  if(!mapObj) return MAP_CENTER;
  const c = mapObj.kind === "google" ? mapObj.map.getCenter() : mapObj.map.getCenter();
  return mapObj.kind === "google" ? [c.lat(), c.lng()] : [c.lat, c.lng];
}

function mapPanTo(lat, lng, zoom){
  if(!mapObj) return;
  if(mapObj.kind === "google"){
    mapObj.map.setCenter({lat, lng});
    if(zoom) mapObj.map.setZoom(zoom);
  }else{
    mapObj.map.setView([lat, lng], zoom || Math.max(mapObj.map.getZoom(), 13));
  }
}

function mapFitPoints(points){
  const pts = (points || []).filter(p => p && p.lat != null && p.lng != null);
  if(!mapObj || !pts.length) return;
  if(mapObj.kind === "google"){
    const bounds = new google.maps.LatLngBounds();
    pts.forEach(p => bounds.extend({lat:p.lat, lng:p.lng}));
    mapObj.map.fitBounds(bounds, 60);
  }else{
    mapObj.map.fitBounds(pts.map(p => [p.lat, p.lng]), { padding:[40, 40], maxZoom:15 });
  }
}

let searchMarker = null;
function clearSearchMarker(){
  if(!searchMarker) return;
  if(mapObj && mapObj.kind === "google") searchMarker.setMap(null);
  else if(mapObj) mapObj.map.removeLayer(searchMarker);
  searchMarker = null;
}

function addSearchMarker(lat, lng, label){
  if(!mapObj) return;
  clearSearchMarker();
  if(mapObj.kind === "google"){
    searchMarker = new google.maps.Marker({
      position:{lat, lng}, map:mapObj.map, title:label || "Search result",
      icon:{ path:google.maps.SymbolPath.CIRCLE, scale:11, fillColor:"#6575e8", fillOpacity:1, strokeColor:"#ffffff", strokeWeight:2 }
    });
  }else{
    searchMarker = L.circleMarker([lat, lng],
      { radius:11, color:"#ffffff", weight:2, fillColor:"#6575e8", fillOpacity:1 }).addTo(mapObj.map);
    searchMarker.bindPopup(escapeHtml(label || "Search result"));
  }
}

/* ---------- Search the map by area name ---------- */
async function searchMapArea(){
  const query = $("mapSearch").value.trim();
  if(!query){ toast("Type an area or landmark to search", "error"); return; }
  $("mapSearch").disabled = true;
  try{
    const result = await fetch(`/api/maps/geocode?q=${encodeURIComponent(query)}`).then(r => r.json());
    if(result.error || result.lat == null){ toast(result.error || "Location not found", "error"); return; }
    mapPanTo(result.lat, result.lng, 14);
    addSearchMarker(result.lat, result.lng, result.address);
    toast(`Showing ${result.address.split(",").slice(0, 2).join(",")}`, "success");
  }catch(e){
    toast("Map search failed — check your connection", "error");
  }finally{
    $("mapSearch").disabled = false;
  }
}

function fitMapToResults(){
  const pts = lastMapItems.filter(i => i.latitude != null && i.longitude != null)
                          .map(i => ({ lat:i.latitude, lng:i.longitude }));
  if(!pts.length){ toast("No pinned issues to fit — pin them while reporting", "error"); return; }
  mapFitPoints(pts);
}

/* ---------- Pin picker in report form ---------- */
async function initPickMap(){
  if($("pickMap").dataset.ready === "1") return;
  $("pickMap").dataset.ready = "1";
  pickMapObj = await createMap($("pickMap"), { center:MAP_CENTER, zoom:12 });

  if(pickMapObj.kind === "google"){
    google.maps.event.addListener(pickMapObj.map, "click", e => placePin(e.latLng.lat(), e.latLng.lng()));
  }else{
    pickMapObj.map.on("click", e => placePin(e.latlng.lat, e.latlng.lng));
  }

  if(pendingPin){ const [a,b] = pendingPin; pendingPin = null; placePin(a,b); }
}

let pendingPin = null;

function placePin(lat, lng){
  if(!pickMapObj){ pendingPin = [lat, lng]; return; }
  if(pickMarker) pickMarker.setMap ? pickMarker.setMap(null) : pickMapObj.map.removeLayer(pickMarker);
  if(pickMapObj.kind === "google"){
    pickMarker = new google.maps.Marker({ position:{lat,lng}, map:pickMapObj.map });
    pickMapObj.map.panTo({lat, lng});
    if(pickMapObj.map.getZoom() < 14) pickMapObj.map.setZoom(15);
  }else{
    pickMarker = L.marker([lat,lng]).addTo(pickMapObj.map);
    pickMapObj.map.setView([lat,lng], Math.max(pickMapObj.map.getZoom(), 15));
  }
  $("latitude").value = lat; $("longitude").value = lng;
  $("pickStatus").textContent = `Pinned at ${Number(lat).toFixed(5)}, ${Number(lng).toFixed(5)} — tap again to change.`;
}

/* ---------- Live GPS location ---------- */
function useLiveLocation(){
  if(!navigator.geolocation){ toast("Geolocation is not supported on this device", "error"); return; }
  $("locStatus").textContent = "Locating you...";
  navigator.geolocation.getCurrentPosition(async pos => {
    await initPickMap();
    placePin(+pos.coords.latitude.toFixed(6), +pos.coords.longitude.toFixed(6));
    $("locStatus").textContent = "Current location captured.";
    toast("Location captured from GPS", "success");
  }, err => {
    toast("Could not get location: " + err.message, "error");
    $("locStatus").textContent = "or tap the map below to pin manually";
  }, { enableHighAccuracy:true, timeout:10000, maximumAge:0 });
}

function resetPick(){
  if(pickMarker){ pickMarker.setMap ? pickMarker.setMap(null) : pickMapObj.map.removeLayer(pickMarker); pickMarker = null; }
  $("latitude").value = ""; $("longitude").value = "";
  $("pickStatus").textContent = "Tap the map to drop a pin where the problem is.";
  $("locStatus").textContent = "or tap the map below to pin manually";
}

/* ---------- Theme ---------- */
function applyTheme(theme){
  document.documentElement.dataset.theme = theme;
  $("themeToggle").textContent = theme === "dark" ? "☀️" : "🌙";
  localStorage.setItem("civicfix-theme", theme);
}

function toggleTheme(){
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(next);
}

applyTheme(localStorage.getItem("civicfix-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));

/* ---------- Toasts ---------- */
function toast(message, type = ""){
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = message;
  $("toasts").appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; }, 2600);
  setTimeout(() => el.remove(), 3000);
}

/* ---------- Modal ---------- */
function openModal(){ $("modal").classList.add("open"); initPickMap(); }
function closeModal(){ $("modal").classList.remove("open"); }

document.querySelectorAll(".modal").forEach(m =>
  m.addEventListener("click", e => { if(e.target === m) m.classList.remove("open"); }));
addEventListener("keydown", e => {
  if(e.key === "Escape") document.querySelectorAll(".modal.open").forEach(m => m.classList.remove("open"));
});

/* ---------- Backend-down banner ---------- */
function showBackendBanner(){
  if(document.getElementById("backendBanner")) return;
  const b = document.createElement("div");
  b.id = "backendBanner";
  b.innerHTML = `<div><strong>Server not running.</strong> You opened the HTML file directly, so live data can't load.
    Run <code>python app.py</code> in the project folder, then open <a href="http://127.0.0.1:5000">http://127.0.0.1:5000</a></div>`;
  document.body.prepend(b);
}

async function loadStats(){
  let s;
  try{
    s = await fetch("/api/stats").then(r=>r.json());
  }catch(e){ showBackendBanner(); return; }
  $("stats").innerHTML = `
    <div class="stat"><span class="muted">Total reports</span><div class="number">${s.total}</div></div>
    <div class="stat"><span class="muted">Reported</span><div class="number">${s.reported}</div></div>
    <div class="stat"><span class="muted">In progress</span><div class="number">${s.in_progress}</div></div>
    <div class="stat"><span class="muted">Resolved</span><div class="number">${s.resolved}</div></div>
    <div class="stat"><span class="muted">Resolution rate</span><div class="number">${s.resolution_rate}%</div></div>`;
}

/* ---------- Analytics ---------- */
async function loadAnalytics(){
  const a = await fetch("/api/analytics").then(r=>r.json());

  const maxCat = Math.max(1, ...a.categories.map(c => c.count));
  $("categoryChart").innerHTML = a.categories.map(c => `
    <div class="bar-row">
      <span>${escapeHtml(c.category)}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${(c.count / maxCat) * 100}%"></div></div>
      <span class="count">${c.count}</span>
    </div>`).join("") || `<p class="muted">No data yet.</p>`;

  const d = a.priority_distribution;
  $("priorityChart").innerHTML = `
    <div class="prio-box prio-critical"><div class="n">${d.critical || 0}</div><div class="l">CRITICAL</div></div>
    <div class="prio-box prio-high"><div class="n">${d.high || 0}</div><div class="l">HIGH</div></div>
    <div class="prio-box prio-medium"><div class="n">${d.medium || 0}</div><div class="l">MEDIUM</div></div>
    <div class="prio-box prio-low"><div class="n">${d.low || 0}</div><div class="l">LOW</div></div>`;

  const maxTrend = Math.max(1, ...a.weekly_trend.map(t => t.count));
  $("trendChart").innerHTML = a.weekly_trend.map(t => `
    <div class="trend-col">
      <span class="v">${t.count}</span>
      <div class="trend-bar" style="height:${Math.max((t.count / maxTrend) * 100, 6)}%"></div>
      <small>${t.label}</small>
    </div>`).join("");
}

/* ---------- Helpers ---------- */
function statusClass(status){
  return status === "Resolved" ? "green" : status === "In Progress" ? "orange" : "";
}

function priorityLevel(p){
  if(p >= 80) return { label: "Critical", cls: "red" };
  if(p >= 60) return { label: "High", cls: "orange" };
  if(p >= 40) return { label: "Medium", cls: "" };
  return { label: "Low", cls: "gray" };
}

function meterClass(p){
  if(p >= 80) return "crit";
  if(p >= 60) return "high";
  if(p < 40) return "low";
  return "";
}

function timeAgo(iso){
  const seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if(seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if(minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if(hours < 24) return `${hours} hr${hours !== 1 ? "s" : ""} ago`;
  const days = Math.floor(hours / 24);
  if(days < 30) return `${days} day${days !== 1 ? "s" : ""} ago`;
  const months = Math.floor(days / 30);
  return `${months} month${months !== 1 ? "s" : ""} ago`;
}

function escapeHtml(value){
  return String(value).replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"
  }[c]));
}

/* ---------- Issues ---------- */
function renderIssues(items){
  $("resultCount").textContent = `${items.length} issue${items.length !== 1 ? "s" : ""}`;
  if(!items.length){
    $("issues").innerHTML = `<article class="issue" style="grid-column:1/-1;text-align:center">
      <h3>No issues found</h3><p>Try another filter or report the first issue.</p></article>`;
    return;
  }
  $("issues").innerHTML = items.map(x => {
    const lvl = priorityLevel(x.priority);
    const voted = localStorage.getItem(`voted-${x.id}`) === "1";
    return `
    <article class="issue">
      <div class="issue-top">
        <div class="badges">
          <span class="badge">${escapeHtml(x.category)}</span>
          <span class="badge ${statusClass(x.status)}">${escapeHtml(x.status)}</span>
          <span class="badge ${lvl.cls}">${lvl.label}</span>
        </div>
        <button class="upvote-btn ${voted ? "voted" : ""}" onclick="upvote(${x.id})" title="Upvote this issue">▲ ${x.upvotes ?? 0}</button>
      </div>
      <h3>${escapeHtml(x.title)}</h3>
      <p>${escapeHtml(x.description)}</p>
      ${x.image_url ? `<img src="${escapeHtml(x.image_url)}" alt="Issue photo" loading="lazy" onerror="this.remove()">` : ""}
      <p>📍 ${escapeHtml(x.location)}${x.state ? " · " + escapeHtml(x.state) : ""} · 🕐 ${timeAgo(x.created_at)}</p>
      <div class="priority-line"><span class="muted">Priority score</span><span>${x.priority}/100 · ${lvl.label}</span></div>
      <div class="meter"><span class="${meterClass(x.priority)}" style="width:${x.priority}%"></span></div>
      <div class="issue-footer">
        <small class="muted">#${x.id}</small>
        <div class="card-actions">
          <button class="link-btn" onclick="openComments(${x.id}, '${escapeHtml(x.title).replace(/'/g, "&#39;")}')">💬 Comments</button>
          <button class="link-btn" onclick="openSolution(${x.id})">🤖 Solve</button>
          <select onchange="changeStatus(${x.id}, this.value)">
            ${["Reported","In Progress","Resolved"].map(s => `<option ${s===x.status?"selected":""}>${s}</option>`).join("")}
          </select>
          <button class="link-btn danger" onclick="deleteIssue(${x.id})">✕</button>
        </div>
      </div>
    </article>`;
  }).join("");
}

async function loadIssues(){
  const params = new URLSearchParams({
    q: $("search").value,
    category: $("category").value,
    status: $("status").value,
    state: $("stateFilter").value,
    sort: $("sort").value
  });
  let items;
  try{
    items = await fetch("/api/issues?" + params).then(r=>r.json());
  }catch(e){ showBackendBanner(); return; }
  renderIssues(items);
  renderMapMarkers(items);
}

/* ---------- Actions ---------- */
async function upvote(id){
  const r = await fetch(`/api/issues/${id}/upvote`, { method:"POST" });
  if(!r.ok){ toast("Could not upvote issue", "error"); return; }
  localStorage.setItem(`voted-${id}`, "1");
  toast("Upvoted! Thanks for boosting this issue ▲", "success");
  await loadIssues(); await loadStats();
}

async function changeStatus(id, status){
  const r = await fetch(`/api/issues/${id}`, {
    method:"PATCH", headers:{"Content-Type":"application/json"},
    body:JSON.stringify({status})
  });
  if(!r.ok){ toast("Could not update issue", "error"); return; }
  toast(`Status updated to "${status}"`, "success");
  await loadIssues(); await loadStats(); await loadAnalytics();
}

async function deleteIssue(id){
  if(!confirm(`Delete issue #${id}? This cannot be undone.`)) return;
  const r = await fetch(`/api/issues/${id}`, { method:"DELETE" });
  if(!r.ok){ toast("Could not delete issue", "error"); return; }
  toast(`Issue #${id} deleted`, "success");
  await loadIssues(); await loadStats(); await loadAnalytics();
}

/* ---------- Comments ---------- */
let currentIssueId = null;

async function openComments(id, title){
  currentIssueId = id;
  $("commentsTitle").textContent = title;
  $("commentsModal").classList.add("open");
  await loadComments();
}

function closeComments(){
  $("commentsModal").classList.remove("open");
  currentIssueId = null;
}

async function loadComments(){
  const comments = await fetch(`/api/issues/${currentIssueId}/comments`).then(r=>r.json());
  $("commentsList").innerHTML = comments.length ? comments.map(c => `
    <div class="comment">
      <span class="who">${escapeHtml(c.author)}</span><span class="when">${timeAgo(c.created_at)}</span>
      <p>${escapeHtml(c.message)}</p>
    </div>`).join("")
    : `<p class="muted">No comments yet. Start the discussion!</p>`;
}

$("commentForm").addEventListener("submit", async e => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target).entries());
  const r = await fetch(`/api/issues/${currentIssueId}/comments`, {
    method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(data)
  });
  const result = await r.json();
  if(!r.ok){ toast(result.error || "Could not post comment", "error"); return; }
  e.target.reset();
  toast("Comment posted 💬", "success");
  await loadComments();
});

/* ---------- Report form ---------- */
/* ---------- Photo preview ---------- */
$("imageFile").addEventListener("change", e => {
  const f = e.target.files[0];
  const pv = $("imagePreview");
  if(f){
    pv.src = URL.createObjectURL(f);
    pv.style.display = "block";
  }else{
    pv.style.display = "none";
  }
});

async function uploadPhoto(){
  const fileInput = $("imageFile");
  if(!fileInput.files.length) return "";
  const fd = new FormData();
  fd.append("file", fileInput.files[0]);
  const ur = await fetch("/api/upload", { method:"POST", body:fd });
  const result = await ur.json().catch(() => ({}));
  if(!ur.ok){ throw new Error(result.error || "Image upload failed"); }
  return result.url;
}

$("issueForm").addEventListener("submit", async e => {
  e.preventDefault();
  let imageUrl = "";
  try{
    imageUrl = await uploadPhoto();
  }catch(err){
    toast(err.message || "Image upload failed", "error");
    return;
  }
  const data = Object.fromEntries(new FormData(e.target).entries());
  ["severity","impact","urgency"].forEach(k => data[k] = Number(data[k]));
  data.latitude = data.latitude ? Number(data.latitude) : null;
  data.longitude = data.longitude ? Number(data.longitude) : null;
  data.image_url = imageUrl;
  const r = await fetch("/api/issues", {
    method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(data)
  });
  const result = await r.json();
  if(!r.ok){ toast(result.error || "Something went wrong", "error"); return; }
  e.target.reset(); resetPick();
  $("imagePreview").style.display = "none";
  closeModal();
  toast(`Issue #${result.id} reported! Priority score: ${result.priority}/100`, "success");
  await loadIssues(); await loadStats(); await loadAnalytics();
});

/* ---------- Contact form ---------- */
$("contactForm").addEventListener("submit", async e => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target).entries());
  const r = await fetch("/api/contact", {
    method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(data)
  });
  const result = await r.json();
  if(!r.ok){ toast(result.error || "Could not send message", "error"); return; }
  e.target.reset();
  toast("Message sent — thanks for reaching out!", "success");
});

/* ---------- Community fund ---------- */
const inr = n => "₹" + Number(n).toLocaleString("en-IN");

async function loadFunds(){
  const f = await fetch("/api/funds").then(r=>r.json());
  $("fundsTiles").innerHTML = `
    <div class="stat"><span class="muted">Total raised</span><div class="number" style="color:#0ea371">${inr(f.total_raised)}</div></div>
    <div class="stat"><span class="muted">Spent on fixes</span><div class="number" style="color:#f43f5e">${inr(f.total_spent)}</div></div>
    <div class="stat"><span class="muted">Available balance</span><div class="number">${inr(f.balance)}</div></div>
    <div class="stat"><span class="muted">Contributors</span><div class="number">${f.donation_count}</div></div>`;

  $("donationList").innerHTML = f.recent_donations.length ? f.recent_donations.map(d => `
    <div class="ledger-item">
      <div>
        <span class="who">${escapeHtml(d.donor_name)}</span>${d.issue_id ? ` <span class="muted">→ #${d.issue_id}</span>` : ""}
        ${d.message ? `<span class="meta">"${escapeHtml(d.message)}"</span>` : ""}
        <span class="meta">${timeAgo(d.created_at)} · ${escapeHtml(d.method)}</span>
      </div>
      <div style="text-align:right">
        <div class="amt-pos">${inr(d.amount)}</div>
        <button class="link-btn danger" onclick="deleteDonation(${d.id})" title="Remove entry">✕</button>
      </div>
    </div>`).join("")
    : `<p class="ledger-empty">No contributions yet — be the first!</p>`;

  $("expenseList").innerHTML = f.recent_expenses.length ? f.recent_expenses.map(x => `
    <div class="ledger-item">
      <div>
        <span class="who">${escapeHtml(x.title)}</span> <span class="badge gray">${escapeHtml(x.category)}</span>
        ${x.issue_id ? `<span class="muted">→ #${x.issue_id}</span>` : ""}
        ${x.notes ? `<span class="meta">${escapeHtml(x.notes)}</span>` : ""}
        <span class="meta">${timeAgo(x.created_at)}</span>
      </div>
      <div style="text-align:right">
        <div class="amt-neg">−${inr(x.amount)}</div>
        <button class="link-btn danger" onclick="deleteExpense(${x.id})" title="Remove entry">✕</button>
      </div>
    </div>`).join("")
    : `<p class="ledger-empty">No expenses recorded yet.</p>`;
}

async function deleteDonation(id){
  if(!confirm("Remove this contribution record?")) return;
  const r = await fetch(`/api/donations/${id}`, { method:"DELETE" });
  if(!r.ok){ toast("Could not remove", "error"); return; }
  toast("Contribution removed", "success");
  await loadFunds();
}

async function deleteExpense(id){
  if(!confirm("Remove this expense record?")) return;
  const r = await fetch(`/api/expenses/${id}`, { method:"DELETE" });
  if(!r.ok){ toast("Could not remove", "error"); return; }
  toast("Expense removed", "success");
  await loadFunds();
}

/* ---------- Donate modal ---------- */
let CFG = {};
getConfig().then(c => {
  CFG = c;
  const ai = c.ai || {};
  $("aiProvider").textContent = ai.live
    ? `${ai.label} · ${ai.model}`
    : `${ai.label} — offline mode, add an API key for live AI`;
  $("mapProvider").textContent = `Maps: ${(c.maps || {}).label || ""}`;
});

function openDonate(){
  $("donateModal").classList.add("open");
  fetch("/api/issues?status=Reported&sort=priority").then(r=>r.json()).then(items => {
    $("donateIssue").innerHTML = `<option value="">General fund</option>` +
      items.map(i => `<option value="${i.id}">#${i.id} · ${escapeHtml(i.title.slice(0,50))}</option>`).join("");
  });
  updateUpiLink();
}

function closeDonate(){ $("donateModal").classList.remove("open"); }

function setAmount(v){
  $("donateAmount").value = v;
  updateUpiLink();
}

function updateUpiLink(){
  const link = $("upiLink"), hint = $("upiHint");
  if(!CFG.upi_id){
    link.style.display = "none";
    hint.textContent = "Set your UPI ID in app.py to enable direct payments. Contributions are recorded below.";
    return;
  }
  const amt = parseFloat($("donateAmount").value);
  if(!amt || amt <= 0){
    link.style.display = "none";
    hint.textContent = "Enter an amount to get a direct UPI payment link.";
    return;
  }
  const params = new URLSearchParams({
    pa: CFG.upi_id,
    pn: "CivicFix",
    am: amt.toFixed(2),
    cu: "INR",
    tn: "CivicFix community fund"
  });
  link.href = "upi://pay?" + params.toString();
  link.style.display = "block";
  hint.textContent = "Opens GPay / PhonePe / Paytm with the amount pre-filled. After paying, click \"I've paid\" below so it appears in the public ledger.";
}

$("donateAmount").addEventListener("input", updateUpiLink);

$("donateForm").addEventListener("submit", async e => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target).entries());
  data.amount = parseFloat(data.amount);
  if(!data.issue_id) delete data.issue_id;
  const r = await fetch("/api/donations", {
    method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(data)
  });
  const result = await r.json();
  if(!r.ok){ toast(result.error || "Could not record donation", "error"); return; }
  e.target.reset(); closeDonate(); updateUpiLink();
  toast(`Thank you for contributing ${inr(result.amount)}!`, "success");
  await loadFunds();
});

/* ---------- Expense modal ---------- */
function openExpense(){
  $("expenseModal").classList.add("open");
  fetch("/api/issues?sort=priority").then(r=>r.json()).then(items => {
    $("expenseIssue").innerHTML = `<option value="">None</option>` +
      items.map(i => `<option value="${i.id}">#${i.id} · ${escapeHtml(i.title.slice(0,50))}</option>`).join("");
  });
}

function closeExpense(){ $("expenseModal").classList.remove("open"); }

$("expenseForm").addEventListener("submit", async e => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target).entries());
  data.amount = parseFloat(data.amount);
  if(!data.issue_id) delete data.issue_id;
  const r = await fetch("/api/expenses", {
    method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(data)
  });
  const result = await r.json();
  if(!r.ok){ toast(result.error || "Could not save expense", "error"); return; }
  e.target.reset(); closeExpense();
  toast("Expense recorded in the public ledger", "success");
  await loadFunds();
});

/* ---------- Location autocomplete (Google Places / OpenStreetMap) ---------- */
let suggestTimer = null;
let suggestions = [];

const locationInput = $("locationInput");

locationInput.addEventListener("input", e => {
  const query = e.target.value.trim();
  clearTimeout(suggestTimer);
  if(query.length < 3){ $("locationSuggest").innerHTML = ""; suggestions = []; return; }
  suggestTimer = setTimeout(() => loadLocationSuggestions(query), 280);
});

locationInput.addEventListener("keydown", e => {
  if(e.key === "Enter"){ e.preventDefault(); if(suggestions.length) pickLocation(0); }
});

async function loadLocationSuggestions(query){
  try{
    const data = await fetch(`/api/maps/autocomplete?q=${encodeURIComponent(query)}`).then(r => r.json());
    suggestions = data.predictions || [];
    if(!suggestions.length){ $("locationSuggest").innerHTML = `<p class="muted suggest-empty">No matching places</p>`; return; }
    $("locationSuggest").innerHTML = suggestions.map((p, i) =>
      `<button type="button" class="suggest-item" onclick="pickLocation(${i})">
         <span class="suggest-name">${escapeHtml(p.description)}</span>
       </button>`).join("");
  }catch(e){
    $("locationSuggest").innerHTML = "";
  }
}

async function pickLocation(index){
  const place = suggestions[index];
  if(!place) return;
  $("locationInput").value = place.description;
  $("locationSuggest").innerHTML = "";
  suggestions = [];

  let lat = place.lat, lng = place.lng;
  if(lat == null && place.place_id){
    const details = await fetch(`/api/maps/place/${encodeURIComponent(place.place_id)}`)
                        .then(r => r.json()).catch(() => null);
    if(details && details.lat != null){ lat = details.lat; lng = details.lng; }
  }
  if(lat == null) return;
  await initPickMap();
  placePin(lat, lng);
  toast("Location pinned from the address lookup", "success");
}

document.addEventListener("click", e => {
  if(!e.target.closest(".suggest-box") && e.target !== locationInput) $("locationSuggest").innerHTML = "";
});

/* ---------- AI: auto-fill a report ---------- */
function formValue(name){
  const el = $("issueForm").elements[name];
  return el ? String(el.value || "").trim() : "";
}

function setBusy(button, busy, busyLabel){
  if(!button) return;
  if(busy){
    button.dataset.label = button.textContent;
    button.textContent = busyLabel || "Working…";
    button.disabled = true;
  }else{
    if(button.dataset.label) button.textContent = button.dataset.label;
    button.disabled = false;
  }
}

async function aiAutoFill(button){
  const title = formValue("title");
  const description = formValue("description");
  if(!title && !description){ toast("Write a title or description first", "error"); return; }

  setBusy(button, true, "🤖 Scoring…");
  try{
    const result = await fetch("/api/ai/triage", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({
        title, description,
        location: formValue("location"),
        state: formValue("state")
      })
    }).then(r => r.json());
    if(result.error){ toast(result.error, "error"); return; }

    const form = $("issueForm").elements;
    if(result.title) form.title.value = result.title;
    if(result.category) form.category.value = result.category;
    form.severity.value = result.severity;
    form.impact.value = result.impact;
    form.urgency.value = result.urgency;

    const dupes = result.possible_duplicates || [];
    $("aiFillNote").innerHTML = `AI scored it <strong>${result.priority}/100</strong> · ${escapeHtml(result.category)}
      · confidence ${Math.round((result.confidence || 0) * 100)}%${dupes.length ? ` · ⚠ similar to #${dupes[0].id}` : ""}`;
    toast(`AI scored this ${result.priority}/100 as ${result.category}`, "success");
  }catch(e){
    toast("AI scoring failed — check your connection", "error");
  }finally{
    setBusy(button, false);
  }
}

async function improveDescription(button){
  const description = formValue("description");
  if(!description){ toast("Write a rough description first", "error"); return; }
  setBusy(button, true, "✨ Rewriting…");
  try{
    const result = await fetch("/api/ai/describe", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({ description, category: formValue("category") })
    }).then(r => r.json());
    if(result.error){ toast(result.error, "error"); return; }
    $("issueForm").elements.description.value = result.description;
    toast("Description rewritten — edit it if you want", "success");
  }catch(e){
    toast("AI rewrite failed — check your connection", "error");
  }finally{
    setBusy(button, false);
  }
}

/* ---------- AI: resolution plan for one issue ---------- */
async function openSolution(id){
  $("solutionTitle").textContent = "Building the plan…";
  $("solutionBody").innerHTML = `<p class="muted">Working out the best fix — root cause, materials, cost and steps…</p>`;
  $("solutionModal").classList.add("open");
  try{
    const plan = await fetch("/api/ai/resolve", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({ issue_id:id })
    }).then(r => r.json());
    if(plan.error){ $("solutionBody").innerHTML = `<p class="muted">${escapeHtml(plan.error)}</p>`; return; }
    renderSolution(plan);
  }catch(e){
    $("solutionBody").innerHTML = `<p class="muted">Could not build the plan — check your connection.</p>`;
  }
}

function closeSolution(){ $("solutionModal").classList.remove("open"); }

function renderSolution(plan){
  $("solutionTitle").textContent = plan.summary ? "Resolution plan" : "Resolution plan";
  const sourceBadge = plan.source === "llm"
    ? `<span class="badge">AI · ${escapeHtml(plan.model || "model")}</span>`
    : `<span class="badge gray">Built-in rule engine</span>`;

  const facts = [
    ["Estimated cost", plan.estimated_cost],
    ["Timeline", plan.timeline],
    ["Owning department", plan.department],
  ].filter(([, value]) => value);

  $("solutionBody").innerHTML = `
    <div class="badges">${sourceBadge}<span class="badge">Issue #${escapeHtml(String(plan.issue_id))}</span></div>
    ${plan.summary ? `<p class="solution-summary">${escapeHtml(plan.summary)}</p>` : ""}
    ${plan.root_cause ? `<div class="solution-block"><h4>Probable root cause</h4><p>${escapeHtml(plan.root_cause)}</p></div>` : ""}
    ${plan.steps && plan.steps.length ? `<div class="solution-block"><h4>Step by step</h4>
      <ol class="solution-steps">${plan.steps.map(s => `<li>${escapeHtml(s)}</li>`).join("")}</ol></div>` : ""}
    ${plan.materials && plan.materials.length ? `<div class="solution-block"><h4>Materials &amp; gear</h4>
      <ul class="solution-materials">${plan.materials.map(m => `<li>${escapeHtml(m)}</li>`).join("")}</ul></div>` : ""}
    ${facts.length ? `<div class="solution-facts">${facts.map(([label, value]) =>
        `<div><span class="muted">${label}</span><p>${escapeHtml(value)}</p></div>`).join("")}</div>` : ""}
    ${plan.prevention ? `<div class="solution-block"><h4>Prevent it recurring</h4><p>${escapeHtml(plan.prevention)}</p></div>` : ""}
    ${plan.escalation ? `<div class="solution-block"><h4>If nothing happens</h4><p>${escapeHtml(plan.escalation)}</p></div>` : ""}
    <p class="muted solution-note">Saved to the issue so the crew can follow it later.</p>`;
}

/* ---------- AI: nearby civic services ---------- */
const NEARBY_KINDS = [
  ["hospital", "🏥 Hospitals"],
  ["police", "🚓 Police"],
  ["fire", "🚒 Fire"],
  ["municipal", "🏛 Municipal"],
  ["bus_stop", "🚌 Bus stops"],
  ["parking", "🅿️ Parking"],
];
let nearbyKind = "hospital";
let nearbyPoint = null;

function renderNearbyKinds(){
  $("nearbyKinds").innerHTML = NEARBY_KINDS.map(([kind, label]) =>
    `<button type="button" class="chip ${kind === nearbyKind ? "active" : ""}" onclick="setNearbyKind('${kind}')">${label}</button>`
  ).join("");
}

function setNearbyKind(kind){
  nearbyKind = kind;
  renderNearbyKinds();
  loadNearby();
}

async function openNearby(){
  $("nearbyModal").classList.add("open");
  renderNearbyKinds();
  const center = mapCenter();
  nearbyPoint = { lat:center[0], lng:center[1] };
  $("nearbyBody").innerHTML = `<p class="muted">Finding services around you…</p>`;
  loadNearby();
  // Refine with the visitor's real position once the browser shares it.
  if(navigator.geolocation){
    navigator.geolocation.getCurrentPosition(pos => {
      nearbyPoint = { lat:pos.coords.latitude, lng:pos.coords.longitude };
      loadNearby();
    }, () => {}, { enableHighAccuracy:true, timeout:6000, maximumAge:60000 });
  }
}

function closeNearby(){ $("nearbyModal").classList.remove("open"); }

async function loadNearby(){
  if(!nearbyPoint) return;
  $("nearbyBody").innerHTML = `<p class="muted">Searching…</p>`;
  try{
    const data = await fetch(
      `/api/maps/nearby?lat=${nearbyPoint.lat}&lng=${nearbyPoint.lng}&kind=${nearbyKind}`
    ).then(r => r.json());
    if(!data.places || !data.places.length){
      $("nearbyBody").innerHTML = `<p class="muted">No ${escapeHtml(data.label || "places")} found within 3 km of this point.</p>`;
      return;
    }
    $("nearbyBody").innerHTML = data.places.map(p => `
      <div class="nearby-item">
        <div>
          <strong>${escapeHtml(p.name)}</strong>
          <span class="muted">${escapeHtml(p.address)}</span>
        </div>
        <div style="text-align:right">
          <span class="badge">${p.distance_km} km</span>
          <a class="link-btn" href="${escapeHtml(p.url)}" target="_blank" rel="noopener">Open map →</a>
        </div>
      </div>`).join("");
  }catch(e){
    $("nearbyBody").innerHTML = `<p class="muted">Could not load nearby services.</p>`;
  }
}

/* ---------- AI: city insights ---------- */
async function loadAiInsights(){
  $("aiInsights").innerHTML = `<p class="muted">Analysing the live data…</p>`;
  try{
    const data = await fetch("/api/ai/insights").then(r => r.json());
    const badge = data.source === "llm"
      ? `<span class="badge">AI · ${escapeHtml(data.model || "model")}</span>`
      : `<span class="badge gray">Built-in rule engine</span>`;
    $("aiInsights").innerHTML = `
      <div class="badges">${badge}</div>
      <h4 class="insight-headline">${escapeHtml(data.headline || "")}</h4>
      <div class="insight-grid">
        ${(data.insights || []).map(i => `
          <div class="insight-card">
            <strong>${escapeHtml(i.title)}</strong>
            <p>${escapeHtml(i.detail)}</p>
            <small>→ ${escapeHtml(i.action)}</small>
          </div>`).join("")}
      </div>
      ${data.focus_areas && data.focus_areas.length ? `<p class="muted">Focus areas: ${data.focus_areas.map(escapeHtml).join(" · ")}</p>` : ""}`;
  }catch(e){
    $("aiInsights").innerHTML = `<p class="muted">Could not build insights — check your connection.</p>`;
  }
}

/* ---------- AI assistant chat ---------- */
let aiHistory = [];
let aiGreeted = false;

function toggleAssistant(){
  const panel = $("aiPanel");
  const open = panel.classList.toggle("open");
  $("aiFab").classList.toggle("hidden", open);
  if(open && !aiGreeted){ aiGreeted = true; greetAssistant(); }
  if(open) setTimeout(() => $("aiInput").focus(), 120);
}

function aiBubble(text, who){
  const el = document.createElement("div");
  el.className = `ai-msg ${who}`;
  el.innerHTML = escapeHtml(text).replace(/\n/g, "<br>");
  $("aiMessages").appendChild(el);
  $("aiMessages").scrollTop = $("aiMessages").scrollHeight;
  return el;
}

function aiTyping(){
  const el = document.createElement("div");
  el.className = "ai-msg bot typing";
  el.innerHTML = `<span class="dot"></span><span class="dot"></span><span class="dot"></span>`;
  $("aiMessages").appendChild(el);
  $("aiMessages").scrollTop = $("aiMessages").scrollHeight;
  return el;
}

function setAiChips(items){
  $("aiChips").innerHTML = (items || []).map(text =>
    `<button type="button" class="ai-chip" onclick="sendAiMessage(${JSON.stringify(text).replace(/"/g, "&quot;")})">${escapeHtml(text)}</button>`
  ).join("");
}

function greetAssistant(){
  const ai = (CFG && CFG.ai) || {};
  aiBubble(`Hi, I am your CivicFix assistant (${ai.label || "rule engine"}). I can rank your issues, explain how to fix a problem step by step, and summarise the platform.`, "bot");
  setAiChips(["What should we fix first?", "How do I fix a pothole?", "Show me the stats", "What is the fund balance?"]);
}

async function sendAiMessage(text){
  const message = String(text || "").trim();
  if(!message) return;
  $("aiInput").value = "";
  aiBubble(message, "user");
  aiHistory.push({ role:"user", content:message });

  const typing = aiTyping();
  try{
    const result = await fetch("/api/ai/assistant", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({ message, history: aiHistory.slice(-6) })
    }).then(r => r.json());
    typing.remove();
    if(result.error){ aiBubble(result.error, "bot"); return; }
    aiHistory.push({ role:"assistant", content:result.answer });
    aiBubble(result.answer, "bot");
    setAiChips(result.suggestions);
  }catch(e){
    typing.remove();
    aiBubble("I could not reach the server. Please try again.", "bot");
  }
}

$("aiForm").addEventListener("submit", e => {
  e.preventDefault();
  sendAiMessage($("aiInput").value);
});

/* ---------- PWA install ---------- */
let deferredPrompt = null;

addEventListener("beforeinstallprompt", e => {
  e.preventDefault();
  deferredPrompt = e;
  $("installBtn").style.display = "";
});

addEventListener("appinstalled", () => {
  deferredPrompt = null;
  $("installBtn").style.display = "none";
  toast("CivicFix installed — find it on your home screen!", "success");
});

function installApp(){
  if(!deferredPrompt){ toast("Use your browser menu → Install / Add to Home Screen"); return; }
  deferredPrompt.prompt();
}

/* ---------- Service worker ---------- */
if("serviceWorker" in navigator){
  addEventListener("load", () => navigator.serviceWorker.register("/sw.js"));
}

loadStats();
loadAnalytics();
loadFunds();
loadAiInsights();
initMainMap();
loadIssues();
